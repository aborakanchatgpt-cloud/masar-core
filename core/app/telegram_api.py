"""
Masar Core — نقطة استقبال Webhook تيليجرام الموحَّدة (B8: إزالة n8n نهائيًا
من مسار الدخول الوارد — بديل مباشر لعقدتي Telegram Trigger اللتين كانتا
تعيشان بـn8n لكل من بوت العملاء "مسار" وبوت الأدمن الخاص).

    POST /telegram/webhook/{token}   نقطة الويب هوك الوحيدة لكلا البوتين

**بلا أي طبقة auth.require_admin_token هنا عمدًا** — تيليجرام لا يقدر يرسل
ترويسة Authorization: Bearer بأي شكل (لا اعتماد HTTP قابل للتخصيص بواجهة
setWebhook). البديل: توكن سرّي بمقطع المسار نفسه (نفس نمط
app.mcp_bridge./mcp/{token} بالضبط — توكن خاطئ ⇐ 404 بلا كشف حتى بوجود
النقطة، غياب التوكن بالبيئة ⇐ 503 تعطيل كامل، مقارنة بزمن ثابت). طبقة دفاع
إضافية اختيارية: ترويسة X-Telegram-Bot-Api-Secret-Token القياسية التي
يُرسلها تيليجرام تلقائيًا إن ضُبطت بنفس القيمة أثناء setWebhook (secret_token)
— إن وصلت هذه الترويسة يجب أن تطابق التوكن أيضًا، دفاعًا بالعمق فوق توكن
المسار وحده (لا اعتماد على طبقة واحدة فقط).

**توجيه بوتين على مسار واحد بمعرّف الدردشة فقط، لا بتوكن البوت** — تيليجرام
لا يُضمّن أي معرّف بوت بجسم Update نفسه (نفس JSON مهما كان البوت)، فالتفريق
الوحيد الممكن هنا هو: chat_id == MASAR_OWNER_CHAT_ID ⇐ بوت الأدمن،
غير ذلك ⇐ بوت العملاء. هذا صحيح عمليًا لأن chat_id بمحادثة خاصة هو معرّف
حساب Telegram الشخصي لصاحبها (لا يتغيّر بين البوتات) — **افتراض تصميم
مهم**: أحمد يتواصل دومًا عبر بوت الأدمن الخاص، لا بوت "مسار" العام (إن
جرّب بوت العملاء بنفسه سيُعامَل كأدمن لا كعميل — سلوك مقصود، لا عطل).

**الشقّ الصادر (B8، بعد الدمج مع تيار reports_relay.py/telegram_notify.py)**:
كل رسالة تقرير يومي يُرفَق بها زرّا 👎/🎉 (app.telegram_notify.build_feedback_keyboard)
بتنسيق callback_data ثابت:

    fb:<customer_id>:<send_queue_id>:<thumbs_down|celebrate>

هذا التنسيق يُعتَرض هنا **قبل** التوجيه لبوت الأدمن/العملاء العادي (قبل حتى
تأكيد الاستلام المركزي أدناه — _handle_feedback_callback يؤكّد بنص تأكيد
مخصَّص بنفسه) لأن الضغطة تصل دومًا من محادثة عميل عبر بوت العميل (التقرير
يُرسَل عبر TELEGRAM_CUSTOMER_BOT_TOKEN حصرًا)، بصرف النظر عن chat_id —
يستدعي app.feedback_api.submit_feedback مباشرة (نفس نمط telegram_admin.py:
استدعاء الدالة الأساسية بايثون مباشرة، لا طلب HTTP داخلي، متجاوزًا
Depends(require_admin_token) على مستوى الراوتر عمدًا — البوت نفسه بوابة
التحقق هنا، تمامًا كما تفعل بقية أوامر telegram_admin.py). **customer_id
المُضمَّن بالزر مكشوف وقابل للتخمين (رقم تسلسلي)، فلا يُوثَق به بمفرده
أبدًا** — _handle_feedback_callback يتحقّق أولًا عبر
customers_api.get_customer_by_telegram(chat_id) أن صاحب chat_id الذي
أرسل الضغطة فعليًا هو نفسه customer_id المكتوب بالزر، ويرفض بصمت (رد عام
"غير متاح" بلا كشف السبب) عند أي عدم تطابق أو محادثة غير مربوطة.
"""
from __future__ import annotations

