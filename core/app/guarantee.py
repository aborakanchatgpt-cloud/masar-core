"""
Masar Core — دفتر الضمان/التعويض (B5a البند 3، الدليل §3.12، PLAN.md B5).

القاعدة الثابتة (غير قابلة للتفاوض، الدليل §0 البند 5 + التكليف): الهدف
الشهري = 510 تقديمًا **مُحتَسَبًا** (المرتد لا يُحتسب أبدًا). إن لم يبلغ
العميل هدفه عند نهاية فترة اشتراكه: تمديد يومين تلقائي أولًا؛ إن بقي عجزًا
بعد التمديد → تعويض تناسبي (سعر الاشتراك ÷ 510 لكل تقديم ناقص)، **يُسجّل
faqat** بانتظار اعتماد/تحويل يدوي من المالك (لا تحويل تلقائي أبدًا — راجع
`POST /admin/guarantee/{id}/settle`). انقطاع بسبب بريد العميل نفسه (مفصول/
فشل تحقّق) **يُمدّد الفترة بدل التعويض** — طالما الصندوق معطوب لا يصل
التقييم لمرحلة التعويض إطلاقًا مهما تكرّر التقييم اليومي.

**رصيد/باقات (لا اشتراك نشط):** `status='na'` — الضمان مبني على اشتراك
`subscriptions` حصرًا (منحة 510 + مدة محدّدة)؛ عميل رصيد مسبق (credits) لا
مفهوم "فترة" له إطلاقًا (الرصيد لا ينتهي، الدليل §0 البند 6) فلا ينطبق عليه
ضمان.

ملاحظة نطاق موثّقة (NEEDS-OWNER — راجع docs/reports/B5a-executor.md): لا
جدول يسجّل **تاريخ** حالة `mail_links` يومًا بيوم (الجدول يحمل الحالة
الحالية فقط) — فلا يمكن حساب `customer_caused_days` تاريخيًا بدقة (كما يصف
الدليل §3.12 حرفيًا: "shortfall = 510 − sent_counted − customer_caused_days×17").
هذا الملف يطبّق تفسيرًا مبسطًا ومحافظًا مكافئًا بالأثر العملي: **طالما
`mail_links.status != 'ok'` وقت التقييم اليومي، التقييم يمدّد الفترة يومًا
إضافيًا ولا يتقدم لمرحلة التعويض إطلاقًا** — يحقّق نفس نتيجة القاعدة
("انقطاع بسبب العميل يُمدّد بدل تعويض") بلا حاجة لجدول تاريخي جديد، لكنه
لا يحسب `customer_caused_days` كعدد صريح بالتقرير (`shortfall` يبقى NULL
طوال فترة الانقطاع، لا يُطرح تناسبيًا من هدف مؤجّل).

تصحيح B5c (docs/reports/B5a-B2b-review.md، القسم 3.2، عيب [major]): عدّاد
التمديد كان عمودًا واحدًا مشتركًا (`extension_days`) بين تمديد الانقطاع
(`OUTAGE_EXTENSION_DAYS` — يتكرر يوميًا طالما البريد معطوب) وتمديد مهلة
الأداء الإلزامية (`GRACE_EXTENSION_DAYS=2`، مرة واحدة لكل فترة) — فإن تراكم
انقطاع بريد ≥ 2 يوم وحده (بلا أي مهلة أداء فعلية مُنحت)، ثم أُصلح البريد
والعميل لا يزال قاصرًا، كان يُسقِط مهلة الأداء الإلزامية بالكامل ويقفز
لتعويض فوري. الإصلاح: عمودان منفصلان بـ`guarantee_ledger` —
`grace_extension_days` (مهلة الأداء فقط) و`outage_extension_days` (انقطاع
البريد فقط) — ترحيل 0010. `extension_days` يبقى موجودًا **كمجموع الاثنين**
(توافقًا خلفيًا لأي قارئ حالي)، لكن فحص "هل استُهلِكت المهلة الإلزامية؟"
يقرأ `grace_extension_days` حصرًا الآن، لا المجموع. صفوف `guarantee_ledger`
القديمة (قبل هذا الترحيل) تبدأ بـ`grace_extension_days=0` افتراضيًا (القيمة
الافتراضية الآمنة الوحيدة الممكنة بلا سجلّ تاريخي يميّز مصدر كل يوم تمديد
سابق — راجع الملاحظة أعلاه) وقد تحصل على مهلة أداء إضافية مرة واحدة كأثر
انتقالي؛ الاتجاه الآمن الوحيد المقبول هنا: تأخير تعويض بيومين إضافيين لبضعة
عملاء قدامى مرة واحدة، لا إسقاط مهلة مستحقة لعميل جديد أبدًا.

P0.5 (2026-09-12، claude/masar_build_brief_v4.md): **لا تحويل نقدي بأي
مسار بعد الآن.** بعد استهلاك مهلة الأداء الإلزامية (يومان) ولا يزال العميل
قاصرًا (بريد سليم): بدل `refund_pending`/تعويض نقدي، تُمدّد
`subscriptions.ends_at` بعدد أيام = `ceil(shortfall / daily_target)` —
`daily_target` من `subscriptions.daily_target` (يُبذَر 17 لكل عميل حاليًا،
راجع customers_api.py/telegram_onboarding.py) بحد أقصى **مجموع تمديدات
تلقائية = 30 يومًا لكل فترة واحدة** (يتتبّعه `guarantee_ledger.
extension_days_total`، ترحيلة 0022 — يشمل grace+outage+هذا التمديد معًا).
إن استُهلِك السقف بالكامل ولا يزال قاصرًا: حالة `extended_final` (تُضاف
لقيد `ck_guarantee_ledger_status`) — الفترة تُغلَق نهائيًا بلا أي تحويل
نقدي (الحقول القديمة `refund_amount`/حالة `refund_pending` تبقى بالمخطّط
للتوافق الخلفي فقط، لا مسار كتابة جديد يُنتجها بعد الآن). منطق "بريد
معطوب → تمديد يوم" (`OUTAGE_EXTENSION_DAYS`) لا يتغيّر إطلاقًا. كل تمديد
(مهلة أداء، انقطاع بريد، أو تمديد عجز) يُرسل للعميل إشعارًا وديًا
(`notify_customer`) وللأدمن سطرًا إعلاميًا (`notify_admin` — إعلامي بحت،
لا يحتاج اعتمادًا لأنه لا تحويل مالي أبدًا الآن).

**إعادة تقييم الارتدادات المتأخرة**: أي سجلّ `guarantee_ledger` بحالة
`computed` (الهدف بُلّغ وقت الإغلاق) خلال 5 أيام من `period_end` قد يتغيّر
عدده المُحتَسَب لاحقًا (ارتداد يصل متأخرًا عبر inbox.py، يُعلّم `bounced`
بعد إغلاق الفترة) — `reassess_recent_computed_periods` تُعيد فحص هذه
السجلّات وتُصحّح `counted_sent`/`bounced`، وإن هبط العدّ تحت الهدف تُعاد
فتح الاشتراك المغلَق (`status='active'`) ليلتقطه `evaluate_period` بجولة
التقييم التالية (يمنحه مهلة الأداء من جديد، تماشيًا مع "لا يُظلَم عميل
لارتداد اكتُشف متأخرًا").
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.discovery import get_engine
from app.telegram_notify_admin import notify_admin, notify_customer

logger = logging.getLogger("masar.guarantee")

MONTHLY_TARGET = 510
GRACE_EXTENSION_DAYS = 2
OUTAGE_EXTENSION_DAYS = 1
# P0.5: سقف مجموع كل التمديدات التلقائية (مهلة أداء + انقطاع بريد + تمديد
# عجز) لفترة اشتراك واحدة — لا تحويل نقدي بعد استهلاكه، الفترة تُغلَق
# بحالة extended_final بدل ذلك.
MAX_AUTO_EXTENSION_DAYS = 30
# داخل شهر 30 يومًا مقابل هدف 510 — نفس القيمة المبذورة بـcustomers.target_daily/
# subscriptions.daily_target الافتراضيين (17)، تُستخدم فقط كاحتياط لو غاب
# daily_target عن صفّ اشتراك (قديم من قبل إضافة العمود).
DEFAULT_DAILY_TARGET = 17
# إعادة تقييم الارتدادات المتأخرة (P0.5): نافذة الأيام بعد period_end التي
# لا يزال يُعاد خلالها فحص سجلات 'computed' (راجع reassess_recent_computed_periods).
LATE_BOUNCE_REASSESS_WINDOW_DAYS = 5


def _fetch_customer(conn: Connection, customer_id: int) -> dict | None:
    row = conn.execute(
        text("SELECT id, name, status, price_sar, telegram_chat_id FROM customers WHERE id = :id"),
        {"id": customer_id},
    ).mappings().first()
    return dict(row) if row else None


def _fetch_due_subscription(conn: Connection, customer_id: int, now_utc: datetime) -> dict | None:
    """آخر اشتراك بحالة active/extended **انتهت فترته فعليًا** (ends_at <=
    الآن) لهذا العميل — الوحيد المؤهّل للتقييم اليوم. اشتراك لم تنتهِ فترته
    بعد يرجع None (لا شيء لتقييمه — لا خطأ)."""
    row = conn.execute(
        text(
            """
            SELECT id, customer_id, product_code, starts_at, ends_at, status, daily_target
            FROM subscriptions
            WHERE customer_id = :cid AND status IN ('active', 'extended') AND ends_at <= :now
            ORDER BY ends_at DESC LIMIT 1
            """
        ),
        {"cid": customer_id, "now": now_utc},
    ).mappings().first()
    return dict(row) if row else None


def _fetch_mail_link_status(conn: Connection, customer_id: int) -> str | None:
    row = conn.execute(text("SELECT status FROM mail_links WHERE customer_id = :id"), {"id": customer_id}).first()
    return row[0] if row else None


def _count_period_applications(conn: Connection, customer_id: int, start: datetime, end: datetime) -> tuple[int, int]:
    """يرجع (counted_sent, bounced) — يستخدم
    ix_applications_customer_status_sent_at (ترحيل 0008)."""
    counted = conn.execute(
        text(
            """
            SELECT count(*) FROM applications
            WHERE customer_id = :cid AND status != 'bounced' AND sent_at >= :start AND sent_at < :end
            """
        ),
        {"cid": customer_id, "start": start, "end": end},
    ).scalar()
    bounced = conn.execute(
        text(
            """
            SELECT count(*) FROM applications
            WHERE customer_id = :cid AND status = 'bounced' AND sent_at >= :start AND sent_at < :end
            """
        ),
        {"cid": customer_id, "start": start, "end": end},
    ).scalar()
    return int(counted or 0), int(bounced or 0)


def _fetch_existing_ledger(conn: Connection, customer_id: int, period_start: date) -> dict | None:
    row = conn.execute(
        text("SELECT * FROM guarantee_ledger WHERE customer_id = :cid AND period_start = :ps"),
        {"cid": customer_id, "ps": period_start},
    ).mappings().first()
    return dict(row) if row else None


def _upsert_ledger(conn: Connection, *, customer_id: int, period_start: date, period_end: date, **fields) -> dict:
    columns = ["customer_id", "period_start", "period_end"] + list(fields.keys())
    values = {"customer_id": customer_id, "period_start": period_start, "period_end": period_end, **fields}
    placeholders = ", ".join(f":{c}" for c in columns)
    update_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in fields.keys())
    row = conn.execute(
        text(
            f"""
            INSERT INTO guarantee_ledger ({", ".join(columns)}, updated_at)
            VALUES ({placeholders}, now())
            ON CONFLICT (customer_id, period_start) DO UPDATE SET
                {update_set}, updated_at = now()
            RETURNING id, customer_id, period_start, period_end, target, counted_sent, bounced,
                      shortfall, extension_days, grace_extension_days, outage_extension_days,
                      extension_days_total, refund_amount, status, created_at, updated_at
            """
        ),
        values,
    ).mappings().first()
    return dict(row)


def _notify_extension(customer: dict, days: int, reason_ar: str) -> None:
    """P0.5: إشعار ودّي للعميل + سطر إعلامي للأدمن عند أي تمديد تلقائي
    (مهلة أداء، انقطاع بريد، أو تمديد عجز) — best-effort بالكامل، فشل
    الإشعار لا يوقف تقييم الضمان نفسه أبدًا."""
    chat_id = customer.get("telegram_chat_id")
    if chat_id:
        try:
            notify_customer(
                chat_id,
                f"🌿 مدّدنا اشتراكك {days} يومًا إضافيًا تلقائيًا وبلا أي طلب منك بسبب {reason_ar} "
                "— نكمل لك المتابعة حتى نحقق هدفك، هذا وعدنا لك.",
            )
        except Exception:  # noqa: BLE001 — best-effort
            logger.exception("فشل إشعار العميل %s بتمديد الضمان", customer.get("id"))
    try:
        notify_admin(
            f"⏳ تمديد ضمان تلقائي: العميل {customer.get('id')} — {days} يومًا إضافيًا ({reason_ar})."
        )
    except Exception:  # noqa: BLE001 — best-effort
        logger.exception("فشل إشعار الأدمن بتمديد الضمان")


def evaluate_period(customer_id: int, engine: Engine | None = None, now: datetime | None = None) -> dict:
    """يقيّم آخر فترة اشتراك **منتهية** لهذا العميل ويُحدّث/يُنشئ صفّ
    guarantee_ledger. يُستدعى يوميًا 00:30 الرياض (scheduler_main.py) ومن
    نقطة التشغيل اليدوي — idempotent (نفس period_start يُحدّث في مكانه،
    لا صفّ جديد لكل تقييم)."""
    engine = engine or get_engine()
    now = now or datetime.now(timezone.utc)

    with engine.begin() as conn:
        customer = _fetch_customer(conn, customer_id)
        if not customer:
            return {"ok": False, "error": "customer_not_found"}

        sub = _fetch_due_subscription(conn, customer_id, now)
        if sub is None:
            return {"ok": True, "status": "na", "reason": "no_due_subscription", "customer_id": customer_id}

        period_start = sub["starts_at"].date()
        period_end = sub["ends_at"].date()
        counted_sent, bounced = _count_period_applications(conn, customer_id, sub["starts_at"], sub["ends_at"])

        existing = _fetch_existing_ledger(conn, customer_id, period_start)
        # عمودان منفصلان (تصحيح B5c أعلى الملف) — لا نقرأ extension_days
        # الكلي لفحص "هل استُهلِكت المهلة الإلزامية؟" أبدًا، فقط
        # grace_extension_days تحديدًا (مصدره حصرًا تمديد المهلة العادية).
        prior_grace_days = (existing or {}).get("grace_extension_days") or 0
        prior_outage_days = (existing or {}).get("outage_extension_days") or 0
        already_had_grace = prior_grace_days >= GRACE_EXTENSION_DAYS
        # P0.5: مجموع كل التمديدات التلقائية حتى الآن — max() احترازي لصفوف
        # انتقالية أُنشئت قبل إضافة هذا العمود (ترحيلة 0022 تبدأه 0 افتراضيًا
        # حتى لصف قديم كان يحمل grace/outage فعليين، راجع توثيق أعلى الملف).
        prior_extension_total = max(
            (existing or {}).get("extension_days_total") or 0, prior_grace_days + prior_outage_days
        )

        if counted_sent >= MONTHLY_TARGET:
            ledger = _upsert_ledger(
                conn,
                customer_id=customer_id,
                period_start=period_start,
                period_end=period_end,
                target=MONTHLY_TARGET,
                counted_sent=counted_sent,
                bounced=bounced,
                shortfall=0,
                extension_days=prior_grace_days + prior_outage_days,
                grace_extension_days=prior_grace_days,
                outage_extension_days=prior_outage_days,
                extension_days_total=prior_extension_total,
                refund_amount=None,
                status="computed",
            )
            conn.execute(
                text("UPDATE subscriptions SET status = 'closed' WHERE id = :id"), {"id": sub["id"]}
            )
            return {"ok": True, "customer_id": customer_id, **ledger}

        mail_status = _fetch_mail_link_status(conn, customer_id)
        mail_broken = mail_status is not None and mail_status != "ok"

        if mail_broken:
            # انقطاع بسبب بريد العميل نفسه — يُمدّد الفترة بدل التعويض
            # (الدليل §0 البند 5) — يتكرر يوميًا طالما الصندوق معطوبًا، بلا
            # وصول لمرحلة التعويض إطلاقًا (راجع ملاحظة النطاق أعلى الملف).
            # يزيد outage_extension_days فقط — لا يمسّ grace_extension_days
            # إطلاقًا، حتى لا يُحتسَب أي جزء من مهلة الأداء الإلزامية مُستهلَكًا
            # بسبب انقطاع لم يكن مهلة أداء فعلية (تصحيح B5c أعلى الملف).
            new_outage_days = prior_outage_days + OUTAGE_EXTENSION_DAYS
            new_total = prior_extension_total + OUTAGE_EXTENSION_DAYS
            new_ends_at = sub["ends_at"] + timedelta(days=OUTAGE_EXTENSION_DAYS)
            conn.execute(
                text("UPDATE subscriptions SET ends_at = :ends, status = 'extended' WHERE id = :id"),
                {"ends": new_ends_at, "id": sub["id"]},
            )
            ledger = _upsert_ledger(
                conn,
                customer_id=customer_id,
                period_start=period_start,
                period_end=new_ends_at.date(),
                target=MONTHLY_TARGET,
                counted_sent=counted_sent,
                bounced=bounced,
                shortfall=None,
                extension_days=prior_grace_days + new_outage_days,
                grace_extension_days=prior_grace_days,
                outage_extension_days=new_outage_days,
                extension_days_total=new_total,
                refund_amount=None,
                status="extended",
            )
            _notify_extension(customer, OUTAGE_EXTENSION_DAYS, "انقطاع مؤقت ببريدك")
            return {"ok": True, "customer_id": customer_id, "reason": "mail_link_broken", **ledger}

        if not already_had_grace:
            # تمديد يومين تلقائي (لمرة واحدة لكل فترة) — الدليل §0 البند 5.
            # يُمنح دومًا هنا (بصرف النظر عن أي انقطاع بريد سابق استهلك
            # outage_extension_days منفصلة) — هذا هو الإصلاح الفعلي لعيب B5c.
            new_grace_days = prior_grace_days + GRACE_EXTENSION_DAYS
            new_total = prior_extension_total + GRACE_EXTENSION_DAYS
            new_ends_at = sub["ends_at"] + timedelta(days=GRACE_EXTENSION_DAYS)
            conn.execute(
                text("UPDATE subscriptions SET ends_at = :ends, status = 'extended' WHERE id = :id"),
                {"ends": new_ends_at, "id": sub["id"]},
            )
            ledger = _upsert_ledger(
                conn,
                customer_id=customer_id,
                period_start=period_start,
                period_end=new_ends_at.date(),
                target=MONTHLY_TARGET,
                counted_sent=counted_sent,
                bounced=bounced,
                shortfall=None,
                extension_days=new_grace_days + prior_outage_days,
                grace_extension_days=new_grace_days,
                outage_extension_days=prior_outage_days,
                extension_days_total=new_total,
                refund_amount=None,
                status="extended",
            )
            _notify_extension(customer, GRACE_EXTENSION_DAYS, "عدم بلوغ الهدف الشهري بعد")
            return {"ok": True, "customer_id": customer_id, "reason": "grace_period_granted", **ledger}

        # P0.5: مهلة الأداء الإلزامية استُهلِكت فعليًا (grace_extension_days
        # >= 2) ولا يزال قاصرًا، والبريد سليم الآن — تمديد إضافي بعدد أيام
        # يكفي (تقديريًا) لتغطية العجز، بحد أقصى سقف MAX_AUTO_EXTENSION_DAYS
        # الكلي لهذه الفترة. **لا تحويل نقدي بأي مسار بعد الآن** (راجع توثيق
        # أعلى الملف) — الحقول القديمة refund_amount/status='refund_pending'
        # لا تُكتَب من هنا بعد اليوم، تبقى بالمخطّط للتوافق الخلفي فقط.
        shortfall = MONTHLY_TARGET - counted_sent
        remaining_budget = MAX_AUTO_EXTENSION_DAYS - prior_extension_total

        if remaining_budget <= 0:
            # السقف الكلي (30 يومًا) استُهلِك بالكامل ولا يزال قاصرًا — إغلاق
            # نهائي بلا مزيد من التمديد وبلا أي تحويل نقدي.
            ledger = _upsert_ledger(
                conn,
                customer_id=customer_id,
                period_start=period_start,
                period_end=period_end,
                target=MONTHLY_TARGET,
                counted_sent=counted_sent,
                bounced=bounced,
                shortfall=shortfall,
                extension_days=prior_grace_days + prior_outage_days,
                grace_extension_days=prior_grace_days,
                outage_extension_days=prior_outage_days,
                extension_days_total=prior_extension_total,
                refund_amount=None,
                status="extended_final",
            )
            conn.execute(text("UPDATE subscriptions SET status = 'closed' WHERE id = :id"), {"id": sub["id"]})
            notify_admin(
                "⏳ فترة ضمان أُغلقت بسقف التمديد الأقصى (30 يومًا) ولا تزال قاصرة "
                f"(إعلامي فقط — لا تحويل نقدي):\n\nالعميل: {customer_id}\nالعجز: {shortfall} تقديمًا."
            )
            return {"ok": True, "customer_id": customer_id, "reason": "extension_cap_reached", **ledger}

        daily_target = sub.get("daily_target") or DEFAULT_DAILY_TARGET
        needed_days = math.ceil(shortfall / daily_target) if daily_target > 0 else remaining_budget
        grant_days = min(needed_days, remaining_budget)
        new_total = prior_extension_total + grant_days
        new_ends_at = sub["ends_at"] + timedelta(days=grant_days)
        conn.execute(
            text("UPDATE subscriptions SET ends_at = :ends, status = 'extended' WHERE id = :id"),
            {"ends": new_ends_at, "id": sub["id"]},
        )
        ledger = _upsert_ledger(
            conn,
            customer_id=customer_id,
            period_start=period_start,
            period_end=new_ends_at.date(),
            target=MONTHLY_TARGET,
            counted_sent=counted_sent,
            bounced=bounced,
            shortfall=shortfall,
            extension_days=prior_grace_days + prior_outage_days,
            grace_extension_days=prior_grace_days,
            outage_extension_days=prior_outage_days,
            extension_days_total=new_total,
            refund_amount=None,
            status="extended",
        )
        _notify_extension(customer, grant_days, "عدم بلوغ الهدف الشهري بعد مهلة الأداء")
        return {"ok": True, "customer_id": customer_id, "reason": "shortfall_extension_granted", **ledger}


def reassess_recent_computed_periods(engine: Engine | None = None, now: datetime | None = None) -> dict:
    """P0.5: إعادة تقييم سجلّات `guarantee_ledger` بحالة 'computed' التي
    انتهت فترتها (`period_end`) خلال آخر `LATE_BOUNCE_REASSESS_WINDOW_DAYS`
    أيام — ارتداد يصل متأخرًا (`inbox.py` يُعلّم تطبيقًا `bounced` بعد إغلاق
    الفترة أصلًا) قد يُنزل `counted_sent` الفعلي تحت الهدف رغم أن الفترة
    أُغلقت كـ'computed' سابقًا. عند اكتشاف تغيّر العدّ: يُصحّح
    `counted_sent`/`bounced` بالسجلّ، وإن هبط تحت الهدف يُعاد فتح الاشتراك
    (`status='active'`) ليلتقطه `evaluate_period` بالجولة التالية (يمنحه
    مهلة الأداء من جديد — لا يُظلَم عميل لارتداد اكتُشف متأخرًا). يُستدعى من
    `run_guarantee_round` قبل الحلقة الرئيسية."""
    engine = engine or get_engine()
    now = now or datetime.now(timezone.utc)
    today = now.date()
    cutoff = today - timedelta(days=LATE_BOUNCE_REASSESS_WINDOW_DAYS)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, customer_id, period_start, period_end, counted_sent
                FROM guarantee_ledger
                WHERE status = 'computed' AND period_end >= :cutoff AND period_end <= :today
                """
            ),
            {"cutoff": cutoff, "today": today},
        ).mappings().all()

    checked = 0
    corrected = 0
    reopened = 0
    for r in rows:
        checked += 1
        try:
            period_start_dt = datetime.combine(r["period_start"], datetime.min.time(), tzinfo=timezone.utc)
            period_end_dt = datetime.combine(
                r["period_end"] + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
            )
            with engine.begin() as conn:
                new_counted, new_bounced = _count_period_applications(
                    conn, r["customer_id"], period_start_dt, period_end_dt
                )
                if new_counted == r["counted_sent"]:
                    continue
                corrected += 1
                conn.execute(
                    text(
                        "UPDATE guarantee_ledger SET counted_sent = :cs, bounced = :b, updated_at = now() "
                        "WHERE id = :id"
                    ),
                    {"cs": new_counted, "b": new_bounced, "id": r["id"]},
                )
                if new_counted < MONTHLY_TARGET:
                    result = conn.execute(
                        text(
                            "UPDATE subscriptions SET status = 'active' "
                            "WHERE customer_id = :cid AND starts_at::date = :ps AND status = 'closed'"
                        ),
                        {"cid": r["customer_id"], "ps": r["period_start"]},
                    )
                    if result.rowcount:
                        reopened += 1
                        notify_admin(
                            "🔁 ارتداد متأخر خفّض عدد فترة ضمان مُغلقة تحت الهدف — أُعيد فتحها "
                            f"للتقييم:\n\nالعميل: {r['customer_id']}\nالعدد الجديد: {new_counted}/{MONTHLY_TARGET}"
                        )
        except Exception:  # noqa: BLE001 — عزل خطأ سجلّ واحد عن بقية إعادة التقييم
            logger.exception("فشل إعادة تقييم سجلّ ضمان متأخر (customer_id=%s)", r.get("customer_id"))

    return {"ok": True, "checked": checked, "corrected": corrected, "reopened": reopened}


