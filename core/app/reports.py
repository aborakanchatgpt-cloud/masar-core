"""
Masar Core — التقرير اليومي (B5a، الدليل §3.13، تكليف B5a البند 1).

`build_customer_report(customer_id, date)` يبني قاموسًا (payload) **ونصًا**
عربيًا جاهزًا للإرسال (`text`) بنبرة مسار الإنسانية (الدليل §0 البند 6،
§4.9): تحية، تقديمات اليوم (شركة/مسمى/مدينة)، تقدّم الفترة بكلمات بشرية
(بلا كسور ولا "حد يومي" — راجع تعليق `_progress_phrase` أدناه)، قسم
"استبعدنا لك" بأسباب ودّية، وخاتمة دعاء. **لا يذكر أبدًا** أتمتة/AI، ولا
صياغة مالية/تجارية، ولا أي حدّ/سقف/تقدير رقمي صريح (فقط أعداد "إنجاز" بلا
مقام — مسموح: "قدّمنا لك اليوم 17 فرصة"؛ ممنوع: "17/22" أو "بلغت حدك اليومي").

تصحيح B5c (NEEDS-CORE #1 بـdocs/reports/B5b-executor.md وn8n/README.md §3):
كل عنصر بـ`today_applications` يحمل الآن `send_queue_id` (+`company_id`،
`job_id`) — `send_queue_id` يُشتق بـJOIN على `send_queue.opportunity_id =
applications.opportunity_id AND send_queue.status='sent'` (لا عمود جديد على
applications، ولا لمس لـsender.py المملوك لمنفّذ B6 المتوازي — send_builder.py
يمنع أصلًا أكثر من صفّ send_queue غير ملغى لكل opportunity_id، فالمطابقة
حتمية). `null` إن كانت applications.opportunity_id فارغة (تطبيقات قديمة قبل
B3، نادرة). وركفلو n8n (`masar_daily_report_relay.json`) مبني مسبقًا ليقرأ
هذا الحقل تلقائيًا بلا أي تعديل هناك — راجع فلتر `withIds` بعقدة "بناء
الرسائل". قائمة `excluded` تحمل `company_id`/`job_id` (متاحان دومًا من
opportunities/jobs) و`send_queue_id: null` دومًا (الفرص المُستبعَدة لم تُبن
لها صفوف send_queue إطلاقًا — لا معنى تقنيًا لأزرار 👎/🎉 عليها اليوم).

`run_reports_round(date=None)` يُشغَّل من scheduler_main.py الساعة 19:00
الرياض ويُدرج صفًّا واحدًا idempotent لكل عميل **نشط** (status='active')
في `daily_reports` (ON CONFLICT (customer_id, report_date) DO NOTHING —
تشغيل ثانِـ لنفس اليوم لا يُكرّر ولا يُبدّل صفًّا مُسلّمًا أصلًا).

ملاحظة نطاق موثَّقة صراحة (B5a): قسم "استبعدنا لك" يعتمد فقط على أسباب
استبعاد **محفوظة فعليًا بقاعدة البيانات** اليوم — تحديدًا
`opportunities.status='skipped'` (send_builder.py: تبريد الشركة/سقف أسبوعي/
لا بريد توظيف معروف) وحديثًا `customer_company_exclusions` (👎 هذا الملف،
البند 2). استبعادات مستوى المطابقة (سنوات الخبرة/الجنسية بـmatching.py
check_disqualifiers) **لا تُخزَّن أبدًا** بأي جدول اليوم — matching.py/
planner.py يستبعدان الوظيفة قبل إنشاء أي صفّ opportunities إطلاقًا (راجع
core/app/planner.py:_select_for_customer، `if result.disqualified: continue`)
— فجوة بنيوية موجودة أصلًا بـB3، خارج نطاق B5a تعديلها (planner.py/matching.py
ملفّان WIP بانتظار مراجعة قبول منفصلة؛ التعديل فيهما بمعزل عن ذلك القبول
خطر غير مبرّر لهذا البند). مُوثَّق أيضًا بـdocs/reports/B5a-executor.md
كبند NEEDS-OWNER/متابعة مستقبلية.
"""
from __future__ import annotations