import hmac
import logging
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app import customers_api, feedback_api, telegram_admin, telegram_onboarding
from app.telegram_admin import is_owner_chat
from app.telegram_client import (
    ChatEvent,
    TelegramAPIError,
    TelegramClient,
    extract_chat_event,
    get_admin_bot_client,
    get_customer_bot_client,
)

logger = logging.getLogger("masar.telegram_api")

router = APIRouter(tags=["telegram"])

FEEDBACK_CALLBACK_PREFIX = "fb:"

_FEEDBACK_CONFIRM_TEXT = {
    "celebrate": "🎉 شكرًا لتقييمك! يسعدنا نسمع هذا.",
    "thumbs_down": "👎 تم تسجيل ملاحظتك — ما راح نرسل لك فرص من هذي الشركة مرة ثانية.",
}


def _webhook_secret() -> str | None:
    tok = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    return tok or None


def _constant_time_eq(a: str, b: str) -> bool:
    try:
        return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
    except Exception:  # noqa: BLE001
        return False


@router.post("/telegram/webhook/{token}")
async def telegram_webhook(token: str, request: Request) -> JSONResponse:
    expected = _webhook_secret()
    if not expected:
        return JSONResponse(status_code=503, content={"error": "telegram webhook disabled"})
    if not _constant_time_eq(token, expected):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})

    secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret_header is not None and not _constant_time_eq(secret_header, expected):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})

    try:
        update = await request.json()
    except Exception:  # noqa: BLE001 — جسم غير صالح: نتجاهله بهدوء (200 يمنع إعادة محاولة عبثية من Telegram)
        logger.warning("جسم Update غير صالح (ليس JSON) — تجاهل")
        return JSONResponse(status_code=200, content={"ok": True})

    if not isinstance(update, dict):
        return JSONResponse(status_code=200, content={"ok": True})

    event = extract_chat_event(update)
    if event is None:
        return JSONResponse(status_code=200, content={"ok": True})

    # الشقّ الصادر (B8، راجع docstring رأس الملف): ضغطة زر تغذية راجعة على
    # تقرير يومي — تُعترَض هنا قبل أي توجيه أدمن/عميل عادي، وقبل تأكيد
    # الاستلام المركزي أدناه (لها تأكيدها المخصَّص الخاص).
    if event.is_callback and event.callback_data.startswith(FEEDBACK_CALLBACK_PREFIX):
        feedback_client = get_customer_bot_client()
        if feedback_client is None:
            logger.warning(
                "توكن بوت customer غير معرَّف بالبيئة — تعذّر معالجة ضغطة تغذية راجعة (chat_id=%s)",
                event.chat_id,
            )
            return JSONResponse(status_code=200, content={"ok": True})
        try:
            await _handle_feedback_callback(event, feedback_client)
        except Exception:  # noqa: BLE001 — نفس فلسفة الغلاف العام أدناه: لا نُسقط استجابة تيليجرام أبدًا
            logger.exception("فشل غير متوقع أثناء معالجة ضغطة تغذية راجعة (chat_id=%s)", event.chat_id)
        return JSONResponse(status_code=200, content={"ok": True})

    if is_owner_chat(event.chat_id):
        client = get_admin_bot_client()
        handler = telegram_admin.handle_update
        client_label = "admin"
    else:
        client = get_customer_bot_client()
        handler = telegram_onboarding.handle_update
        client_label = "customer"

    if client is None:
        logger.warning("توكن بوت %s غير معرَّف بالبيئة — تجاهل تحديث وارد (chat_id=%s)", client_label, event.chat_id)
        return JSONResponse(status_code=200, content={"ok": True})

    # تأكيد استلام ضغطة الزر فورًا (Telegram يتوقّعه خلال ~30 ثانية من كل
    # callback_query وإلا بقيت شارة "جاري التحميل" عالقة عند المستخدم) —
    # مركزيًا هنا قبل التوجيه، بدل تكراره بكل من telegram_admin/telegram_onboarding.
    if event.is_callback and event.callback_query_id:
        try:
            await client.answer_callback_query(event.callback_query_id)
        except Exception:  # noqa: BLE001 — فشل التأكيد لا يجب أن يُسقط معالجة الضغطة نفسها
            logger.warning("تعذّر تأكيد استلام ضغطة زر (chat_id=%s)", event.chat_id)

    # أي فشل غير متوقّع أثناء معالجة تحديث واحد يجب ألا يُسقط الاستجابة
    # لتيليجرام (500 هنا يعني إعادة محاولة تلقائية متكررة من تيليجرام لنفس
    # التحديث الفاشل) — يُسجَّل كاملًا (traceback) ونُرجع 200 دومًا.
    try:
        await handler(update, event, client)
    except Exception:  # noqa: BLE001
        logger.exception("فشل غير متوقع أثناء معالجة تحديث Telegram (chat_id=%s, bot=%s)", event.chat_id, client_label)

    return JSONResponse(status_code=200, content={"ok": True})


