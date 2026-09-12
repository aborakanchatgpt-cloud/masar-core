"""
Masar Core — التقرير الشامل الجديد "📊 تقرير شامل" ببوت الأدمن (B6/v3،
`claude/masar_build_brief_v3_2026-09-11.md`).

منطق تجميع/تنسيق **بحت** فقط بهذا الملف — لا Telegram، لا جلسات، لا شيء
تفاعلي (نفس نمط فصل `reports.py`/`skill_gap.py` عن ملفات الأدمن التي
تستدعيهما؛ التوصيل الفعلي بالأزرار/الجلسات بملف منفصل
`app.telegram_admin_reports_summary`). هذا التقرير **مستقلّ تمامًا** عن
"📨 تقرير عميل" (`reports.py` — تقرير فردي يومي لعميل واحد يصله هو) وعن
"📊 نظرة عامة" (`overview_api.py` — أرقام تشغيلية لحظية للأدمن) — لا تعديل
على أيٍّ منهما هنا إطلاقًا (تعليمات صريحة بملخّص التصميم).

**أقسام التقرير الثلاثة:**

1. **الملخص المالي** — من `payment_requests` حيث `status='confirmed'` خلال
   الفترة (بحسب `decided_at`، لا `created_at` — لحظة التأكيد الفعلية هي ما
   يهمّ تقرير "ماذا تحصّل خلال هذه الفترة")، مُجمَّع حسب `product_code`/
   `products.name_ar`. **قرار تصميم:** المبلغ المعتمَد هو `expected_amount`
   (السعر الرسمي للباقة وقت إنشاء الطلب) لا `declared_amount` (رقم يكتبه
   العميل بنفسه عند تقديم الإيصال، قد يخطئ فيه) — تأكيد الأدمن للطلب
   (`status='confirmed`) يعني أصلًا أنه راجع الإيصال ووجده مطابقًا للسعر
   الرسمي، فـ`expected_amount` هو التمثيل الموثوق للإيراد الفعلي المؤكَّد.

2. **"رسائل وصلتك كمالك/مطوّر النظام"** — **ليست فئة رابعة** لقناة "تواصل
   معنا" (تبقى فئاتها الثلاث كما هي بـ`telegram_onboarding.py`
   `CONTACT_CATEGORY_LABELS`) بل قسم تقريري يعرض ما وصل فعليًا خلال الفترة
   من `customer_messages` حيث `direction='in'` **و`category IS NOT NULL`**
   (تحديدًا رسائل قناة "تواصل معنا" المصنَّفة — رسائل `direction='in'` بلا
   تصنيف، إن وُجدت، ليست ضمن نطاق هذا القسم لأنها ليست تصعيدات تستحق تجميعًا
   بتقرير شامل). عناوين الفئات (`_CATEGORY_LABELS` أدناه) يجب أن تبقى مطابقة
   لـ`telegram_onboarding.CONTACT_CATEGORY_LABELS` — نسخة محلية بدل استيراد
   مباشر، بنفس نمط تكرار قواميس التصنيف الصغيرة بالمشروع (مثال:
   `BANK_FIELD_LABELS` بـ`telegram_admin_settings.py`).

3. **إحصاءات الإرسال المعتادة** — بنفس نمط استعلامات `overview_api.py`
   (`applications.sent_at`/`status='bounced'`)، لكن محسوبة لكامل الفترة
   المطلوبة لا "اليوم"/"آخر 7 أيام" الثابتَين هناك.

**نطاق الفترة:** أزرار سريعة (يومي/أسبوعي/شهري عبر `compute_period_range`)
أو مدى مخصَّص (تاريخين يُدخَلهما الأدمن نصًّا، التحقّق من الصيغة يقع بملف
التوصيل `telegram_admin_reports_summary.py` قبل استدعاء `build_summary`
هنا — هذا الملف يفترض `start_date`/`end_date` صحيحين دومًا، ويُصحّح ترتيبهما
دفاعًا فقط إن انعكسا).
"""
from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.pacing import to_riyadh_naive, utc_now

# ملاحظة: لا تقسيم نص يدوي هنا — TelegramClient.send_message (telegram_client.py)
# يقسّم تلقائيًا أي نص أطول من SAFE_SPLIT_LIMIT (راجع split_message_text)،
# فملف التوصيل telegram_admin_reports_summary.py يمرّر النص الكامل مباشرة.

# يجب أن تبقى مطابقة تمامًا لـ telegram_onboarding.CONTACT_CATEGORY_LABELS
# (نفس المفاتيح والقيم) — راجع تعليق الرأس أعلاه لسبب عدم الاستيراد المباشر.
_CATEGORY_LABELS: dict[str, str] = {
    "complaint": "😔 شكاوى",
    "heart_to_heart": "💬 من قلب لقلب",
    "note": "📝 ملاحظات",
}
_CATEGORY_ORDER = ["complaint", "heart_to_heart", "note"]

_MSG_SNIPPET_CHARS = 300


def _riyadh_today() -> date:
    return to_riyadh_naive(utc_now()).date()


def _riyadh_day_bounds_utc(d: date) -> tuple[datetime, datetime]:
    """يرجع (بداية، نهاية) اليوم `d` (وقت رياض) كـUTC — نسخة محلية مطابقة
    لتلك بـ`reports.py` (نفس مبرّر عدم الاستيراد المباشر بأعلى الملف)."""
    from app.pacing import riyadh_naive_to_utc

    day_start = datetime.combine(d, dt_time(0, 0))
    day_end = datetime.combine(d, dt_time(23, 59, 59))
    return riyadh_naive_to_utc(day_start), riyadh_naive_to_utc(day_end) + timedelta(seconds=1)


