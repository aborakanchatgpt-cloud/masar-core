"""اختبارات core/app/telegram_notify.py (B8: إزالة n8n — الإرسال الصادر عبر
تيليجرام مباشرة). كل الاختبارات هنا تُحاكي httpx بالكامل — **بعد التوحيد**
(راجع REPORT.md قسم "توحيد عميل Bot API") الاستدعاء الفعلي لـ`httpx.post`
يقع بداخل app.telegram_client.send_message_sync، فنُموّه `telegram_client.httpx.post`
مباشرة (لا `tn.httpx.post` — الوحدة لم تعد تستورد httpx إطلاقًا). بلا أي
اتصال شبكة حقيقي، بلا حاجة لقاعدة بيانات.

تشغيل: cd core && python -m pytest tests/test_telegram_notify.py -v
"""
from __future__ import annotations

import httpx
import pytest

from app import telegram_client, telegram_notify as tn


def _ok_response(chat_id: int = 1) -> httpx.Response:
    return httpx.Response(
        200,
        json={"ok": True, "result": {"message_id": 1, "chat": {"id": chat_id}}},
        request=httpx.Request("POST", "https://api.telegram.org/botTEST/sendMessage"),
    )


def _rejected_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={"ok": False, "description": "Bad Request: chat not found"},
        request=httpx.Request("POST", "https://api.telegram.org/botTEST/sendMessage"),
    )


def _retryable_response(status: int) -> httpx.Response:
    return httpx.Response(
        status,
        json={"ok": False, "description": "too many requests"},
        request=httpx.Request("POST", "https://api.telegram.org/botTEST/sendMessage"),
    )


def test_send_message_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_CUSTOMER_BOT_TOKEN", raising=False)
    with pytest.raises(tn.TelegramNotConfigured):
        tn.send_message(123, "مرحبًا")


def test_send_message_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_CUSTOMER_BOT_TOKEN", "TESTTOKEN")
    calls: list[dict] = []

    def fake_post(url, *, json, timeout):  # noqa: A002 — يطابق توقيع httpx.post
        calls.append({"url": url, "json": json, "timeout": timeout})
        return _ok_response()

    monkeypatch.setattr(telegram_client.httpx, "post", fake_post)
    result = tn.send_message(555, "نص تجريبي", reply_markup={"inline_keyboard": [[]]})

    assert result["ok"] is True
    assert len(calls) == 1
    assert calls[0]["url"] == "https://api.telegram.org/botTESTTOKEN/sendMessage"
    assert calls[0]["json"]["chat_id"] == 555
    assert calls[0]["json"]["text"] == "نص تجريبي"
    assert calls[0]["json"]["reply_markup"] == {"inline_keyboard": [[]]}


def test_send_message_omits_reply_markup_when_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_CUSTOMER_BOT_TOKEN", "TESTTOKEN")
    captured = {}

    def fake_post(url, *, json, timeout):
        captured.update(json)
        return _ok_response()

    monkeypatch.setattr(telegram_client.httpx, "post", fake_post)
    tn.send_message(1, "بلا أزرار")
    assert "reply_markup" not in captured


def test_send_message_raises_on_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_CUSTOMER_BOT_TOKEN", "TESTTOKEN")
    monkeypatch.setattr(telegram_client.httpx, "post", lambda url, **kw: _rejected_response())
    with pytest.raises(tn.TelegramSendError):
        tn.send_message(1, "نص")


def test_send_message_retries_then_succeeds_on_429(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_CUSTOMER_BOT_TOKEN", "TESTTOKEN")
    monkeypatch.setattr(tn.time, "sleep", lambda *_a, **_kw: None)
    responses = [_retryable_response(429), _ok_response()]

    def fake_post(url, **kw):
        return responses.pop(0)

    monkeypatch.setattr(telegram_client.httpx, "post", fake_post)
    result = tn.send_message(1, "نص")
    assert result["ok"] is True
    assert responses == []


def test_send_message_uses_custom_token_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_CUSTOMER_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_ADMIN_BOT_TOKEN", "ADMINTOKEN")
    captured_url = {}

    def fake_post(url, **kw):
        captured_url["url"] = url
        return _ok_response()

    monkeypatch.setattr(telegram_client.httpx, "post", fake_post)
    tn.send_message(1, "نص", token_env_var="TELEGRAM_ADMIN_BOT_TOKEN")
    assert captured_url["url"] == "https://api.telegram.org/botADMINTOKEN/sendMessage"


def test_send_message_raises_send_error_on_persistent_connect_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """توحيد ما بعد الدمج: فشل شبكة مستمر (لا فقط ok:false) يجب أن يُرفَع
    أيضًا كـTelegramSendError (عقد الاستثناءات العلوي لهذا الملف) — telegram_client
    يرفع TelegramAPIError الأدنى مستوى، وsend_message هنا يُغلّفه."""
    monkeypatch.setenv("TELEGRAM_CUSTOMER_BOT_TOKEN", "TESTTOKEN")
    monkeypatch.setattr(tn.time, "sleep", lambda *_a, **_kw: None)

    def fake_post(url, **kw):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(telegram_client.httpx, "post", fake_post)
    with pytest.raises(tn.TelegramSendError):
        tn.send_message(1, "نص")


def test_split_message_keeps_short_text_as_single_chunk() -> None:
    assert tn.split_message("سطر واحد قصير", max_len=100) == ["سطر واحد قصير"]


def test_split_message_splits_on_line_boundaries() -> None:
    lines = [f"سطر رقم {i}" for i in range(50)]
    text_body = "\n".join(lines)
    chunks = tn.split_message(text_body, max_len=80)
    assert len(chunks) > 1
    # لا سطر يُقطع منتصفه — كل سطر أصلي يظهر كاملًا بأحد الأجزاء.
    rebuilt = "\n".join(chunks)
    assert rebuilt == text_body
    for chunk in chunks:
        assert len(chunk) <= 80 or "\n" not in chunk  # سطر واحد أطول من الحد يبقى وحده بجزء


def test_build_feedback_keyboard_none_when_no_send_queue_ids() -> None:
    apps = [{"title": "وظيفة", "send_queue_id": None}, {"title": "أخرى"}]
    assert tn.build_feedback_keyboard(customer_id=7, applications=apps) is None


def test_build_feedback_keyboard_builds_rows_matching_n8n_format() -> None:
    apps = [
        {"title": "مهندس", "send_queue_id": 10},
        {"title": "محاسب", "send_queue_id": 11},
        {"title": "بلا معرّف", "send_queue_id": None},
    ]
    keyboard = tn.build_feedback_keyboard(customer_id=42, applications=apps)
    assert keyboard is not None
    rows = keyboard["inline_keyboard"]
    assert len(rows) == 2
    assert rows[0][0] == {"text": "👎", "callback_data": "fb:42:10:thumbs_down"}
    assert rows[0][1] == {"text": "🎉", "callback_data": "fb:42:10:celebrate"}
    assert rows[1][0]["callback_data"] == "fb:42:11:thumbs_down"


def test_build_feedback_keyboard_respects_max_buttons() -> None:
    apps = [{"title": f"وظيفة {i}", "send_queue_id": i} for i in range(60)]
    keyboard = tn.build_feedback_keyboard(customer_id=1, applications=apps, max_buttons=5)
    assert keyboard is not None
    assert len(keyboard["inline_keyboard"]) == 5
