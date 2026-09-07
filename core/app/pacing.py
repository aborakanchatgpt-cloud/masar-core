"""
Masar Core — تباعد الإرسال والنافذة الزمنية ووتيرة الإحماء (B4، الدليل:
"نافذة الإرسال الأحد-الخميس 08:00-16:30 بتوقيت الرياض"، "تباعد ≥8 دقائق
بين كل رسالتين لنفس العميل"، "وتيرة إحماء تدريجية للصناديق الجديدة").

وحدة **نقية بالكامل** (بلا اتصال قاعدة بيانات ولا شبكة) — قابلة للاختبار
مباشرة (core/tests/test_pacing.py)، تُستدعى من send_builder.py وscheduler_main.py.

ملاحظة توقيت حرجة (تُوثَّق أيضًا بتقرير B4-executor.md): planner.py يملك
دالة `now_riyadh()` خاصة به تُرجع datetime بـ`tzinfo=timezone.utc` لكن
بأرقام ساعة مُزاحة +3 — وهذا **آمن فقط لمقارنات بالتاريخ فقط** (planner
يستخدمها فقط لاستخراج `.date()`)، **وليس آمنًا لتخزين مباشر بعمود
timestamptz حقيقي** مثل `send_queue.send_after` (تخزين مباشر لتلك القيمة
كـtimestamptz سيُفسَّرها Postgres على أنها UTC فعليًا، أي متأخرة 3 ساعات
عن الوقت المقصود). هذا الملف يتعمّد مخطّط تحويل مختلف وصريح، يعمل بالكامل
بفضاء "وقت رياض ساذج" (naive) داخليًا، ولا يتحوّل لـUTC حقيقي إلا عند نقطة
التخزين/المقارنة النهائية عبر `riyadh_naive_to_utc()` — يمنع هذا الفخ كليًا
(مُتحقَّق منه صراحة باختبار round-trip مخصّص).
"""
from __future__ import annotations

import hashlib
import random
from datetime import date, datetime, time, timedelta, timezone

# الرياض UTC+3 ثابتة طوال السنة (لا يوجد توقيت صيفي بالسعودية) — نفس ثابت
# planner.RIYADH_OFFSET، مكرّر هنا عمدًا (وحدة مستقلة بلا استيراد من planner.py).
RIYADH_OFFSET = timedelta(hours=3)

# نافذة الإرسال العادية: الأحد-الخميس 08:00-16:30 بتوقيت الرياض.
WINDOW_START = time(8, 0)
WINDOW_END = time(16, 30)

# نافذة رمضان (أقصر — ساعات عمل مخفّضة تقليديًا) — مُعرَّفة للاستخدام
# المستقبلي (لا تفعيل تلقائي بالتاريخ الهجري بهذا الإصدار؛ يحتاج معايرة
# تقويم هجري لاحقًا)، القيم مبنية على ساعات عمل رمضان الشائعة سعوديًا.
RAMADAN_WINDOW_START = time(9, 30)
RAMADAN_WINDOW_END = time(14, 30)

# أيام الإرسال: الأحد=6، الاثنين=0، الثلاثاء=1، الأربعاء=2، الخميس=3 —
# Python weekday(): الاثنين=0 ... الأحد=6.
SEND_WEEKDAYS: set[int] = {6, 0, 1, 2, 3}

MIN_GAP_MINUTES = 8
MAX_GAP_MINUTES = 15

DEFAULT_TARGET_DAILY = 17
MAX_DAILY = 22