def compute_period_range(period: str, today: date | None = None) -> tuple[date, date]:
    """أزرار الفترة السريعة: يومي = اليوم فقط، أسبوعي = آخر 7 أيام (شاملة
    اليوم)، شهري = آخر 30 يومًا (شاملة اليوم). `today` معامل اختياري
    للاختبار (بلا اتصال قاعدة بيانات) — الاستخدام الفعلي ببوت الأدمن يتركه
    فارغًا فيُحسَب من التوقيت الفعلي."""
    d = today if today is not None else _riyadh_today()
    if period == "day":
        return d, d
    if period == "week":
        return d - timedelta(days=6), d
    if period == "month":
        return d - timedelta(days=29), d
    raise ValueError(f"فترة غير معروفة: {period}")


def build_summary(engine: Engine, start_date: date, end_date: date) -> dict:
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    start_utc, _ = _riyadh_day_bounds_utc(start_date)
    _, end_utc = _riyadh_day_bounds_utc(end_date)

    with engine.connect() as conn:
        # ix_payment_requests_status (ترحيل 0017) + JOIN products بمفتاحه
        # الأساسي (code) — لا فهرس إضافي مطلوب لعدد الباقات الصغير الحالي.
        financial_rows = conn.execute(
            text(
                """
                SELECT pr.product_code, p.name_ar, count(*) AS n,
                       coalesce(sum(pr.expected_amount), 0) AS total
                FROM payment_requests pr
                JOIN products p ON p.code = pr.product_code
                WHERE pr.status = 'confirmed' AND pr.decided_at >= :start AND pr.decided_at < :end
                GROUP BY pr.product_code, p.name_ar
                ORDER BY total DESC
                """
            ),
            {"start": start_utc, "end": end_utc},
        ).all()

        # customer_messages.category (ترحيل 0020) — IS NOT NULL يحصر النتيجة
        # برسائل قناة "تواصل معنا" المصنَّفة فقط (راجع تعليق الرأس، البند 2).
        message_rows = conn.execute(
            text(
                """
                SELECT cm.customer_id, c.name AS customer_name, cm.category, cm.text, cm.created_at
                FROM customer_messages cm
                JOIN customers c ON c.id = cm.customer_id
                WHERE cm.direction = 'in' AND cm.category IS NOT NULL
                  AND cm.created_at >= :start AND cm.created_at < :end
                ORDER BY cm.created_at ASC
                """
            ),
            {"start": start_utc, "end": end_utc},
        ).all()

        # ix_applications_status_sent_at — بنفس نمط overview_api.py، لكن
        # لمدى الفترة المطلوبة بدل "اليوم"/"آخر 7 أيام" الثابتَين هناك.
        sends_count = conn.execute(
            text("SELECT count(*) FROM applications WHERE sent_at >= :start AND sent_at < :end"),
            {"start": start_utc, "end": end_utc},
        ).scalar()
        bounces_count = conn.execute(
            text(
                "SELECT count(*) FROM applications "
                "WHERE status = 'bounced' AND sent_at >= :start AND sent_at < :end"
            ),
            {"start": start_utc, "end": end_utc},
        ).scalar()

    financial = [
        {
            "product_code": row.product_code,
            "name_ar": row.name_ar,
            "count": int(row.n),
            "total": float(row.total),
        }
        for row in financial_rows
    ]
    grand_total = sum(r["total"] for r in financial)

    messages: dict[str, list[dict]] = {key: [] for key in _CATEGORY_ORDER}
    for row in message_rows:
        cat = row.category if row.category in messages else None
        if cat is None:
            continue
        messages[cat].append(
            {
                "customer_id": row.customer_id,
                "customer_name": row.customer_name or "غير معروف",
                "text": row.text,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "financial": {"rows": financial, "grand_total": grand_total},
        "messages": messages,
        "stats": {"sends": int(sends_count or 0), "bounces": int(bounces_count or 0)},
    }


def _snippet(text_body: str) -> str:
    text_body = (text_body or "").strip()
    if len(text_body) > _MSG_SNIPPET_CHARS:
        return text_body[:_MSG_SNIPPET_CHARS].rstrip() + "…"
    return text_body


def build_summary_text(payload: dict) -> str:
    start_date = payload["start_date"]
    end_date = payload["end_date"]
    period_line = f"🗓️ {start_date}" if start_date == end_date else f"🗓️ من {start_date} إلى {end_date}"

    lines = ["📊 التقرير الشامل", period_line, ""]

    lines.append("💰 الملخص المالي (طلبات مؤكَّدة):")
    fin = payload["financial"]
    if not fin["rows"]:
        lines.append("لا طلبات دفع مؤكَّدة بهذه الفترة.")
    else:
        for row in fin["rows"]:
            lines.append(f"• {row['name_ar']}: {row['count']} — {row['total']:.0f} ريال")
        lines.append(f"الإجمالي: {fin['grand_total']:.0f} ريال")
    lines.append("")

    lines.append("✉️ رسائل وصلتك كمالك/مطوّر النظام:")
    messages = payload["messages"]
    total_messages = sum(len(v) for v in messages.values())
    if total_messages == 0:
        lines.append("لا رسائل جديدة بهذه الفترة.")
    else:
        for cat in _CATEGORY_ORDER:
            items = messages.get(cat, [])
            if not items:
                continue
            lines.append(f"{_CATEGORY_LABELS[cat]} ({len(items)}):")
            for m in items:
                lines.append(f"  • {m['customer_name']} (#{m['customer_id']}) — {_snippet(m['text'])}")
    lines.append("")

    lines.append("📈 إحصاءات الإرسال:")
    stats = payload["stats"]
    lines.append(f"• تقديمات مُرسَلة: {stats['sends']}")
    lines.append(f"• مرتجعة (bounced): {stats['bounces']}")

    return "\n".join(lines)
