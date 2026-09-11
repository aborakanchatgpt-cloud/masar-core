"""
Masar Core — عميل Telegram Bot API خفيف (بديل n8n الكامل لتفاعل تيليجرام،
B8: إزالة n8n نهائيًا من مسار الدخول الوارد — الدليل: "كل تفاعل تيليجرام
يُدار داخل Core نفسها، بلا n8n إطلاقًا").

عمدًا بلا أي مكتبة تيليجرام خارجية ثقيلة (python-telegram-bot / aiogram) —
فقط httpx (موجودة أصلًا بمتطلبات المشروع، نفس ما يستخدمه cv_builder.py
لـGotenberg) فوق طبقة Bot API HTTP البسيطة. يغطي فقط العمليات التي
يحتاجها البوتان الحاليان + الشقّ الصادر بعد الدمج: sendMessage (بلوحة
أزرار inline أو بلا)، answerCallbackQuery، editMessageText،
editMessageReplyMarkup، وgetFile/تنزيل ملف — إن احتاج لاحقًا sendDocument
أو غيرها يُضاف بنفس النمط.

كل استدعاء يبني عميل httpx.AsyncClient قصير العمر خاصًا به (لا اتصال دائم
مفتوح بين الرسائل — نفس فلسفة app.cv_builder.convert_html_to_pdf، حمل
الرسائل هنا خفيف جدًا مقارنة بفائدة تبسيط دورة الحياة). أي فشل شبكة/HTTP
يُرفع كـTelegramAPIError برسالة واضحة — **المستدعي** (telegram_api.py) هو
من يقرر التسامح معه (تسجيل فقط بدل إسقاط معالجة التحديث بالكامل)، فلا نُخفي
الخطأ هنا أبدًا.

**توحيد ما بعد الدمج (B8، تيّارا الوارد/الصادر)**: هذا الملف هو العميل
الوحيد الذي يبني رابط/جسم/فحص `ok` لطلب Bot API خامّ بالمستودع كاملًا —
لا مكان آخر يستدعي `httpx` مباشرة تجاه `api.telegram.org`. تيّار الوارد
(الويب هوك، `telegram_onboarding.py`/`telegram_admin.py`) غير متزامن
بطبيعته (مسار FastAPI) فيستخدم `TelegramClient` أعلاه مباشرة. تيّار
الصادر (`telegram_notify.py` ومن خلفه `reports_relay.py`/`retention.py`/
`skill_gap.py`) يعمل داخل `core-scheduler` — عملية `APScheduler.BlockingScheduler`
متزامنة بالكامل بلا حلقة أحداث asyncio مفتوحة، فتغليف كل رسالة بـ`asyncio.run`
منفصل هنا كان سيضيف تكلفة/تعقيدًا بلا فائدة حقيقية لحلقة إرسال ساخنة (حتى
200+ تقرير بجولة واحدة). الحل: `send_message_sync` أدناه — دالة متزامنة
مستقلة تُعيد استخدام **نفس** منطق بناء الرابط وفحص `ok`/الوصف (لا نسخ
ثانية من هذا المنطق)، مع دعم إعادة محاولة اختيارية (`max_attempts`) لأن
تيّار الصادر يحتاجها فعليًا (دفعات، 429 وارد محتمل) بعكس تيّار الويب هوك
الذي يعالج تحديثًا واحدًا في كل مرة ولا يحتاج نفس درجة المقاومة. `telegram_notify.py`
يستدعيها بدل تكرار حلقة httpx.post الخاصة به.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable

import httpx

logger = logging.getLogger("masar.telegram_client")

DEFAULT_TIMEOUT_SECONDS = 15.0
API_BASE_URL = "https://api.telegram.org"

# B9/A3: الحد الرسمي لطول نص sendMessage بواجهة Telegram Bot API هو 4096
# حرفًا — أي رسالة أطول كانت تُرفَض بالكامل (ok:false) فترفع TelegramAPIError
# ولا يصل أي شيء للمستلم إطلاقًا. أوضح مثال اكتُشف به هذا (مراجعة الجاهزية
# قبل التجربة الحيّة، 2026-09-11): زر "📨 تقرير عميل" بالبوت الإداري، الذي
# قد ينتج نصًّا يتجاوز الحد بسهولة لعميل نشط بيوم مزدحم. SAFE_SPLIT_LIMIT
# أقل من الحد الرسمي بهامش أمان (لا نعتمد على 4096 بالضبط، تحسّبًا لعدّ
# Telegram الأحرف بطريقة UTF-16 لبعض الرموز التعبيرية النادرة).
TELEGRAM_MESSAGE_LIMIT = 4096
SAFE_SPLIT_LIMIT = 3500

# أكواد حالة HTTP يُستحسَن إعادة المحاولة عندها (تحدّد المعدّل/فشل مؤقت من
# طرف تيليجرام) — تُستخدَم فقط من send_message_sync (max_attempts>1)؛
# TelegramClient._call غير المتزامنة أعلاه تُبقي محاولة واحدة فقط عمدًا
# (تيار الويب هوك يُفضّل فشلًا سريعًا مُسجَّلًا على تعليق معالجة تحديث تيليجرام
# بإعادة محاولات — راجع ملاحظة التوحيد أعلى الملف).
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class TelegramAPIError(RuntimeError):
    """فشل استدعاء Telegram Bot API — إما خطأ شبكة/HTTP، أو ok:false بجسم الرد."""


def split_message_text(text: str, limit: int | None = None) -> list[str]:
    """B9/A3: يقسّم نصًّا طويلًا إلى قِطع كل منها ≤ `limit` حرفًا — يُستخدَم من
    `TelegramClient.send_message`/`send_message_sync` أدناه قبل أي استدعاء
    فعلي لـsendMessage، حتى لا يُرفَض نص طويل بالكامل (راجع SAFE_SPLIT_LIMIT
    أعلاه لتفاصيل المشكلة).

    `limit` افتراضيًا None فيُقرَأ SAFE_SPLIT_LIMIT من متغيّر الوحدة (module
    global) *عند كل استدعاء* لا مرّة واحدة وقت تعريف الدالة (تجنّبًا لفخّ
    Python الشهير: قيمة افتراضية لبارامتر تُحسَب مرّة واحدة فقط عند
    `def` — لو كانت `limit: int = SAFE_SPLIT_LIMIT` مباشرة، لَما أمكن أي
    اختبار/تهيئة تغيير SAFE_SPLIT_LIMIT لاحقًا وتوقّع أثره هنا).

    يحاول القسمة عند حدود منطقية بالترتيب: فقرة فارغة (`\\n\\n`) ثم سطر
    (`\\n`) — فقط إن وقعت نقطة القسمة بالنصف الثاني من النافذة الحالية
    (>= limit/2)، تجنّبًا لقِطع مجهرية لو وقع أول سطر فارغ قريبًا جدًا من
    البداية. لا يوجد فاصل مناسب (نص كتلة واحدة طويلة جدًا بلا أسطر إطلاقًا،
    نادر عمليًا لرسائل Masar) → قسّ حرفي صارم عند `limit` كحل احتياطي آمن
    (لا فقدان بيانات، فقط قد يقسّم كلمة بمنتصفها)."""
    if limit is None:
        limit = SAFE_SPLIT_LIMIT
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        split_at = window.rfind("\n\n")
        if split_at < limit // 2:
            alt = window.rfind("\n")
            split_at = alt if alt >= limit // 2 else limit
        chunks.append(remaining[:split_at].rstrip("\n"))
        remaining = remaining[split_at:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


@dataclass
class TelegramClient:
    """عميل Bot API بسيط لتوكن بوت واحد. لا يُخزّن أي حالة بين الاستدعاءات
    (بلا كاش/جلسة) — كل دالة مستقلة تمامًا، آمنة للاستدعاء من كوروتينات
    متزامنة عدة (لا حالة مشتركة قابلة للتسابق)."""

    token: str
    base_url: str = API_BASE_URL
    timeout: float = DEFAULT_TIMEOUT_SECONDS

    def _method_url(self, method: str) -> str:
        return f"{self.base_url}/bot{self.token}/{method}"

    async def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        # لا نُسجّل payload كاملًا بالسجلّ (قد يحوي نص رسالة عميل خاص) —
        # فقط اسم الطريقة ومعرّف الدردشة إن وُجد، للتشخيص بلا تسريب محتوى.
        chat_id = payload.get("chat_id")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self._method_url(method), json=payload)
        except httpx.HTTPError as exc:
            logger.warning("telegram %s فشل اتصال (chat_id=%s): %s", method, chat_id, exc)
            raise TelegramAPIError(f"فشل اتصال Telegram ({method}): {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            logger.warning("telegram %s ردّ غير JSON (status=%s)", method, response.status_code)
            raise TelegramAPIError(f"ردّ Telegram غير صالح ({method}, status={response.status_code})") from exc

        if not data.get("ok"):
            description = data.get("description", "بلا وصف")
            logger.warning("telegram %s رفض الطلب (chat_id=%s): %s", method, chat_id, description)
            raise TelegramAPIError(f"Telegram رفض {method}: {description}")

        return data.get("result") or {}

    # -----------------------------------------------------------------
    # sendMessage — نص عادي، أو بلوحة أزرار inline (buttons) إن أُرسلت
    # -----------------------------------------------------------------

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        buttons: list[list[dict[str, str]]] | None = None,
        disable_web_page_preview: bool = True,
    ) -> dict[str, Any]:
        """يرسل رسالة نصية. `buttons` (إن وُجدت) قائمة صفوف، كل صف قائمة
        أزرار {"text": ..., "callback_data": ...} — تُبنى كـinline_keyboard
        مباشرة (معظم المحادثة الجديدة قائمة على أزرار inline؛ استثناء
        وحيد هو طلب رقم الجوال عبر send_contact_request أدناه، الذي يحتاج
        reply_keyboard حقيقية — Telegram لا يدعم "شارك رقمك" كزر inline).

        B9/A3: نص أطول من SAFE_SPLIT_LIMIT يُقسَّم تلقائيًا (split_message_text)
        إلى عدة رسائل متتالية — `buttons` (إن وُجدت) تُرفَق بآخر قِطعة فقط
        (منطقيًا: الأزرار عادة إجراء متعلّق بنهاية الرسالة/القائمة). النتيجة
        المُرجَعة هي نتيجة *آخر* استدعاء sendMessage فقط (نفس التوقيع القديم
        بلا تغيير عند رسالة واحدة قصيرة — الحالة الشائعة كثيرًا)."""
        parts = split_message_text(text)
        result: dict[str, Any] = {}
        for i, part in enumerate(parts):
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": part,
                "disable_web_page_preview": disable_web_page_preview,
            }
            if buttons and i == len(parts) - 1:
                payload["reply_markup"] = {"inline_keyboard": buttons}
            result = await self._call("sendMessage", payload)
        return result

    async def send_contact_request(self, chat_id: int | str, text: str, button_text: str) -> dict[str, Any]:
        """يرسل رسالة مع زر لوحة ردّ واحد (reply_keyboard، لا inline) بخاصية
        request_contact — الطريقة الوحيدة بواجهة Telegram لطلب رقم جوال
        موثّق (مُتحقَّق من ملكيته عبر حساب Telegram نفسه، لا نصًا حرًّا قد
        يكتبه أي شخص). يُستخدم فقط بخطوة ربط هوية تيليجرام بعميل مُسجَّل
        مسبقًا (app.telegram_onboarding) — لا مكان آخر يحتاجه."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": {
                "keyboard": [[{"text": button_text, "request_contact": True}]],
                "resize_keyboard": True,
                "one_time_keyboard": True,
            },
        }
        return await self._call("sendMessage", payload)

    # -----------------------------------------------------------------
    # answerCallbackQuery — تأكيد استلام ضغطة زر (يُزيل شارة "جاري التحميل"
    # من عند المستخدم، مطلوب من Telegram خلال ~30 ثانية من كل callback_query)
    # -----------------------------------------------------------------

    async def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str | None = None,
        show_alert: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id, "show_alert": show_alert}
        if text:
            payload["text"] = text
        return await self._call("answerCallbackQuery", payload)

    # -----------------------------------------------------------------
    # editMessageText — تعديل نص/أزرار رسالة سابقة (تُستخدم لتحديث لوحة
    # اختيار متعددة مثل المدن/المجالات بمكانها بدل إرسال رسالة جديدة كل ضغطة)
    # -----------------------------------------------------------------

    async def edit_message_text(
        self,
        chat_id: int | str,
        message_id: int,
        text: str,
        *,
        buttons: list[list[dict[str, str]]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if buttons is not None:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        return await self._call("editMessageText", payload)

    # -----------------------------------------------------------------
    # editMessageReplyMarkup — تعديل لوحة الأزرار فقط بلا الحاجة لمعرفة نص
    # الرسالة الأصلي (بعكس editMessageText أعلاه الذي يتطلّب `text`). تُستخدم
    # بعد معالجة ضغطة تغذية راجعة (fb:...، راجع telegram_api.py) لإزالة
    # زرّي 👎/🎉 بعد تسجيل التقييم (buttons=[] يُزيل اللوحة بالكامل) بلا
    # الحاجة لتخزين/إعادة بناء نص التقرير الأصلي.
    # -----------------------------------------------------------------

    async def edit_message_reply_markup(
        self,
        chat_id: int | str,
        message_id: int,
        *,
        buttons: list[list[dict[str, str]]] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": {"inline_keyboard": buttons or []},
        }
        return await self._call("editMessageReplyMarkup", payload)

    # -----------------------------------------------------------------
    # getFile + تنزيل المحتوى — يحتاجهما onboarding لاستقبال ملف السيرة
    # الذاتية (PDF) المُرسَل كمرفق (document) بمحادثة العميل. طريقتان
    # منفصلتان (لا واحدة مدمجة) لأن getFile تمرّ عبر _call العادية (JSON،
    # نفس فحص ok:false) بينما التنزيل الفعلي رابط ملفات مختلف تمامًا
    # (api.telegram.org/file/bot<token>/<file_path>، جسم ثنائي لا JSON).
    # -----------------------------------------------------------------

    async def get_file(self, file_id: str) -> dict[str, Any]:
        return await self._call("getFile", {"file_id": file_id})

    # -----------------------------------------------------------------
    # sendPhoto / sendDocument — رفع bytes خام (multipart) مباشرة لمحادثة
    # أخرى، غالبًا ببوت آخر (B9/B3): إيصال دفع يستقبله بوت العملاء يُعاد
    # رفعه لبوت الأدمن. **file_id من بوت لا يعمل مع بوت آخر** (كل file_id
    # مرتبط بتوكن البوت الذي استقبله أصلًا) — لذا نُنزّل bytes الخام مرّة
    # (download_file_bytes أعلاه) ثم نرفعها هنا من جديد بتوكن البوت الآخر،
    # لا نمرّر file_id مباشرة. `_call` أعلاه غير صالحة هنا (تبني جسم JSON،
    # لا multipart) فنبني الطلب يدويًا بنفس فلسفة معالجة الأخطاء.
    # -----------------------------------------------------------------

    async def _send_bytes(
        self,
        method: str,
        field_name: str,
        chat_id: int | str,
        data: bytes,
        filename: str,
        *,
        caption: str | None = None,
        buttons: list[list[dict[str, str]]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": str(chat_id)}
        if caption:
            payload["caption"] = caption
        if buttons is not None:
            payload["reply_markup"] = json.dumps({"inline_keyboard": buttons}, ensure_ascii=False)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self._method_url(method),
                    data=payload,
                    files={field_name: (filename, data)},
                )
        except httpx.HTTPError as exc:
            logger.warning("telegram %s فشل اتصال (chat_id=%s): %s", method, chat_id, exc)
            raise TelegramAPIError(f"فشل اتصال Telegram ({method}): {exc}") from exc

        try:
            result = response.json()
        except ValueError as exc:
            raise TelegramAPIError(f"ردّ Telegram غير صالح ({method}, status={response.status_code})") from exc

        if not result.get("ok"):
            description = result.get("description", "بلا وصف")
            logger.warning("telegram %s رفض الطلب (chat_id=%s): %s", method, chat_id, description)
            raise TelegramAPIError(f"Telegram رفض {method}: {description}")
        return result.get("result") or {}

    async def send_photo(
        self,
        chat_id: int | str,
        data: bytes,
        filename: str,
        *,
        caption: str | None = None,
        buttons: list[list[dict[str, str]]] | None = None,
    ) -> dict[str, Any]:
        return await self._send_bytes("sendPhoto", "photo", chat_id, data, filename, caption=caption, buttons=buttons)

    async def send_document(
        self,
        chat_id: int | str,
        data: bytes,
        filename: str,
        *,
        caption: str | None = None,
        buttons: list[list[dict[str, str]]] | None = None,
    ) -> dict[str, Any]:
        return await self._send_bytes(
            "sendDocument", "document", chat_id, data, filename, caption=caption, buttons=buttons
        )

    async def download_file_bytes(self, file_path: str, *, max_bytes: int = 8 * 1024 * 1024) -> bytes:
        # تنبيه أمني (راجع REVIEW.md البند 8.2): رابط تنزيل الملفات ببروتوكول
        # Telegram يتضمّن توكن البوت الخام مكشوفًا بالرابط نفسه
        # (`.../file/bot<TOKEN>/...`) — سلوك بروتوكول Telegram نفسه، لا خطأ
        # هنا. لكن `httpx.HTTPStatusError` الناتجة عن `raise_for_status()`
        # تُضمّن هذا الرابط الكامل حرفيًا بنص رسالتها (`str(exc)`)، وأي
        # مُستدعٍ يُسجّلها بـ`exc_info=True` (telegram_onboarding.py._step_cv
        # يفعل هذا بالضبط عند فشل تنزيل CV) يُسرّب التوكن للسجلّ. لذا نلتقط
        # HTTPStatusError هنا تحديدًا ونبني استثناءً/رسالة سجلّ **جديدين
        # تمامًا** (كود الحالة + مسار الملف فقط، لا الرابط الخام إطلاقًا)
        # — و`from None` عمدًا (لا `from exc`) حتى لا يبقى الاستثناء الأصلي
        # (وتوكنه) مُتسلسلًا بـ`__cause__` فيظهر مجددًا بأي traceback مُسجَّل
        # لاحقًا عبر exc_info=True رغم الرسالة الآمنة.
        url = f"{self.base_url}/file/bot{self.token}/{file_path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else "?"
            logger.warning(
                "telegram تنزيل ملف رُفض بكود حالة غير ناجح (file_path=%s, status=%s)",
                file_path, status_code,
            )
            raise TelegramAPIError(
                f"فشل تنزيل ملف من Telegram (file_path={file_path}, status={status_code})"
            ) from None
        except httpx.HTTPError as exc:
            # فشل اتصال (لا استجابة HTTP أصلًا) — لا يحمل الرابط بنص رسالته
            # (تحقّقنا تجريبيًا: httpx.ConnectError/TimeoutException وغيرهما
            # لا تُضمّن الرابط، بعكس HTTPStatusError أعلاه) — آمن كما هو.
            raise TelegramAPIError(f"فشل تنزيل ملف من Telegram: {exc}") from exc
        content = response.content
        if len(content) > max_bytes:
            raise TelegramAPIError(f"الملف المُنزَّل يتجاوز الحد الأقصى ({max_bytes} بايت)")
        return content


# ---------------------------------------------------------------------------
# استخراج حدث محادثة موحّد من جسم تحديث Telegram الخام (Update JSON) — يشترك
# فيه onboarding والأدمن معًا (كلاهما يحتاج نفس التفكيك: أهو رسالة نصية عادية،
# مرفق، أم ضغطة زر inline؟) حتى لا يتكرر منطق التفكيك في كلا الملفين.
# ---------------------------------------------------------------------------


@dataclass
class ChatEvent:
    chat_id: int
    text: str
    is_callback: bool
    callback_data: str
    callback_query_id: str | None
    message_id: int | None
    document: dict[str, Any] | None
    photo: list[dict[str, Any]] | None
    contact: dict[str, Any] | None
    from_user_id: int | None
    # B9/B2: اسم مستخدم تيليجرام لمرسل التحديث (بلا "@"، كما يُرجعه Telegram
    # حرفيًا — قد يحوي أحرفًا كبيرة، التطبيع lower-case مسؤولية المستدعي)،
    # أو None إن لم يضبط المستخدم اسم مستخدم إطلاقًا. حقل جديد بنهاية
    # dataclass بقيمة افتراضية حتى لا يكسر أي بناء ChatEvent(...) قديم بالكود
    # أو الاختبارات لا يمرّره. يُستخدم حصرًا بربط مفوّضي بوت الأدمن
    # (telegram_admin._try_link_delegate) — لا استخدام آخر حاليًا.
    from_username: str | None = None


def extract_chat_event(update: dict[str, Any]) -> ChatEvent | None:
    """يحوّل جسم Update خام (JSON من Telegram) إلى ChatEvent موحّد، أو None
    إن لم يحمل التحديث رسالة/ضغطة زر ذات محادثة صالحة (مثال: edited_message،
    my_chat_member، إلخ — تُتجاهَل بصمت، ليست ضمن نطاق البوتين الحاليين)."""
    callback_query = update.get("callback_query")
    if callback_query:
        message = callback_query.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return None
        from_user = callback_query.get("from") or {}
        return ChatEvent(
            chat_id=int(chat_id),
            text="",
            is_callback=True,
            callback_data=str(callback_query.get("data") or ""),
            callback_query_id=callback_query.get("id"),
            message_id=message.get("message_id"),
            document=None,
            photo=None,
            contact=None,
            from_user_id=from_user.get("id"),
            from_username=from_user.get("username"),
        )

    message = update.get("message") or update.get("edited_message")
    if not message:
        return None
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return None
    from_user = message.get("from") or {}
    return ChatEvent(
        chat_id=int(chat_id),
        text=str(message.get("text") or message.get("caption") or "").strip(),
        is_callback=False,
        callback_data="",
        callback_query_id=None,
        message_id=message.get("message_id"),
        document=message.get("document"),
        photo=message.get("photo"),
        contact=message.get("contact"),
        from_user_id=from_user.get("id"),
        from_username=from_user.get("username"),
    )


# ---------------------------------------------------------------------------
# send_message_sync — نسخة متزامنة من sendMessage (راجع ملاحظة التوحيد أعلى
# الملف). تُستخدم حصرًا من app.telegram_notify (تيار الصادر داخل
# core-scheduler، بلا حلقة أحداث asyncio) بدل تكرار منطق بناء الرابط/فحص
# ok:false بشكل مستقل هناك — نفس عقد الرد الذي تتحقق منه TelegramClient._call
# غير المتزامنة أعلاه، مع إضافة إعادة محاولة اختيارية (max_attempts) بتراجع
# عبر sleep_fn (المُستدعي يمرّر time.sleep أو ما يعادله بالاختبار).
# ---------------------------------------------------------------------------


def send_message_sync(
    chat_id: int | str,
    text: str,
    *,
    token: str,
    reply_markup: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    base_url: str = API_BASE_URL,
    max_attempts: int = 1,
    sleep_fn: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    """يرسل رسالة نصية عبر sendMessage بطلب httpx متزامن (لا AsyncClient) —
    يرجع جسم رد تيليجرام الكامل (`{"ok": ..., "result": ...}`، بعكس
    TelegramClient._call التي تُرجع `result` فقط) حفاظًا على توافق عقد
    الإرجاع الذي كان يعتمده app.telegram_notify قبل التوحيد. يرفع
    TelegramAPIError عند ok:false، رد غير JSON صالح، أو استنفاد كل محاولات
    الاتصال — المُستدعي (telegram_notify.send_message) هو من يقرر تحويلها
    لاستثنائه الخاص (TelegramSendError) إن أراد عقد استثناءات مختلف."""
    url = f"{base_url}/bot{token}/sendMessage"
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = httpx.post(url, json=payload, timeout=timeout)
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < max_attempts:
                if sleep_fn:
                    sleep_fn(attempt)
                continue
            raise TelegramAPIError(f"فشل اتصال Telegram (sendMessage): {exc}") from exc

        if response.status_code in _RETRYABLE_STATUS and attempt < max_attempts:
            if sleep_fn:
                sleep_fn(attempt)
            continue

        try:
            data = response.json()
        except ValueError as exc:
            raise TelegramAPIError(
                f"ردّ Telegram غير صالح (sendMessage, status={response.status_code})"
            ) from exc

        if not data.get("ok"):
            raise TelegramAPIError(f"Telegram رفض sendMessage: {data.get('description', 'بلا وصف')}")
        return data

    if last_exc:
        raise TelegramAPIError(f"فشل اتصال Telegram (sendMessage): {last_exc}") from last_exc
    raise TelegramAPIError("فشل إرسال الرسالة بعد كل المحاولات لسبب غير معروف")  # pragma: no cover


# ---------------------------------------------------------------------------
# مصانع بسيطة تقرأ التوكنات من متغيّرات البيئة (نفس نمط auth.py/mail_crypto.py:
# فشل مغلق — None إن لم يُعرَّف التوكن، والمستدعي (telegram_api.py) هو من
# يقرر تعطيل المسار المرتبط بالكامل عندها بدل محاولة استدعاء ببيانات فارغة)
# ---------------------------------------------------------------------------


def get_customer_bot_client() -> TelegramClient | None:
    """عميل بوت العملاء ("مسار") — TELEGRAM_CUSTOMER_BOT_TOKEN من البيئة."""
    token = os.environ.get("TELEGRAM_CUSTOMER_BOT_TOKEN", "")
    return TelegramClient(token=token) if token else None


def get_admin_bot_client() -> TelegramClient | None:
    """عميل بوت الأدمن الخاص (أحمد فقط) — TELEGRAM_ADMIN_BOT_TOKEN من البيئة."""
    token = os.environ.get("TELEGRAM_ADMIN_BOT_TOKEN", "")
    return TelegramClient(token=token) if token else None
