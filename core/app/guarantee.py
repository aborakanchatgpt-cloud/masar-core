"""
Masar Core — دفتر الضمان/التعويض (B5a البند 3، الدليل §3.12، PLAN.md B5).

القاعدة الثابتة (غير قابلة للتفاوض، الدليل §0 البند 5 + التكليف): الهدف
الشهري = 510 تقديمًا **مُحتسَبًا** (المرتد لا يُحتسب أبدًا). إن لم يبلغ
العميل هدفه عند نهاية فترة اشتراكه: تمديد يومين تلقائي أولًا؛ إن بقي عجزًا
بعد التمديد → تعويض تناسبي (سعر الاشتراك ÷ 510 لكل تقديم ناقص)، **يُسجّل
فقط** بانتظار اعتماد/تحويل يدوي من المالك (لا تحويل تلقائي أبدًا — راجع
`POST /admin/guarantee/{id}/settle`). انقطاع بسبب بريد العميل نفسه (مفصول/
فشل تحقّق) **يُمدّد الفترة بدل التعويض** — طالما الصندوق معطوب لا يصل
التقييم لمرحلة التعويض إطلاقًا مهما تكرّر التقييم اليومي.

**رصيد/باقات (لا اشتراك نشط):** `status='na'` — الضمان مبني على اشتراك
`subscriptions` حصرًا (منحة 510 + مدة محدَّدة)؛ عميل رصيد مسبق (credits) لا
مفهوم "فترة" له إطلاقًا (الرصيد لا ينتهي، الدليل §0 البند 6) فلا ينطبق عليه
ضمان.

ملاحظة نطاق موثَّقة (NEEDS-OWNER — راجع docs/reports/B5a-executor.md): لا
جدول يسجّل **تاريخ** حالة `mail_links` يومًا بيوم (الجدول يحمل الحالة
الحالية فقط) — فلا يمكن حساب `customer_caused_days` تاريخيًا بدقة (كما يصف
الدليل §3.12 حرفيًا: "shortfall = 510 − sent_counted − customer_caused_days×17").
هذا الملف يطبّق تفسيرًا مبسّطًا ومحافظًا مكافئًا في الأثر العملي: **طالما
`mail_links.status != 'ok'` وقت التقييم اليومي، التقييم يمدّد الفترة يومًا
إضافيًا ولا يتقدم لمرحلة التعويض إطلاقًا** — يحقّق نفس نتيجة القاعدة
("انقطاع بسبب العميل يُمدّد بدل تعويض") بلا حاجة لجدول تاريخي جديد، لكنه
لا يحسب `customer_caused_days` كعدد صريح بالتقرير (`shortfall` يبقى NULL
طوال فترة الانقطاع، لا يُطرح تناسبيًا من هدف مؤجّل).

تصحيح B5c (docs/reports/B5a-B2b-review.md، القسم 3.2، عيب [major]): عدّاد
التمديد كان عمودًا واحدًا مشتركًا (`extension_days`) بين تمديد الانقطاع
(`OUTAGE_EXTENSION_DAYS` — يتكرر يوميًا طالما البريد معطوب) وتمديد مهلة
الأداء الإلزامية (`GRACE_EXTENSION_DAYS=2`، مرة واحدة لكل فترة) — فإن تراكم
انقطاع بريد ≥2 يوم وحده (بلا أي مهلة أداء فعلية مُنحت)، ثم أُصلح البريد
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
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.discovery import get_engine
from app.telegram_notify_admin import notify_admin

logger = logging.getLogger("masar.guarantee")

MONTHLY_TARGET = 510
GRACE_EXTENSION_DAYS = 2
OUTAGE_EXTENSION_DAYS = 1


def _fetch_customer(conn: Connection, customer_id: int) -> dict | None:
    row = conn.execute(
        text("SELECT id, name, status, price_sar FROM customers WHERE id = :id"), {"id": customer_id}
    ).mappings().first()
    return dict(row) if row else None


def _fetch_due_subscription(conn: Connection, customer_id: int, now_utc: datetime) -> dict | None:
    """آخر اشتراك بحالة active/extended **انتهت فترته فعليًا** (ends_at <=
    الآن) لهذا العميل — الوحيد المؤهّل للتقييم اليوم. اشتراك لم تنتهِ فترته
    بعد يرجع None (لا شيء لتقييمه — لا خطأ)."""
    row = conn.execute(
        text(
            """
            SELECT id, customer_id, product_code, starts_at, ends_at, status
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
                      refund_amount, status, created_at, updated_at
            """
        ),
        values,
    ).mappings().first()
    return dict(row)


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
                refund_amount=None,
                status="extended",
            )
            return {"ok": True, "customer_id": customer_id, "reason": "mail_link_broken", **ledger}

        if not already_had_grace:
            # تمديد يومين تلقائي (لمرة واحدة لكل فترة) — الدليل §0 البند 5.
            # يُمنح دومًا هنا (بصرف النظر عن أي انقطاع بريد سابق استهلك أيام
            # outage_extension_days منفصلة) — هذا هو الإصلاح الفعلي لعيب B5c.
            new_grace_days = prior_grace_days + GRACE_EXTENSION_DAYS
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
                refund_amount=None,
                status="extended",
            )
            return {"ok": True, "customer_id": customer_id, "reason": "grace_period_granted", **ledger}

        # مهلة الأداء الإلزامية استُهلِكت فعليًا (grace_extension_days >= 2)
        # ولا يزال قاصرًا، والبريد سليم الآن — تعويض تناسبي (يحتاج اعتماد
        # المالك، لا تحويل تلقائي أبدًا).
        shortfall = MONTHLY_TARGET - counted_sent
        price_sar = customer.get("price_sar")
        refund_amount = None
        if price_sar is not None:
            refund_amount = round(float(shortfall) * float(price_sar) / MONTHLY_TARGET, 2)

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
            refund_amount=refund_amount,
            status="refund_pending",
        )
        conn.execute(text("UPDATE subscriptions SET status = 'closed' WHERE id = :id"), {"id": sub["id"]})
        return {"ok": True, "customer_id": customer_id, "reason": "shortfall_after_grace", **ledger}


def run_guarantee_round(engine: Engine | None = None, now: datetime | None = None) -> dict:
    """نقطة الدخول الرئيسية — يُشغَّل يوميًا 00:30 الرياض (scheduler_main.py)
    لكل عميل لديه اشتراك بحالة active/extended انتهت فترته فعليًا (بلا
    قيد status='active' على العميل نفسه عمدًا — عميل موقوف قد لا يزال له
    اشتراك واجب التقييم/التمديد/التعويض)."""
    engine = engine or get_engine()
    now = now or datetime.now(timezone.utc)

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
            if status == "refund_pending":
                # B8 (إزالة n8n): تنبيه أحمد مباشرة عبر تيليجرام — تعويض
                # ضمان معلّق يحتاج اعتماده اليدوي دومًا (لا تحويل تلقائي
                # أبدًا، راجع توثيق أعلى الملف). subscriptions.status يصبح
                # 'closed' فورًا بنفس الفرع (راجع أعلاه) فهذا الشرط يتحقق
                # مرة واحدة بالضبط لكل فترة (لا تنبيه مكرر بتشغيلات لاحقة —
                # run_guarantee_round لا يعيد التقاط اشتراك closed أصلًا).
                # notify_admin دالة best-effort لا ترفع استثناءً أبدًا (راجع
                # توثيقها بـtelegram_notify_admin.py) — لا خطر إضافي هنا.
                notify_admin(
                    "💰 تعويض ضمان معلّق يحتاج اعتمادك:\n\n"
                    f"العميل: {result.get('customer_id')}\n"
                    f"العجز: {result.get('shortfall')} تقديمًا\n"
                    f"المبلغ المقترح: {result.get('refund_amount')} ريال\n\n"
                    f"اعتمد عبر POST /admin/guarantee/{result.get('id')}/settle بعد التحويل اليدوي."
                )
        except Exception:  # noqa: BLE001 — عزل خطأ عميل واحد عن بقية الدورة
            logger.exception("فشل تقييم فترة الضمان للعميل %s", customer_id)
            errors += 1

    result = {
        "ok": True,
        "due_customers": len(customer_ids),
        "evaluated": evaluated,
        "errors": errors,
        "by_status": by_status,
    }
    logger.info("جولة تقييم الضمان انتهت: %s", result)
    return result
