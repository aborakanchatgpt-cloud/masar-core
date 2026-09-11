"""اختبارات core/app/telegram_client.py (B8) — عميل Telegram Bot API الخفيف.

كل استدعاء شبكي يُموّه بمراقبة/استبدال httpx.AsyncClient (لا اتصال حقيقي
أبدًا) — نتحقق من: بناء الرابط الصحيح، تمرير reply_markup الصحيح
(inline_keyboard مقابل reply_keyboard بـrequest_contact)، رفع
TelegramAPIError عند ok:false أو فشل شبكة/JSON تالف، وتفكيك Update الخام
(extract_chat_event) لرسالة عادية/مرفق/ضغطة زر.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app import telegram_client
from app.telegram_client import (
    ChatEvent,
    TelegramAPIError,
    TelegramClient,
    extract_chat_event,
    split_message_text,
)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# مزيّفات httpx.AsyncClient — تُسجّل الطلب وتُرجع استجابة مُعدّة سلفًا،
# بلا أي اتصال شبكي حقيقي.
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(
        self,
        *,
        json_body: Any = None,
        status_code: int = 200,
        content: bytes = b"",
        raise_error: bool = False,
        raise_error_url: str | None = None,
    ):
        self._json_body = json_body
        self.status_code = status_code
        self.content = content
        self._raise_error = raise_error
        self._raise_error_url = raise_error_url

    def json(self) -> Any:
        if self._json_body is None:
            raise ValueError("no json body configured")
        return self._json_body

    def raise_for_status(self) -> None:
        if not self._raise_error:
            return
        # نبني httpx.Response/HTTPStatusError حقيقيّين (نفس مسار الكود
        # الفعلي بمكتبة httpx، لا استثناء مُصطنَع يدويًا) — هذا يضمن أن
        # str(exc) يتضمّن الرابط الكامل (بما فيه التوكن إن مُرّر بـ
        # raise_error_url) تمامًا كما يحدث فعليًا، ما يجعل اختبار عدم
        # التسريب أدناه (test_download_file_bytes_...) واقعيًا لا مصطنَعًا.
        request = httpx.Request("GET", self._raise_error_url or "https://api.telegram.org/x")
        real_response = httpx.Response(self.status_code, request=request, content=self.content)
        real_response.raise_for_status()


class _FakeAsyncClient:
    calls: list[dict[str, Any]] = []
    response: _FakeResponse = _FakeResponse(json_body={"ok": True, "result": {"message_id": 1}})
    raise_connect_error: bool = False

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url: str, json: dict | None = None) -> _FakeResponse:
        type(self).calls.append({"method": "post", "url": url, "json": json})
        if type(self).raise_connect_error:
            raise httpx.ConnectError("boom")
        return type(self).response

    async def get(self, url: str) -> _FakeResponse:
        type(self).calls.append({"method": "get", "url": url})
        if type(self).raise_connect_error:
            raise httpx.ConnectError("boom")
        return type(self).response


@pytest.fixture(autouse=True)
def _patch_httpx(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.response = _FakeResponse(json_body={"ok": True, "result": {"message_id": 1}})
    _FakeAsyncClient.raise_connect_error = False
    monkeypatch.setattr(telegram_client.httpx, "AsyncClient", _FakeAsyncClient)
    yield


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


def test_send_message_builds_correct_url_and_payload():
    client = TelegramClient(token="TESTTOKEN")
    result = _run(client.send_message(12345, "أهلًا"))
    assert result == {"message_id": 1}
    assert len(_FakeAsyncClient.calls) == 1
    call = _FakeAsyncClient.calls[0]
    assert call["url"] == "https://api.telegram.org/botTESTTOKEN/sendMessage"
    assert call["json"]["chat_id"] == 12345
    assert call["json"]["text"] == "أهلًا"
    assert "reply_markup" not in call["json"]


def test_send_message_with_inline_keyboard_buttons():
    client = TelegramClient(token="T")
    buttons = [[{"text": "نعم", "callback_data": "yes"}]]
    _run(client.send_message(1, "سؤال؟", buttons=buttons))
    payload = _FakeAsyncClient.calls[0]["json"]
    assert payload["reply_markup"] == {"inline_keyboard": buttons}


def test_send_message_raises_on_ok_false():
    _FakeAsyncClient.response = _FakeResponse(json_body={"ok": False, "description": "chat not found"})
    client = TelegramClient(token="T")
    with pytest.raises(TelegramAPIError, match="chat not found"):
        _run(client.send_message(1, "hi"))


def test_send_message_raises_telegram_error_on_connect_failure():
    _FakeAsyncClient.raise_connect_error = True
    client = TelegramClient(token="T")
    with pytest.raises(TelegramAPIError):
        _run(client.send_message(1, "hi"))


def test_send_message_raises_on_invalid_json_response():
    _FakeAsyncClient.response = _FakeResponse(json_body=None)  # .json() سيرفع ValueError
    client = TelegramClient(token="T")
    with pytest.raises(TelegramAPIError):
        _run(client.send_message(1, "hi"))


# ---------------------------------------------------------------------------
# B9/A3: split_message_text + تقسيم send_message التلقائي للنصوص الطويلة
# ---------------------------------------------------------------------------


def test_split_message_text_returns_single_chunk_when_short():
    assert split_message_text("سطر قصير") == ["سطر قصير"]


def test_split_message_text_splits_at_paragraph_boundary():
    part_a = "أ" * 2000
    part_b = "ب" * 2000
    text = f"{part_a}\n\n{part_b}"
    chunks = split_message_text(text, limit=3500)
    assert len(chunks) == 2
    assert chunks[0] == part_a
    assert chunks[1] == part_b


def test_split_message_text_hard_splits_single_block_with_no_newlines():
    text = "س" * 9000
    chunks = split_message_text(text, limit=3500)
    assert len(chunks) == 3
    assert "".join(chunks) == text
    assert all(len(c) <= 3500 for c in chunks)


def test_split_message_text_never_drops_content():
    # نص طويل عشوائي التركيب (أسطر متفاوتة الطول + فقرات) — التحقق الجوهري:
    # لا فقدان حرف واحد بصرف النظر عن نقاط القسمة.
    lines = [f"سطر رقم {i} " + ("x" * (i % 50)) for i in range(300)]
    text = "\n".join(lines)
    chunks = split_message_text(text, limit=500)
    assert len(chunks) > 1
    assert "\n".join(chunks) == text or "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_send_message_splits_long_text_into_multiple_calls(monkeypatch):
    monkeypatch.setattr(telegram_client, "SAFE_SPLIT_LIMIT", 50)
    client = TelegramClient(token="T")
    long_text = "\n\n".join(f"فقرة {i} " + ("x" * 40) for i in range(4))
    _run(client.send_message(1, long_text))
    assert len(_FakeAsyncClient.calls) > 1
    sent_texts = [c["json"]["text"] for c in _FakeAsyncClient.calls]
    assert "".join(sent_texts).replace("\n\n", "") != ""  # لم تُرسَل رسالة فارغة بالخطأ


def test_send_message_attaches_buttons_only_to_last_chunk(monkeypatch):
    monkeypatch.setattr(telegram_client, "SAFE_SPLIT_LIMIT", 50)
    client = TelegramClient(token="T")
    buttons = [[{"text": "رجوع", "callback_data": "back"}]]
    long_text = "\n\n".join(f"فقرة {i} " + ("x" * 40) for i in range(3))
    _run(client.send_message(1, long_text, buttons=buttons))
    assert len(_FakeAsyncClient.calls) > 1
    for call in _FakeAsyncClient.calls[:-1]:
        assert "reply_markup" not in call["json"]
    assert _FakeAsyncClient.calls[-1]["json"]["reply_markup"] == {"inline_keyboard": buttons}


# ---------------------------------------------------------------------------
# send_contact_request — reply_keyboard حقيقية بخاصية request_contact
# ---------------------------------------------------------------------------


def test_send_contact_request_uses_reply_keyboard_not_inline():
    client = TelegramClient(token="T")
    _run(client.send_contact_request(1, "شارك رقمك", "📱 مشاركة الرقم"))
    payload = _FakeAsyncClient.calls[0]["json"]
    assert "keyboard" in payload["reply_markup"]
    assert "inline_keyboard" not in payload["reply_markup"]
    assert payload["reply_markup"]["keyboard"][0][0]["request_contact"] is True


# ---------------------------------------------------------------------------
# answerCallbackQuery / editMessageText
# ---------------------------------------------------------------------------


def test_answer_callback_query_payload():
    client = TelegramClient(token="T")
    _run(client.answer_callback_query("cb1", text="تم", show_alert=True))
    payload = _FakeAsyncClient.calls[0]["json"]
    assert payload == {"callback_query_id": "cb1", "show_alert": True, "text": "تم"}


def test_edit_message_text_payload():
    client = TelegramClient(token="T")
    _run(client.edit_message_text(1, 99, "نص جديد", buttons=[[{"text": "زر", "callback_data": "x"}]]))
    call = _FakeAsyncClient.calls[0]
    assert call["url"].endswith("/editMessageText")
    assert call["json"]["message_id"] == 99
    assert call["json"]["reply_markup"]["inline_keyboard"][0][0]["text"] == "زر"


def test_edit_message_reply_markup_with_buttons():
    client = TelegramClient(token="T")
    _run(client.edit_message_reply_markup(1, 99, buttons=[[{"text": "✅", "callback_data": "x"}]]))
    call = _FakeAsyncClient.calls[0]
    assert call["url"].endswith("/editMessageReplyMarkup")
    assert call["json"]["reply_markup"]["inline_keyboard"][0][0]["text"] == "✅"


def test_edit_message_reply_markup_empty_removes_keyboard():
    client = TelegramClient(token="T")
    _run(client.edit_message_reply_markup(1, 99, buttons=[]))
    call = _FakeAsyncClient.calls[0]
    assert call["json"]["reply_markup"] == {"inline_keyboard": []}
    _run(client.edit_message_reply_markup(1, 99, buttons=None))
    call2 = _FakeAsyncClient.calls[1]
    assert call2["json"]["reply_markup"] == {"inline_keyboard": []}


# ---------------------------------------------------------------------------
# getFile / download_file_bytes
# ---------------------------------------------------------------------------


def test_get_file_calls_correct_method():
    client = TelegramClient(token="T")
    _FakeAsyncClient.response = _FakeResponse(json_body={"ok": True, "result": {"file_path": "docs/f.pdf"}})
    result = _run(client.get_file("file123"))
    assert result["file_path"] == "docs/f.pdf"
    assert _FakeAsyncClient.calls[0]["json"] == {"file_id": "file123"}


def test_download_file_bytes_returns_content():
    client = TelegramClient(token="T")
    _FakeAsyncClient.response = _FakeResponse(content=b"%PDF-1.4 fake")
    content = _run(client.download_file_bytes("docs/f.pdf"))
    assert content == b"%PDF-1.4 fake"
    assert _FakeAsyncClient.calls[0]["url"] == "https://api.telegram.org/file/botT/docs/f.pdf"


def test_download_file_bytes_rejects_oversized_content():
    client = TelegramClient(token="T")
    _FakeAsyncClient.response = _FakeResponse(content=b"x" * 100)
    with pytest.raises(TelegramAPIError):
        _run(client.download_file_bytes("docs/f.pdf", max_bytes=10))


# ---------------------------------------------------------------------------
# REVIEW.md البند 8.2 [blocker] — تسريب توكن البوت عبر رسالة/سجلّ
# httpx.HTTPStatusError الخام (رابط تنزيل الملف يتضمّن التوكن بالرابط نفسه:
# `.../file/bot<TOKEN>/...`؛ raise_for_status() الخام يُضمّنه بنص رسالته).
# ---------------------------------------------------------------------------


def test_download_file_bytes_http_status_error_message_excludes_token_and_includes_status():
    token = "123456789:AA-SUPER-SECRET-BOT-TOKEN"
    file_path = "documents/file_42.pdf"
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    client = TelegramClient(token=token)
    _FakeAsyncClient.response = _FakeResponse(status_code=404, raise_error=True, raise_error_url=url)

    with pytest.raises(TelegramAPIError) as excinfo:
        _run(client.download_file_bytes(file_path))

    exc = excinfo.value
    assert token not in str(exc)
    assert token not in repr(exc)
    assert "404" in str(exc)
    assert file_path in str(exc)
    # from None عمدًا بالتنفيذ — لا يبقى httpx.HTTPStatusError الأصلي (وتوكنه
    # الكامل بالرابط) مُتسلسلًا بـ__cause__ خلف الاستثناء الآمن
    assert exc.__cause__ is None
    assert exc.__suppress_context__ is True


def test_download_file_bytes_http_status_error_does_not_leak_token_via_exc_info_logging(caplog):
    """يُحاكي حرفيًا نمط telegram_onboarding.py._step_cv (`except
    TelegramAPIError: logger.warning(..., exc_info=True)`) — التوكن يجب ألا
    يظهر حتى بالـtraceback الكامل المُسجّل، لا فقط بنص رسالة الاستثناء."""
    token = "987654321:ANOTHER-SECRET-TOKEN-VALUE"
    file_path = "documents/cv_customer_7.pdf"
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    client = TelegramClient(token=token)
    _FakeAsyncClient.response = _FakeResponse(status_code=403, raise_error=True, raise_error_url=url)

    with pytest.raises(TelegramAPIError) as excinfo:
        _run(client.download_file_bytes(file_path))

    onboarding_logger = telegram_client.logging.getLogger("masar.telegram_onboarding")
    with caplog.at_level(telegram_client.logging.WARNING):
        try:
            raise excinfo.value
        except TelegramAPIError:
            onboarding_logger.warning("فشل تنزيل مرفق CV من Telegram", exc_info=True)

    assert token not in caplog.text


# ---------------------------------------------------------------------------
# مصانع التوكن — فشل مغلق (None بلا متغيّر بيئة)
# ---------------------------------------------------------------------------


def test_get_customer_bot_client_returns_none_without_env(monkeypatch):
    monkeypatch.delenv("TELEGRAM_CUSTOMER_BOT_TOKEN", raising=False)
    assert telegram_client.get_customer_bot_client() is None


def test_get_admin_bot_client_returns_client_with_env_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_BOT_TOKEN", "abc123")
    client = telegram_client.get_admin_bot_client()
    assert client is not None
    assert client.token == "abc123"


# ---------------------------------------------------------------------------
# extract_chat_event — تفكيك Update خام
# ---------------------------------------------------------------------------


def test_extract_chat_event_plain_text_message():
    update = {"message": {"message_id": 5, "chat": {"id": 111}, "text": "  مرحبا  ", "from": {"id": 111}}}
    event = extract_chat_event(update)
    assert isinstance(event, ChatEvent)
    assert event.chat_id == 111
    assert event.text == "مرحبا"
    assert event.is_callback is False
    assert event.document is None


def test_extract_chat_event_callback_query():
    update = {
        "callback_query": {
            "id": "cbid1",
            "data": "menu:overview",
            "from": {"id": 222},
            "message": {"message_id": 9, "chat": {"id": 222}},
        }
    }
    event = extract_chat_event(update)
    assert event.is_callback is True
    assert event.callback_data == "menu:overview"
    assert event.callback_query_id == "cbid1"
    assert event.chat_id == 222
    assert event.message_id == 9


def test_extract_chat_event_document_and_contact():
    update = {
        "message": {
            "chat": {"id": 333},
            "from": {"id": 333},
            "document": {"file_id": "f1", "mime_type": "application/pdf"},
        }
    }
    event = extract_chat_event(update)
    assert event.document == {"file_id": "f1", "mime_type": "application/pdf"}

    update2 = {
        "message": {
            "chat": {"id": 333},
            "from": {"id": 333},
            "contact": {"phone_number": "966500000000", "user_id": 333},
        }
    }
    event2 = extract_chat_event(update2)
    assert event2.contact["phone_number"] == "966500000000"
    assert event2.from_user_id == 333


def test_extract_chat_event_returns_none_for_unsupported_update():
    assert extract_chat_event({"my_chat_member": {}}) is None
    assert extract_chat_event({"message": {"chat": {}}}) is None


# ---------------------------------------------------------------------------
# send_message_sync — نسخة متزامنة (B8: توحيد عميل Bot API — تُستخدم من
# app.telegram_notify، تيار الصادر داخل core-scheduler). تُموّه هنا
# httpx.post مباشرة (لا AsyncClient) لأنها الدالة الوحيدة المتزامنة بالملف.
# ---------------------------------------------------------------------------


def test_send_message_sync_builds_correct_url_and_payload(monkeypatch):
    calls = []

    def fake_post(url, *, json, timeout):
        calls.append({"url": url, "json": json})

        class _Resp:
            status_code = 200

            def json(self):
                return {"ok": True, "result": {"message_id": 1}}

        return _Resp()

    monkeypatch.setattr(telegram_client.httpx, "post", fake_post)
    result = telegram_client.send_message_sync(555, "نص", token="TESTTOKEN")
    assert result["ok"] is True
    assert calls[0]["url"] == "https://api.telegram.org/botTESTTOKEN/sendMessage"
    assert calls[0]["json"]["chat_id"] == 555
    assert "reply_markup" not in calls[0]["json"]


def test_send_message_sync_raises_on_ok_false(monkeypatch):
    class _Resp:
        status_code = 200

        def json(self):
            return {"ok": False, "description": "chat not found"}

    monkeypatch.setattr(telegram_client.httpx, "post", lambda *a, **kw: _Resp())
    with pytest.raises(TelegramAPIError, match="chat not found"):
        telegram_client.send_message_sync(1, "hi", token="T")


def test_send_message_sync_retries_on_retryable_status_then_succeeds(monkeypatch):
    sleeps = []
    responses = iter(
        [
            type("R", (), {"status_code": 429, "json": lambda self: {"ok": False}})(),
            type("R", (), {"status_code": 200, "json": lambda self: {"ok": True, "result": {}}})(),
        ]
    )
    monkeypatch.setattr(telegram_client.httpx, "post", lambda *a, **kw: next(responses))
    result = telegram_client.send_message_sync(
        1, "hi", token="T", max_attempts=2, sleep_fn=lambda attempt: sleeps.append(attempt)
    )
    assert result["ok"] is True
    assert sleeps == [1]


def test_send_message_sync_raises_after_exhausting_retries_on_connect_error(monkeypatch):
    def fake_post(*a, **kw):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(telegram_client.httpx, "post", fake_post)
    with pytest.raises(TelegramAPIError):
        telegram_client.send_message_sync(1, "hi", token="T", max_attempts=2, sleep_fn=lambda _a: None)


def test_send_message_sync_includes_reply_markup_when_given(monkeypatch):
    captured = {}

    def fake_post(url, *, json, timeout):
        captured.update(json)

        class _Resp:
            status_code = 200

            def json(self):
                return {"ok": True, "result": {}}

        return _Resp()

    monkeypatch.setattr(telegram_client.httpx, "post", fake_post)
    telegram_client.send_message_sync(1, "hi", token="T", reply_markup={"inline_keyboard": [[]]})
    assert captured["reply_markup"] == {"inline_keyboard": [[]]}
