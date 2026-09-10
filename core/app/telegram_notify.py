"""
Masar Core — إرسال مباشر لتيليجرام عبر Bot API (B8: إزالة n8n بالكامل من
تدفّق العمل — قرار أحمد النهائي: كل تفاعل تيليجرام يُدار داخل Core مباشرة،
بلا أي وسيط n8n). دالة إرسال منخفضة المستوى تُستخدم من كل وحدات الإرسال
الصادر (reports_relay.py، retention.py، skill_gap.py) + مساعدات بناء لوحة
مفاتيح 👎/🎉 وتقسيم النصوص الطويلة — منقولة حرفيًا من منطق عقدة "بناء
الرسائل" بركفلو n8n السابق (n8n/workflows/masar_daily_report_relay.json)
حتى يبقى تنسيق callback_data متوافقًا مع مُعالِج الأزرار الداخل (POST
/telegram/webhook/{token}، راجع app.telegram_api._handle_feedback_callback).

**توحيد ما بعد الدمج (B8)**: نُقل منطق بناء الرابط/إرسال httpx.post/فحص
ok:false الخام إلى app.telegram_client.send_message_sync — هذا الملف لم
يعد يستدعي httpx مباشرة إطلاقًا، فقط يحلّ التوكن من متغيّرات البيئة (منطق
outbound خاص: توكن افتراضي قابل للتجاوز عبر معامل/متغيّر بيئة مختلف —
telegram_client.get_customer_bot_client/get_admin_bot_client لا يوفّران
هذا التخصيص)، يضبط سياسة إعادة المحاولة (MAX_ATTEMPTS/تراجع أُسّي)، ويحوّل
TelegramAPIError الأدنى مستوى إلى TelegramSendError (عقد الاستثناءات الذي
تعتمده reports_relay.py/retention.py/skill_gap.py أصلًا). `telegram_client.py`
هو الآن المكان الوحيد بالمستودع الذي يبني طلب Bot API خامًا.

متغيرات البيئة:
    TELEGRAM_CUSTOMER_BOT_TOKEN — توكن بوت العميل (نفس اعتماد "Masar
        Customer Bot" سابقًا بـn8n) — الافتراضي لكل دوال هذا الملف (تقارير/
        تذكيرات/تحليل فجوة معرفية — كلها رسائل موجَّهة للعميل).
    TELEGRAM_ADMIN_BOT_TOKEN — توكن بوت الأدمن المنفصل (نفس اعتماد
        "Telegram Admin Bot" سابقًا) — يُستخدَم فقط عبر telegram_notify_admin.py
        (لا يُستخدَم مباشرة من هذا الملف).
"""
from __future__ import annotations

import logging
import os
import time

from app.telegram_client import DEFAULT_TIMEOUT_SECONDS, TelegramAPIError, send_message_sync

logger = logging.getLogger("masar.telegram_notify")

MAX_ATTEMPTS = 3

# هامش أمان تحت حد تيليجرام الفعلي لطول الرسالة (4096 حرفًا) — نفس القيمة
# المستخدَمة سابقًا بعقدة "بناء الرسائل" بركفلو n8n (masar_daily_report_relay.json).
DEFAULT_MAX_MESSAGE_CHARS = 3800

# حد تيليجرام الفعلي لعدد أزرار لوحة مفاتيح واحدة بأمان عملي — نفس القيمة
# المستخدَمة سابقًا بـn8n (`.slice(0, 50)` بعقدة "بناء الرسائل").
DEFAULT_MAX_KEYBOARD_BUTTONS = 50


class TelegramNotConfigured(RuntimeError):
    """توكن البوت المطلوب غير معرّف بمتغيرات البيئة."""


class TelegramSendError(RuntimeError):
    """رد تيليجرام غير ناجح (ok=false) بعد استنفاد كل المحاولات، أو فشل
    شبكة/JSON مستمر — كلاهما يُرفَعان هنا بنفس النوع (يُغلّفان
    TelegramAPIError الأدنى مستوى من app.telegram_client) حفاظًا على عقد
    الاستثناءات الذي كانت تعتمده reports_relay.py/retention.py/skill_gap.py
    قبل التوحيد."""