# ---------------------------------------------------------------------------
# معالج ضغطة تغذية راجعة (fb:<customer_id>:<send_queue_id>:<kind>) — الشقّ
# الداخل المقابل لـapp.telegram_notify.build_feedback_keyboard (الشقّ
# الصادر). يستدعي app.feedback_api.submit_feedback مباشرة كدالة بايثون
# (متجاوزًا Depends(require_admin_token) على مستوى الراوتر عمدًا — نفس نمط
# app.telegram_admin مع customers_api/overview_api/reports_api/guarantee_api:
# البوت نفسه هو بوابة التحقق، لا حاجة لتوكن إداري HTTP لضغطة زر عميل حقيقي).
# ---------------------------------------------------------------------------


async def _safe_answer_callback(
    client: TelegramClient, callback_query_id: str | None, text: str, *, show_alert: bool = False
) -> None:
    if not callback_query_id:
        return
    try:
        await client.answer_callback_query(callback_query_id, text=text, show_alert=show_alert)
    except TelegramAPIError:
        logger.warning("تعذّر إرسال تأكيد ضغطة تغذية راجعة لتيليجرام (callback_query_id=%s)", callback_query_id)


async def _handle_feedback_callback(event: ChatEvent, client: TelegramClient) -> None:
    """يفكّك callback_data بصيغة `fb:<customer_id>:<send_queue_id>:<kind>`،
    **يتحقّق أولًا أن customer_id المُضمَّن بالزر يخصّ فعليًا صاحب chat_id
    الذي أرسل الضغطة** (راجع REVIEW.md البند 8.1 — customer_id مكشوف
    وقابل للتخمين بنص الزر نفسه، فلا يُوثَق به وحده أبدًا؛ التحقّق عبر
    customers_api.get_customer_by_telegram(chat_id) الموجودة أصلًا
    بالمستودع)، ثم يستدعي feedback_api.submit_feedback (نفس منطق
    `POST /customers/{id}/feedback` حرفيًا — idempotent، راجع توثيق تلك
    الدالة)، يؤكّد الاستلام للعميل بنص عربي مختصر حسب kind عبر
    answerCallbackQuery، ثم يُزيل لوحة الأزرار من الرسالة الأصلية
    (best-effort — فشل الإزالة لا يُسقط أي شيء، التقييم نفسه سُجِّل فعلًا
    بقاعدة البيانات بحلول هذه النقطة).

    عدم تطابق (أو محادثة غير مربوطة بأي عميل أصلًا) ⇐ رفض بصمت: تأكيد
    عام "غير متاح" بلا كشف السبب (لا "customer id mismatch" ولا أي تلميح
    يفيد بأن هذا تخمين صحيح جزئيًا)، وتحذير بالسجلّ فقط للتتبّع الداخلي."""
    parts = event.callback_data.split(":")
    if len(parts) != 4:
        logger.warning("callback_data تغذية راجعة بصيغة غير متوقعة: %r", event.callback_data)
        await _safe_answer_callback(client, event.callback_query_id, "⚠️ طلب غير صالح")
        return

    _, customer_id_str, send_queue_id_str, kind = parts
    try:
        customer_id = int(customer_id_str)
        send_queue_id = int(send_queue_id_str)
    except ValueError:
        logger.warning("معرّفات غير رقمية بـcallback_data تغذية راجعة: %r", event.callback_data)
        await _safe_answer_callback(client, event.callback_query_id, "⚠️ طلب غير صالح")
        return

    if kind not in feedback_api.FEEDBACK_KINDS:
        logger.warning("kind غير معروف بـcallback_data تغذية راجعة: %r", kind)
        await _safe_answer_callback(client, event.callback_query_id, "⚠️ طلب غير صالح")
        return

    # تحقّق الهوية: customer_id المكتوب بالزر ضد صاحب chat_id الفعلي —
    # راجع docstring الدالة أعلاه وREVIEW.md البند 8.1. lookup فاشل (chat_id
    # غير مربوط بأي عميل) أو customer_id غير مطابق ⇐ رفض بصمت، نفس الرد
    # العام لكل الحالات ("غير متاح")، بلا كشف أيّ من السببين للطرف المرسل.
    try:
        lookup = await customers_api.get_customer_by_telegram(event.chat_id)
    except HTTPException:
        logger.warning(
            "ضغطة تغذية راجعة من محادثة غير مربوطة بأي عميل مسجَّل "
            "(chat_id=%s, customer_id بالزر=%s, send_queue_id=%s) — رفض",
            event.chat_id, customer_id, send_queue_id,
        )
        await _safe_answer_callback(client, event.callback_query_id, "⚠️ غير متاح")
        return

    if lookup["customer_id"] != customer_id:
        logger.warning(
            "عدم تطابق هوية بضغطة تغذية راجعة — customer_id بالزر لا يخصّ "
            "صاحب chat_id الفعلي (chat_id=%s, customer_id بالزر=%s, "
            "customer_id الفعلي لهذه المحادثة=%s, send_queue_id=%s) — رفض "
            "(احتمال تلاعب بـcallback_data)",
            event.chat_id, customer_id, lookup["customer_id"], send_queue_id,
        )
        await _safe_answer_callback(client, event.callback_query_id, "⚠️ غير متاح")
        return

    try:
        await feedback_api.submit_feedback(
            customer_id, feedback_api.FeedbackRequest(send_queue_id=send_queue_id, kind=kind)
        )
    except HTTPException as exc:
        logger.warning(
            "تعذّر تسجيل تغذية راجعة (customer_id=%s, send_queue_id=%s): %s",
            customer_id, send_queue_id, exc.detail,
        )
        await _safe_answer_callback(client, event.callback_query_id, f"⚠️ تعذّر: {exc.detail}", show_alert=True)
        return

    await _safe_answer_callback(client, event.callback_query_id, _FEEDBACK_CONFIRM_TEXT.get(kind, "✅ تم"))

    if event.message_id is not None:
        try:
            await client.edit_message_reply_markup(event.chat_id, event.message_id, buttons=[])
        except TelegramAPIError:
            logger.warning(
                "تعذّر إزالة أزرار التغذية الراجعة من الرسالة الأصلية (chat_id=%s, message_id=%s)",
                event.chat_id, event.message_id,
            )


@router.get("/telegram/webhook/{token}")
async def telegram_webhook_get(token: str) -> JSONResponse:
    expected = _webhook_secret()
    if not expected:
        return JSONResponse(status_code=503, content={"error": "telegram webhook disabled"})
    if not _constant_time_eq(token, expected):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    return JSONResponse(status_code=405, content={"detail": "Method Not Allowed — use POST"})