def run_guarantee_round(engine: Engine | None = None, now: datetime | None = None) -> dict:
    """نقطة الدخول الرئيسية — يُشغَّل يوميًا 00:30 الرياض (scheduler_main.py)
    لكل عميل لديه اشتراك بحالة active/extended انتهت فترته فعليًا (بلا
    قيد status='active' على العميل نفسه عمدًا — عميل موقوف قد لا يزال له
    اشتراك واجب التقييم/التمديد/التعويض)."""
    engine = engine or get_engine()
    now = now or datetime.now(timezone.utc)

    # P0.5: إعادة تقييم فترات 'computed' القريبة أولًا (ارتدادات متأخرة قد
    # تُعيد فتح اشتراكًا أُغلق بالخطأ فوق الهدف) — قبل الحلقة الرئيسية، حتى
    # يلتقط استعلام customer_ids أدناه أي اشتراك أُعيد فتحه للتو بنفس الجولة.
    reassessment = reassess_recent_computed_periods(engine=engine, now=now)

    with engine.connect() as conn:
        customer_ids = [
            r[0]
            for r in conn.execute(
                text(
                    """
                    SELECT DISTINCT customer_id FROM subscriptions
                    WHERE status IN ('active', 'extended') AND ends_at <= :now
                    """
                ),
                {"now": now},
            ).all()
        ]

    evaluated = 0
    errors = 0
    by_status: dict[str, int] = {}
    for customer_id in customer_ids:
        try:
            result = evaluate_period(customer_id, engine=engine, now=now)
            evaluated += 1
            status = result.get("status", "unknown")
            by_status[status] = by_status.get(status, 0) + 1
            # P0.5: لا مسار تحويل نقدي متبقٍّ — evaluate_period نفسه يُرسل
            # إشعار العميل/الأدمن لكل تمديد (_notify_extension) ولإغلاق سقف
            # التمديد (extended_final) مباشرة، فلا حاجة لأي تنبيه إضافي هنا.
        except Exception:  # noqa: BLE001 — عزل خطأ عميل واحد عن بقية الدورة
            logger.exception("فشل تقييم فترة الضمان للعميل %s", customer_id)
            errors += 1

    result = {
        "ok": True,
        "due_customers": len(customer_ids),
        "evaluated": evaluated,
        "errors": errors,
        "by_status": by_status,
        "reassessment": reassessment,
    }
    logger.info("جولة تقييم الضمان انتهت: %s", result)
    return result
