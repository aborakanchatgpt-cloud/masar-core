"""
Masar Core — تسليم التقرير اليومي عبر تيليجرام مباشرة (B8: إزالة n8n
بالكامل — بديل ركفلو n8n/workflows/masar_daily_report_relay.json الذي كان
يجلب /admin/reports/pending عبر HTTP كل 5 دقائق بين 19:00-21:30 الرياض
ويُرسل كل تقرير عبر تيليجرام ثم يُعلّمه مُسلَّمًا عبر /admin/reports/{id}/delivered).

بعد الإزالة: `run_relay_round()` يُشغَّل **داخل نفس عملية core-scheduler**
(core/app/scheduler_main.py) على نفس الجدولة الزمنية تقريبًا — يستدعي
reports.fetch_pending_reports/mark_report_delivered مباشرة كدوال بايثون (لا
طلب HTTP داخلي، الفرق الجوهري بعد إزالة n8n)، ويرسل كل تقرير عبر
telegram_notify.send_message.

يغطي هذا الملف أيضًا الشقّ **الصادر** من "التغذية الراجعة" (B5a البند 2،
n8n/workflows/masar_feedback_callback.json): إرفاق لوحة أزرار 👎/🎉 بآخر
جزء من رسالة كل تقرير — نفس مكان/توقيت إرفاق الأزرار حرفيًا بركفلو n8n
السابق (عقدة "بناء الرسائل" بمصفوفة `today_applications`، لا رسالة منفصلة
لكل تقديم). الشقّ **الداخل** (معالجة ضغطة الزر نفسها، callback_query) خارج
نطاق هذا الملف — يبنيه تيار العمل المتوازي عبر POST /telegram/webhook (راجع
core/app/telegram_admin.py المفترض هناك)؛ لا نلمسه من هنا.
"""
from __future__ import annotations

import logging

from sqlalchemy.engine import Engine

from app import reports
from app.discovery import get_engine
from app.telegram_notify import build_feedback_keyboard, split_message, send_message

logger = logging.getLogger("masar.reports_relay")

# هامش أمان تحت حد تيليجرام (4096 حرفًا) — نفس القيمة المستخدَمة سابقًا
# بـn8n (masar_daily_report_relay.json، ثابت MAX_LEN بعقدة "بناء الرسائل").
MAX_MESSAGE_CHARS = 3800

# نفس حجم صفحة /admin/reports/pending الافتراضي بـn8n (queryParameter limit=200).
DEFAULT_BATCH_LIMIT = 200


def run_relay_round(engine: Engine | None = None, *, limit: int = DEFAULT_BATCH_LIMIT) -> dict:
    """نقطة الدخول الرئيسية — تُستدعى من scheduler_main.py. لكل تقرير
    status='queued': تجلب chat_id العميل، تُرسل النص (مُقسَّمًا إن لزم) مع
    لوحة أزرار 👎/🎉 على الجزء الأخير فقط (نفس منطق n8n: `idx === chunks.length - 1`)،
    ثم تُعلّمه مُسلَّمًا (channel='telegram'). عميل بلا telegram_chat_id
    (لم يُكمل ربط تيليجرام بعد) يُتخطّى بصمت — بلا تعليم كمُسلَّم (يبقى
    queued بانتظار ربط لاحق، يُعاد محاولته بالجولة التالية تلقائيًا — نفس
    السلوك حرفيًا بفرع "تخطي؟" بركفلو n8n السابق، فقط بلا سجلّ ملخّص
    منفصل هنا، مُستبدَل بـ`skipped_no_chat` بنتيجة الجولة)."""
    engine = engine or get_engine()
    pending = reports.fetch_pending_reports(engine, limit=limit)

    delivered = 0
    skipped_no_chat = 0
    errors = 0

    for row in pending:
        report_id = row["id"]
        customer_id = row["customer_id"]
        try:
            chat_id = reports.fetch_customer_chat_id(engine, customer_id)
            if chat_id is None:
                skipped_no_chat += 1
                logger.info(
                    "تخطي تسليم التقرير %s للعميل %s — لا telegram_chat_id مربوط بعد",
                    report_id,
                    customer_id,
                )
                continue

            payload = row.get("payload") or {}
            full_text = str(payload.get("text") or "").strip() or "لا يوجد نص تقرير."
            chunks = split_message(full_text, MAX_MESSAGE_CHARS)
            applications = payload.get("today_applications") or []
            keyboard = build_feedback_keyboard(customer_id, applications)

            for idx, chunk in enumerate(chunks):
                is_last = idx == len(chunks) - 1
                send_message(chat_id, chunk, reply_markup=keyboard if is_last else None)

            marked = reports.mark_report_delivered(engine, report_id, "telegram")
            if marked is None:
                # نادر جدًا (سباق حذف/تعديل يدوي بين الجلب والتعليم) — الرسالة
                # وصلت فعليًا للعميل، فقط التعليم فشل؛ يُسجَّل كخطأ للمتابعة
                # اليدوية بدل إعادة إرسال مكرّرة للعميل بالجولة القادمة.
                logger.error("أُرسل التقرير %s فعليًا لكن تعذّر تعليمه مُسلَّمًا (لم يعد موجودًا؟)", report_id)
                errors += 1
                continue

            delivered += 1
        except Exception:  # noqa: BLE001 — عزل خطأ تقرير واحد عن بقية الجولة
            logger.exception("فشل تسليم التقرير %s للعميل %s عبر تيليجرام", report_id, customer_id)
            errors += 1

    result = {
        "ok": True,
        "pending_count": len(pending),
        "delivered": delivered,
        "skipped_no_chat": skipped_no_chat,
        "errors": errors,
    }
    logger.info("جولة تسليم التقارير اليومية عبر تيليجرام انتهت: %s", result)
    return result