import hashlib
import logging
import random
from datetime import date, datetime, time as dt_time, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.discovery import get_engine
from app.pacing import to_riyadh_naive, utc_now

logger = logging.getLogger("masar.reports")

MONTHLY_TARGET = 510

_GREETINGS = [
    "مساء الخير يا {name} 🌙",
    "السلام عليكم يا {name}،",
    "أهلًا بك يا {name} 🌿",
    "يومك سعيد يا {name}،",
]

_NO_APPS_LINES = [
    "اليوم لم نجد فرصًا جديدة تناسب ملفك تمامًا، لكننا نواصل البحث لك يوميًا بإذن الله.",
    "لم تصلنا اليوم فرصة تليق بخبرتك، وسنستمر بالمتابعة لك غدًا.",
]

_CLOSINGS = [
    "وفّقك الله وبارك في سعيك 🌿",
    "نسأل الله لك فتحًا قريبًا وخيرًا يليق بك.",
    "دعواتنا لك بالتوفيق، ونحن معك خطوة بخطوة.",
    "بإذن الله يكون قريبًا خبر يسعدك — استمر ولا تكل.",
]

# خرائط أسباب استبعاد "استبعدنا لك" — ودّية، بلا أي ذكر رقمي/سقف/أتمتة.
# المفاتيح تطابق opportunities.reasons->>'skip_reason' (send_builder.py)
# وقيمة اصطناعية واحدة جديدة (company_excluded_by_customer) من هذا الملف
# (core/app/send_builder.py تصحيح البند 2 — راجع _mark_skipped هناك).
_FRIENDLY_EXCLUSION_REASONS: dict[str, str] = {
    "company_cooldown": "تقدّمنا لك لهذه الشركة مؤخرًا، فتركنا لها وقتًا قبل التكرار",
    "weekly_company_cap": "هذه الشركة استقبلت تقديمات من عدد من الباحثين هذا الأسبوع، فأجّلنا التقديم لها الآن",
    "company_excluded_by_customer": "استبعدناها بناءً على طلبك سابقًا",
    "no_apply_email": "لم نجد بعد بريد توظيف موثوقًا لهذه الشركة",
}


