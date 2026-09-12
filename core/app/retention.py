"""
Masar Core — الاحتفاظ بالعملاء (Customer Retention) — B8: إزالة n8n بالكامل،
بديل مباشر لركفلو n8n/workflows/job-bot-customer-retention-auto__B2PYcIMOQN7i5VXA.json
(كان يعمل على جدول n8n Data Table "Customers" المهجور — لا صلة بجداول
Postgres الفعلية اليوم؛ هذا الملف يعيد بناء نفس **الفكرة** لكن فوق
customers/subscriptions الحقيقيتين، راجع migrations/versions/0004_b3_customers.py).

الفكرتان المنقولتان من n8n حرفيًا:
    1. تذكير تجديد — اشتراك على وشك الانتهاء (≤ 3 أيام) يُرسل له العميل
       تذكيرًا وديًا + إشعار أحمد، مرة واحدة في اليوم كحد أقصى (idempotent
       عبر subscriptions.reminder_sent_date — ترحيل 0013_b8_telegram_outbound).
    2. إشعار انتهاء — اشتراك انتقل فعليًا لحالة 'closed' يُرسل له العميل
       إشعارًا بتوقف البحث + إشعار أحمد، مرة واحدة فقط لكل فترة (idempotent
       عبر subscriptions.expiry_notified_at).

**فرق جوهري متعمّد عن n8n السابق (وليس نقصًا)**: نسخة n8n كانت تُقارن
subscriptionEndDate بالتاريخ مباشرة **وتُحدّث** subscriptionStatus بنفسها
("Expired") — أي تُقرّر انتهاء الاشتراك بمعزل عن أي منطق تمديد/ضمان. اليوم
core/app/guarantee.py هو المالك الوحيد لمتى/كيف تنتقل subscriptions.status
(active→extended→closed، بما فيها منطق مهلة الأداء الإلزامية وتمديد انقطاع
البريد والتعويض التناسبي — راجع توثيق ذلك الملف بالكامل). لو أعاد هذا
الملف نفس قرار "انتهى" بمعزل عن guarantee.py لتصادم القراران (مثال: عميل
بلغ ends_at لكنه لا يزال مؤهّلًا لمهلة يومين تلقائية بـguarantee.py — تعليمه
"منتهي" هنا قبل أوانه رسالة خاطئة صراحة للعميل). لذلك: هذا الملف **لا يكتب
أبدًا** إلى subscriptions.status — فقط يقرأ الحالة **التي قرّرها guarantee.py
فعلًا** (active/extended → تذكير اقتراب، closed → إشعار انتهاء) ويرسل
الإشعارات المناظرة فقط. هذا هو المقصود بـ"تجنّب ازدواج المنطق" بالتكليف،
مطبّق هنا كما طُبّق بملف reports.py مع reports_relay.py."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.discovery import get_engine
from app.pacing import to_riyadh_naive, utc_now
from app.telegram_admin_settings import support_whatsapp
from app.telegram_notify import send_message
from app.telegram_notify_admin import notify_admin

logger = logging.getLogger("masar.retention")

# نافذة التذكير: ≤3 أيام متبقية (وقت رياض) — نفس عتبة n8n السابق
# (`daysLeft <= 3`، عقدة Compute Subscription Status).
REMINDER_WINDOW_DAYS = 3

# B4/v2-B6 (12 سبتمبر): رقم واتساب الدعم كان مكتوبًا هنا حرفيًا (نفس القيمة
# بكل رسائل n8n السابقة) — استُبدل بـ`support_whatsapp()` الموحّدة
# (`app.telegram_admin_settings`، القراءة الوحيدة من `app_settings.support_whatsapp`
# الآن بكل الملفات، راجع docstring ذلك الملف: "B4/B6 القادمتين ستستوردانها").
_REMINDER_TEXT = (
    "مرحبًا {name} 🌟\n\n"
    "حبينا نذكرك بود قلب إن اشتراكك في مسار راح ينتهي خلال {days} يوم/أيام "
    "(بتاريخ {end_date}).\n\n"
    "إذا عجبتك خدماتنا وتحب تكمل معنا بدون انقطاع، اضغط 💳 تجديد الاشتراك "
    "أدناه، أو تواصل معنا على واتساب: {whatsapp}\n\n"
    "وإن شاء الله دايم في تيسير ورزق يوصلك 🤍"
)

_EXPIRY_TEXT = (
    "مرحبًا {name}،\n\n"
    "نود إعلامك أن اشتراكك في مسار انتهى، وتوقف البحث والتقديم التلقائي "
    "لك مؤقتًا.\n\n"
    "إذا تحب ترجع وتكمل معنا، اضغط 💳 تجديد الاشتراك أدناه، أو تواصل معنا "
    "على واتساب: {whatsapp}\n\n"
    "ونتمنى لك التوفيق والرزق الطيّب 🤍"
)

# B4/v2-B6: زرّا إجراء بدل الاكتفاء بنص واتساب فقط (قرار تصميم مطابق لأسلوب
# المشروع) — "menu:subscription"/"menu:contact" يعملان بلا أي كود توجيه
# جديد: `telegram_onboarding.handle_update` يوجّه هذين الاستدعاءين بالفعل
# لكلا مساري العميل النشط والموقوف/المنتهي (راجع `_handle_active_menu`/
# `_handle_inactive_customer`)، فضغطة الزر تُحسم وقتها بحالة العميل
# الفعلية عند الضغط، لا بحالته وقت إرسال رسالة التذكير/الانتهاء.
_ACTION_KEYBOARD = {
    "inline_keyboard": [
        [{"text": "💳 تجديد الاشتراك", "callback_data": "menu:subscription"}],
        [{"text": "📞 تواصل معنا", "callback_data": "menu:contact"}],
    ]
}


def _riyadh_today() -> date:
    return to_riyadh_naive(utc_now()).date()


def _fetch_customer(conn, customer_id: int) -> dict | None:
    row = conn.execute(
        text("SELECT id, name, phone, telegram_chat_id FROM customers WHERE id = :id"),
        {"id": customer_id},
    ).mappings().first()
    return dict(row) if row else None


def _fetch_reminder_candidates(conn) -> list[dict]:
    """كل اشتراك active/extended — الفلترة الفعلية (النافذة الزمنية + هل
    أُرسل التذكير اليوم؟) تقع بالكود بايثون بـ_send_reminders (لا بالـSQL)
    لأنها تحتاج تحويل الرياض عبر pacing.to_riyadh_naive (نفس مبرّر عدم
    استخدام `AT TIME ZONE` مباشرة بـSQL — راجع تعليق رأس core/app/pacing.py
    عن فخ التوقيت). دالة مستقلة (بدل استعلام inline) حتى تُختبر منطق
    الفلترة/التنسيق بـtest_retention.py بلا اتصال قاعدة بيانات حقيقي."""
    rows = conn.execute(
        text(
            """
            SELECT id, customer_id, ends_at, reminder_sent_date
            FROM subscriptions
            WHERE status IN ('active', 'extended')
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def _fetch_expiry_candidates(conn) -> list[dict]:
    """نفس مبرّر _fetch_reminder_candidates أعلاه — استعلام مستقل قابل
    للمحاكاة باختبارات الوحدة."""
    rows = conn.execute(
        text(
            """
            SELECT id, customer_id FROM subscriptions
            WHERE status = 'closed' AND expiry_notified_at IS NULL
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def _send_reminders(engine: Engine, today: date) -> dict:
    """اشتراكات active/extended تنتهي خلال REMINDER_WINDOW_DAYS يومًا، لم
    يُرسل لها تذكير اليوم بعد."""
    sent = 0
    skipped_no_chat = 0
    errors = 0

    with engine.begin() as conn:
        subs = _fetch_reminder_candidates(conn)

        for sub in subs:
            end_riyadh_date = to_riyadh_naive(sub["ends_at"]).date()
            days_left = (end_riyadh_date - today).days
            if days_left < 0 or days_left > REMINDER_WINDOW_DAYS:
                continue
            if sub["reminder_sent_date"] == today:
                continue

            customer = _fetch_customer(conn, sub["customer_id"])
            if not customer:
                continue

            chat_id = customer.get("telegram_chat_id")
            name = customer.get("name") or "عميلنا"
            if chat_id:
                try:
                    send_message(
                        chat_id,
                        _REMINDER_TEXT.format(
                            name=name, days=days_left, end_date=end_riyadh_date.isoformat(), whatsapp=support_whatsapp()
                        ),
                        reply_markup=_ACTION_KEYBOARD,
                    )
                except Exception:  # noqa: BLE001 — عزل خطأ عميل واحد عن بقية الجولة
                    logger.exception("فشل إرسال تذكير التجديد للعميل %s", sub["customer_id"])
                    errors += 1
                    continue
            else:
                skipped_no_chat += 1

            notify_admin(
                "🔔 تذكير تجديد اشتراك:\n\n"
                f"العميل: {name} (id={sub['customer_id']})\n"
                f"الهاتف: {customer.get('phone') or 'غير مسجّل'}\n"
                f"ينتهي الاشتراك خلال {days_left} يوم/أيام ({end_riyadh_date.isoformat()})\n\n"
                + ("تم إرسال تذكير للعميل أيضًا عبر تيليجرام. تابع معه للتجديد قبل الانتهاء."
                   if chat_id else
                   "تعذّر إرسال تذكير للعميل عبر تيليجرام (لا chat_id مربوط) — تابع معه مباشرة.")
            )

            conn.execute(
                text("UPDATE subscriptions SET reminder_sent_date = :d WHERE id = :id"),
                {"d": today, "id": sub["id"]},
            )
            sent += 1

    return {"reminders_sent": sent, "reminders_skipped_no_chat": skipped_no_chat, "reminders_errors": errors}


def _send_expiry_notices(engine: Engine) -> dict:
    """اشتراكات انتقلت لحالة 'closed' (قرار guarantee.py الحصري) ولم يُشعر
    عملاؤها/أحمد بعد."""
    sent = 0
    skipped_no_chat = 0
    errors = 0

    with engine.begin() as conn:
        subs = _fetch_expiry_candidates(conn)

        for sub in subs:
            customer = _fetch_customer(conn, sub["customer_id"])
            if not customer:
                # عميل محذوف أو غير موجود (نادر) — نُعلّم كمُشعَر حتى لا تتكرر
                # المحاولة للأبد على صفّ لا يمكن إكماله أصلًا.
                conn.execute(
                    text("UPDATE subscriptions SET expiry_notified_at = now() WHERE id = :id"),
                    {"id": sub["id"]},
                )
                continue

            chat_id = customer.get("telegram_chat_id")
            name = customer.get("name") or "عميلنا"
            if chat_id:
                try:
                    send_message(
                        chat_id, _EXPIRY_TEXT.format(name=name, whatsapp=support_whatsapp()), reply_markup=_ACTION_KEYBOARD
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("فشل إرسال إشعار انتهاء الاشتراك للعميل %s", sub["customer_id"])
                    errors += 1
                    continue
            else:
                skipped_no_chat += 1

            notify_admin(
                "⚠️ انتهى اشتراك عميل ولم يُجدّد بعد:\n\n"
                f"العميل: {name} (id={sub['customer_id']})\n"
                f"الهاتف: {customer.get('phone') or 'غير مسجّل'}\n\n"
                "توقّف البحث التلقائي له تلقائيًا (guarantee.py). إذا جدّد اشتراكه، "
                "أنشئ اشتراكًا جديدًا له بحسب المسار المعتاد."
            )

            conn.execute(
                text("UPDATE subscriptions SET expiry_notified_at = now() WHERE id = :id"),
                {"id": sub["id"]},
            )
            sent += 1

    return {"expiry_sent": sent, "expiry_skipped_no_chat": skipped_no_chat, "expiry_errors": errors}


def run_retention_round(engine: Engine | None = None, today: date | None = None) -> dict:
    """نقطة الدخول الرئيسية — تُستدعى يوميًا من scheduler_main.py (9 صباحًا
    الرياض، نفس توقيت n8n السابق حرفيًا: `triggerAtHour: 9`). idempotent
    بالكامل (لا يرفع استثناءً لخطأ عميل واحد، بنفس فلسفة بقية core/app/*.py)."""
    engine = engine or get_engine()
    today = today or _riyadh_today()

    reminders = _send_reminders(engine, today)
    expiries = _send_expiry_notices(engine)

    result = {"ok": True, "date": today.isoformat(), **reminders, **expiries}
    logger.info("جولة الاحتفاظ بالعملاء انتهت: %s", result)
    return result