def _resolve_token(bot_token: str | None, env_var: str) -> str:
    token = bot_token or os.environ.get(env_var)
    if not token:
        raise TelegramNotConfigured(
            f"{env_var} غير معرّف بمتغيرات البيئة — لا يمكن الإرسال عبر تيليجرام"
        )
    return token


def send_message(
    chat_id: int | str,
    text: str,
    *,
    bot_token: str | None = None,
    token_env_var: str = "TELEGRAM_CUSTOMER_BOT_TOKEN",
    reply_markup: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """يرسل رسالة نصية واحدة عبر Telegram Bot API (sendMessage) — إعادة
    محاولة بتراجع أُسّي (1s، 2s) عند 429/5xx أو خطأ شبكة، حتى 3 محاولات
    إجمالًا (نفس نمط core/app/collectors/http_client.py)، منفَّذة الآن
    مركزيًا بـapp.telegram_client.send_message_sync. يرفع TelegramSendError
    إن رجع تيليجرام ok=false بعد كل المحاولات، أو فشل شبكة مستمر — كلاهما
    مسؤولية المُستدعي التقاطه (كل جولات scheduler_main.py تعزل خطأ عميل/
    تقرير واحد عن بقية الجولة، بنفس فلسفة بقية الملفات — راجع
    run_collector_round إلخ)."""
    token = _resolve_token(bot_token, token_env_var)
    try:
        return send_message_sync(
            chat_id,
            text,
            token=token,
            reply_markup=reply_markup,
            timeout=timeout,
            max_attempts=MAX_ATTEMPTS,
            sleep_fn=lambda attempt: time.sleep(2 ** (attempt - 1)),
        )
    except TelegramAPIError as exc:
        raise TelegramSendError(str(exc)) from exc


def split_message(text_body: str, max_len: int = DEFAULT_MAX_MESSAGE_CHARS) -> list[str]:
    """يقسّم نصًا طويلاً إلى أجزاء بحدود الأسطر (لا يقطع سطرًا منتصفه) —
    نفس منطق `splitText` بعقدة "بناء الرسائل" بركفلو n8n السابق حرفيًا."""
    if not text_body:
        return [text_body]
    lines = text_body.split("\n")
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > max_len and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [text_body]


def build_feedback_keyboard(
    customer_id: int, applications: list[dict], *, max_buttons: int = DEFAULT_MAX_KEYBOARD_BUTTONS
) -> dict | None:
    """يبني لوحة أزرار 👎/🎉 صفًّا لكل تقديم يحمل send_queue_id — أي رسالة
    ترسل قائمة تقديمات اليوم (التقرير اليومي حاليًا؛ أي رسالة مستقبلية
    مشابهة لاحقًا) تستدعي هذه الدالة لإرفاق الأزرار. تنسيق callback_data
    **يُحافَظ عليه حرفيًا** مطابقًا لركفلو n8n السابق (masar_daily_report_relay.json
    وmasar_feedback_callback.json):

        fb:<customer_id>:<send_queue_id>:<thumbs_down|celebrate>

    (customer_id مُضمَّن بالزر نفسه بدل الاعتماد على تحويل chat_id→customer_id
    — نفس تبرير n8n السابق: core/app/customers_api.py يملك فعليًا الآن نقطة
    GET /customers/by-telegram/{chat_id} لكن الإبقاء على نفس التنسيق هنا
    يمنع أي تعديل على معالج الأزرار الداخل. **بعد الدمج**: معالج هذا التنسيق
    فعليًا موجود الآن بـapp.telegram_api._handle_feedback_callback، يستدعي
    app.feedback_api.submit_feedback مباشرة). يرجع None إن لم تحمل أي فرصة
    send_queue_id (لا لوحة أزرار للرسالة — مطابق تمامًا لفروع reports.py قسم
    "استبعدنا لك" التي send_queue_id فيها دومًا None، راجع تعليق
    _fetch_today_exclusions بـreports.py)."""
    with_ids = [a for a in applications if a and a.get("send_queue_id") is not None][:max_buttons]
    if not with_ids:
        return None
    rows = []
    for app in with_ids:
        sqid = app["send_queue_id"]
        rows.append(
            [
                {"text": "👎", "callback_data": f"fb:{customer_id}:{sqid}:thumbs_down"},
                {"text": "🎉", "callback_data": f"fb:{customer_id}:{sqid}:celebrate"},
            ]
        )
    return {"inline_keyboard": rows}