def _seed_rng(customer_id: int, report_date: date) -> random.Random:
    digest = hashlib.sha256(f"report:{customer_id}:{report_date.isoformat()}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _riyadh_day_bounds_utc(d: date) -> tuple[datetime, datetime]:
    """يرجع (بداية، نهاية) اليوم `d` (وقت رياض) كـUTC حقيقي — نفس مخطّط
    التحويل بـpacing.py/send_builder.py (لا حساب توقيت مباشر هنا)."""
    from app.pacing import riyadh_naive_to_utc

    day_start = datetime.combine(d, dt_time(0, 0))
    day_end = datetime.combine(d, dt_time(23, 59, 59))
    return riyadh_naive_to_utc(day_start), riyadh_naive_to_utc(day_end) + timedelta(seconds=1)


def _fetch_customer(conn: Connection, customer_id: int) -> dict | None:
    row = conn.execute(
        text("SELECT id, name, status, price_sar FROM customers WHERE id = :id"), {"id": customer_id}
    ).mappings().first()
    return dict(row) if row else None


def _fetch_today_applications(conn: Connection, customer_id: int, day_start: datetime, day_end: datetime) -> list[dict]:
    """send_queue_id عبر LEFT JOIN على send_queue.opportunity_id (لا عمود
    جديد على applications — راجع تعليق الرأس، تصحيح B5c). شرط
    `sq.status = 'sent'` بـON (لا WHERE) يبقيها LEFT JOIN حقيقيًا — تطبيق بلا
    opportunity_id (نادر، تطبيقات قديمة قبل B3) يبقى بالنتيجة بـsend_queue_id
    NULL بدل أن يُستبعَد بالكامل."""
    rows = conn.execute(
        text(
            """
            SELECT j.company_name AS company, j.title, j.city, a.status,
                   a.job_id AS job_id, j.company_id AS company_id, sq.id AS send_queue_id
            FROM applications a
            JOIN jobs j ON j.id = a.job_id
            LEFT JOIN send_queue sq ON sq.opportunity_id = a.opportunity_id AND sq.status = 'sent'
            WHERE a.customer_id = :cid AND a.sent_at >= :start AND a.sent_at < :end
            ORDER BY a.sent_at
            """
        ),
        {"cid": customer_id, "start": day_start, "end": day_end},
    ).mappings().all()
    return [dict(r) for r in rows]


def _fetch_active_subscription(conn: Connection, customer_id: int, on_date: date) -> dict | None:
    row = conn.execute(
        text(
            """
            SELECT id, starts_at, ends_at, status FROM subscriptions
            WHERE customer_id = :cid AND status IN ('active', 'extended')
              AND starts_at::date <= :d AND ends_at::date >= :d
            ORDER BY starts_at DESC LIMIT 1
            """
        ),
        {"cid": customer_id, "d": on_date},
    ).mappings().first()
    return dict(row) if row else None


def _count_counted_applications(conn: Connection, customer_id: int, start: datetime, end: datetime | None) -> int:
    """يُحصي applications المُحتسَبة (بلا المرتدة — bounced لا تُحتسب أبدًا،
    القاعدة الثابتة) خلال فترة (start, end] — end=None يعني حتى الآن. يستخدم
    ix_applications_customer_status_sent_at (ترحيل 0008)."""
    sql = "SELECT count(*) FROM applications WHERE customer_id = :cid AND status != 'bounced' AND sent_at >= :start"
    params: dict = {"cid": customer_id, "start": start}
    if end is not None:
        sql += " AND sent_at < :end"
        params["end"] = end
    return int(conn.execute(text(sql), params).scalar() or 0)


def _fetch_today_exclusions(conn: Connection, customer_id: int, report_date: date, limit: int = 15) -> list[dict]:
    """company_id/job_id متاحان دومًا (من opportunities/jobs مباشرة، بلا
    JOIN إضافي). send_queue_id يبقى None صراحة دومًا (تصحيح B5c، راجع تعليق
    الرأس) — فرصة مُستبعَدة لم تُبنى لها صفّ send_queue قط."""
    rows = conn.execute(
        text(
            """
            SELECT j.company_name AS company, j.title, j.city, o.reasons->>'skip_reason' AS reason_code,
                   o.job_id AS job_id, j.company_id AS company_id
            FROM opportunities o JOIN jobs j ON j.id = o.job_id
            WHERE o.customer_id = :cid AND o.planned_for = :d AND o.status = 'skipped'
              AND o.reasons->>'skip_reason' = ANY(:codes)
            ORDER BY o.id
            LIMIT :limit
            """
        ),
        {"cid": customer_id, "d": report_date, "codes": list(_FRIENDLY_EXCLUSION_REASONS.keys()), "limit": limit},
    ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["reason_text"] = _FRIENDLY_EXCLUSION_REASONS.get(d["reason_code"], "لم تكن مناسبة تمامًا لملفك")
        d["send_queue_id"] = None
        out.append(d)
    return out


def _fetch_today_replies(conn: Connection, customer_id: int, day_start: datetime, day_end: datetime) -> list[dict]:
    rows = conn.execute(
        text(
            """
            SELECT from_addr, kind FROM inbox_events
            WHERE customer_id = :cid AND kind IN ('reply', 'interview')
              AND created_at >= :start AND created_at < :end
            ORDER BY created_at
            """
        ),
        {"cid": customer_id, "start": day_start, "end": day_end},
    ).mappings().all()
    return [dict(r) for r in rows]


def _progress_phrase(period: dict | None, period_count: int, all_time_count: int) -> str:
    """نص التقدّم بكلمات بشرية بلا أي كسر/سقف (الدليل §0 البند 6: "لا يُعرض
    للعميل أي تقدير طاقة أو سقف"). عدد "إنجاز" مجرَّد (بلا مقام) مسموح —
    نفس منطق "قدّمنا لك اليوم 17 فرصة" بمثال التكليف حرفيًا."""
    if period is not None:
        if period_count <= 0:
            return "ومنذ بداية فترتك الحالية معنا، بدأنا للتو نبحث لك عن أفضل الفرص."
        return f"ومنذ بداية فترتك الحالية معنا، قدّمنا لك حتى الآن {period_count} فرصة، ولا نزال نعمل على استكمال الباقي بإذن الله."
    if all_time_count <= 0:
        return "بدأنا للتو رحلتنا معك في البحث عن فرصة تليق بك."
    return f"ومنذ بداية تعاملك معنا، قدّمنا لك حتى الآن {all_time_count} فرصة."


def _build_text(
    *,
    customer_name: str,
    report_date: date,
    today_apps: list[dict],
    period: dict | None,
    period_count: int,
    all_time_count: int,
    exclusions: list[dict],
    replies: list[dict],
    rng: random.Random,
) -> str:
    lines: list[str] = []
    lines.append(rng.choice(_GREETINGS).format(name=customer_name or "صاحبنا"))
    lines.append("")

    today_count = len(today_apps)
    if today_count > 0:
        lines.append(f"قدّمنا لك اليوم {today_count} فرصة على وظائف تناسب ملفك:")
        for app in today_apps:
            title = app.get("title") or "فرصة"
            company = app.get("company") or "شركة"
            city = app.get("city")
            line = f"• {title} في {company}"
            if city:
                line += f" — {city}"
            lines.append(line)
    else:
        lines.append(rng.choice(_NO_APPS_LINES))
    lines.append("")

    lines.append(_progress_phrase(period, period_count, all_time_count))

    if replies:
        lines.append("")
        lines.append("وصلتنا اليوم ردود من بعض الشركات، وسنطّلعك على تفاصيلها:")
        for r in replies:
            kind_ar = "دعوة لمقابلة" if r.get("kind") == "interview" else "رد"
            lines.append(f"• {kind_ar} من {r.get('from_addr') or 'إحدى الشركات'}")

    if exclusions:
        lines.append("")
        lines.append("استبعدنا لك اليوم بعض الفرص التي وجدناها لكنها لم تكن مناسبة تمامًا:")
        for ex in exclusions:
            title = ex.get("title") or "فرصة"
            company = ex.get("company") or "شركة"
            lines.append(f"• {title} في {company} — {ex.get('reason_text')}")

    lines.append("")
    lines.append(rng.choice(_CLOSINGS))

    return "\n".join(lines)


def build_customer_report(customer_id: int, report_date: date, engine: Engine | None = None) -> dict | None:
    """يبني تقرير عميل واحد لتاريخ محدَّد — **لا يكتب أي شيء بقاعدة
    البيانات** (قراءة فقط)؛ الإدراج بـdaily_reports مسؤولية `run_reports_round`
    فقط. يرجع None إن لم يوجد العميل."""
    engine = engine or get_engine()
    day_start, day_end = _riyadh_day_bounds_utc(report_date)

    with engine.connect() as conn:
        customer = _fetch_customer(conn, customer_id)
        if not customer:
            return None

        today_apps = _fetch_today_applications(conn, customer_id, day_start, day_end)
        exclusions = _fetch_today_exclusions(conn, customer_id, report_date)
        replies = _fetch_today_replies(conn, customer_id, day_start, day_end)

        subscription = _fetch_active_subscription(conn, customer_id, report_date)
        period: dict | None = None
        period_count = 0
        all_time_count = 0
        if subscription:
            period_start_utc, _ = _riyadh_day_bounds_utc(subscription["starts_at"].date())
            period_count = _count_counted_applications(conn, customer_id, period_start_utc, None)
            period = {
                "start": subscription["starts_at"].date().isoformat(),
                "end": subscription["ends_at"].date().isoformat(),
                "target": MONTHLY_TARGET,
                "counted_sent": period_count,
            }
        else:
            all_time_count = _count_counted_applications(conn, customer_id, datetime(2000, 1, 1, tzinfo=timezone.utc), None)

    rng = _seed_rng(customer_id, report_date)
    text_body = _build_text(
        customer_name=customer.get("name") or "",
        report_date=report_date,
        today_apps=today_apps,
        period=period,
        period_count=period_count,
        all_time_count=all_time_count,
        exclusions=exclusions,
        replies=replies,
        rng=rng,
    )

    payload = {
        "customer_id": customer_id,
        "date": report_date.isoformat(),
        "today_count": len(today_apps),
        "today_applications": today_apps,
        "period": period,
        "all_time_count": all_time_count if period is None else None,
        "excluded": exclusions,
        "replies_today": replies,
        "text": text_body,
    }
    return payload


def _upsert_report(conn: Connection, customer_id: int, report_date: date, payload: dict) -> bool:
    """يُدرج صفًّا جديدًا فقط (idempotent per customer+date — تكليف B5a البند
    1). ON CONFLICT DO NOTHING عمدًا: تشغيلة ثانية لنفس اليوم **لا تُبدّل**
    تقريرًا رُبما سُلِّم أصلًا (status='delivered') — لإعادة توليد فعلية
    استخدم /admin/reports/run مع تنظيف يدوي مسبق إن لزم (خارج نطاق B5a)."""
    import json

    result = conn.execute(
        text(
            """
            INSERT INTO daily_reports (customer_id, report_date, payload, status, created_at)
            VALUES (:cid, :d, CAST(:payload AS jsonb), 'queued', now())
            ON CONFLICT (customer_id, report_date) DO NOTHING
            RETURNING id
            """
        ),
        {"cid": customer_id, "d": report_date, "payload": json.dumps(payload, ensure_ascii=False)},
    ).first()
    return result is not None


def run_reports_round(report_date: date | None = None, engine: Engine | None = None) -> dict:
    """نقطة الدخول الرئيسية — صفًّ واحد لكل عميل **نشط فقط** (status='active'،
    الدليل: التقرير خدمة للمشترك الفعّال). عميل موقوف/منتهٍ لا يحصل على
    تقرير اليوم (لا خطأ — يُتخطّى بصمت، محسوب بـ`customers_skipped`).
    idempotent بالكامل (لا يرفع استثناءً لخطأ عميل واحد، بنفس فلسفة
    discovery.run_round/planner.run_plan_round)."""
    engine = engine or get_engine()
    report_date = report_date or to_riyadh_naive(utc_now()).date()

    with engine.connect() as conn:
        customer_ids = [
            r[0] for r in conn.execute(text("SELECT id FROM customers WHERE status = 'active'")).all()
        ]

    created = 0
    already_existed = 0
    errors = 0
    for customer_id in customer_ids:
        try:
            payload = build_customer_report(customer_id, report_date, engine=engine)
            if payload is None:
                continue
            with engine.begin() as conn:
                inserted = _upsert_report(conn, customer_id, report_date, payload)
            if inserted:
                created += 1
            else:
                already_existed += 1
        except Exception:  # noqa: BLE001 — عزل خطأ عميل واحد عن بقية الدورة
            logger.exception("فشل بناء/تسجيل تقرير العميل %s لتاريخ %s", customer_id, report_date)
            errors += 1

    result = {
        "ok": True,
        "date": report_date.isoformat(),
        "customers_active": len(customer_ids),
        "created": created,
        "already_existed": already_existed,
        "errors": errors,
    }
    logger.info("جولة التقارير اليومية انتهت: %s", result)
    return result