# وتيرة الإحماء التدريجية لكل صندوق بريد جديد (أيام منذ إنشاء mail_link):
#   يوم 1-2: 6، يوم 3-5: 12، يوم 6-9: 16، من يوم 10 فصاعدًا: MAX_DAILY (22).
# ملاحظة الفهرسة: يوم الإنشاء نفسه = "يوم 1" = age_days صفر، لذا حد كل مرحلة
# هو (رقم اليوم الأخير فيها - 1)، لا رقم اليوم نفسه — مثال: "يوم 1-2" يعني
# age_days ∈ {0, 1} أي max_age=1، لا 2.
_RAMP_TIERS: tuple[tuple[int, int], ...] = (
    (1, 6),
    (4, 12),
    (8, 16),
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_riyadh_naive(utc_dt: datetime) -> datetime:
    """يحوّل datetime بمنطقة زمنية حقيقية (عادة UTC) إلى datetime ساذج
    (بلا tzinfo) يمثّل نفس اللحظة بتوقيت الرياض الفعلي. إن وصل datetime
    ساذج أصلًا يُفترض أنه UTC فعليًا (نفس افتراض `datetime.now(timezone.utc)`)."""
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    return (utc_dt.astimezone(timezone.utc) + RIYADH_OFFSET).replace(tzinfo=None)


def riyadh_naive_to_utc(naive_riyadh: datetime) -> datetime:
    """يحوّل datetime ساذج يمثّل وقت رياض إلى datetime حقيقي بمنطقة UTC —
    نقطة التحويل الوحيدة المستخدَمة قبل أي تخزين بعمود timestamptz حقيقي
    (send_queue.send_after)."""
    if naive_riyadh.tzinfo is not None:
        raise ValueError("riyadh_naive_to_utc تتوقّع datetime ساذجًا (بلا tzinfo)")
    return (naive_riyadh - RIYADH_OFFSET).replace(tzinfo=timezone.utc)


def is_send_weekday(d: date) -> bool:
    return d.weekday() in SEND_WEEKDAYS


def is_in_window(riyadh_naive_now: datetime, *, ramadan: bool = False) -> bool:
    """هل اللحظة (وقت رياض ساذج) ضمن نافذة الإرسال (يوم مسموح + ساعة ضمن
    النافذة)؟ حدّا النافذة شاملان (>=start و<=end)."""
    if not is_send_weekday(riyadh_naive_now.date()):
        return False
    start = RAMADAN_WINDOW_START if ramadan else WINDOW_START
    end = RAMADAN_WINDOW_END if ramadan else WINDOW_END
    current = riyadh_naive_now.time()
    return start <= current <= end


def next_window_bounds(riyadh_naive_now: datetime, *, ramadan: bool = False) -> tuple[datetime, datetime]:
    """يرجع (بداية، نهاية) أقرب نافذة إرسال صالحة بدءًا من riyadh_naive_now
    (ساذج، وقت رياض) — إما نافذة اليوم نفسه (إن كان يوم إرسال ولم تنتهِ
    نافذته بعد)، أو نافذة أقرب يوم إرسال قادم بدءًا من 00:00."""
    start_t = RAMADAN_WINDOW_START if ramadan else WINDOW_START
    end_t = RAMADAN_WINDOW_END if ramadan else WINDOW_END

    d = riyadh_naive_now.date()
    for offset in range(0, 8):
        candidate_date = d + timedelta(days=offset)
        if not is_send_weekday(candidate_date):
            continue
        window_start = datetime.combine(candidate_date, start_t)
        window_end = datetime.combine(candidate_date, end_t)
        if offset == 0 and riyadh_naive_now > window_end:
            continue
        return window_start, window_end
    # لن يصل هنا فعليًا (5 من كل 7 أيام أيام إرسال) — احتياط دفاعي فقط.
    fallback_start = datetime.combine(d, start_t)
    fallback_end = datetime.combine(d, end_t)
    return fallback_start, fallback_end


def ramp_cap(mail_link_created_at_utc: datetime | None, riyadh_today: date) -> int:
    """سقف الإرسال اليومي لصندوق بريد بحسب عمره (وتيرة الإحماء). صندوق بلا
    تاريخ إنشاء معروف (None) يُعامَل كصندوق قديم مستقر (MAX_DAILY) — الحذر
    هنا يكون بعدم تقييد صندوق ناضج بالخطأ لا العكس؛ التحفّظ الفعلي يقع على
    مسار إنشاء mail_link نفسه (يبدأ دومًا بـcreated_at فعلي=الآن)."""
    if mail_link_created_at_utc is None:
        return MAX_DAILY
    created_riyadh_date = to_riyadh_naive(mail_link_created_at_utc).date()
    age_days = (riyadh_today - created_riyadh_date).days
    if age_days < 0:
        age_days = 0
    for max_age, cap in _RAMP_TIERS:
        if age_days <= max_age:
            return cap
    return MAX_DAILY


def next_slot(
    previous_slot: datetime,
    window_start: datetime,
    window_end: datetime,
    rng: random.Random,
) -> datetime | None:
    """يرجع موعد الإرسال التالي (ساذج، وقت رياض) بعد `previous_slot` بفارق
    عشوائي [MIN_GAP_MINUTES, MAX_GAP_MINUTES] دقيقة — أو None إن تجاوز
    الموعد الناتج نهاية النافذة (لا مزيد من الفتحات المتاحة اليوم)."""
    gap = rng.uniform(MIN_GAP_MINUTES, MAX_GAP_MINUTES)
    candidate = previous_slot + timedelta(minutes=gap)
    if candidate > window_end:
        return None
    if candidate < window_start:
        candidate = window_start
    return candidate


def deterministic_rng(seed_text: str) -> random.Random:
    """مولّد أرقام عشوائية حتمي مبني على sha256 (لا `hash()` المدمجة —
    عشوائية PYTHONHASHSEED عبر إعادة تشغيل العملية تجعل `hash()` غير حتمية
    بين عمليتين مختلفتين، بعكس sha256 الثابتة دومًا لنفس النص)."""
    digest = hashlib.sha256(seed_text.encode("utf-8")).digest()
    seed_int = int.from_bytes(digest[:8], "big")
    return random.Random(seed_int)
