"""اختبارات core/app/telegram_api.py (POST /telegram/webhook/{token}/{bot_kind})
— لم يكن لهذا الراوتر أي اختبار آلي قبل الدمج (تقرير تيار الوارد اكتفى بتحقّق
يدوي عبر TestClient، راجع REPORT.md الأصلي). يُضاف هنا كجزء من الدمج،
خصوصًا لتغطية الوصلة الجديدة بين التيارين: ضغطة تغذية راجعة (fb:...) التي
يُرفقها تيار الصادر (telegram_notify.build_feedback_keyboard) ويعالجها هذا
الراوتر (_handle_feedback_callback → feedback_api.submit_feedback).

كما يغطي التوجيه حسب bot_kind بمقطع المسار (بدل chat_id وحده، راجع
docstring رأس telegram_api.py): مسار /customer يعامل أي chat_id — بما فيه
صاحب MASAR_OWNER_CHAT_ID نفسه — كعميل عادي (هذا ما يتيح لأحمد تجربة بوت
العملاء من حسابه الشخصي)، ومسار /admin يرفض بصمت أي chat_id غير صاحب
MASAR_OWNER_CHAT_ID حتى لو عرف رابطه السرّي.

نفس أسلوب بقية اختبارات core/tests (استدعاء دالة الراوتر مباشرة عبر
asyncio.run بدل TestClient — لا حاجة لإقلاع app.main كاملة ولا اتصال شبكة/
قاعدة بيانات حقيقي؛ راجع test_telegram_client.py:_run لنفس النمط). كل
استدعاء Bot API حقيقي (TelegramClient) مموَّه.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException

from app import feedback_api, telegram_api
from app.telegram_api import telegram_webhook, telegram_webhook_get

WEBHOOK_SECRET = "test-secret-token"


def _run(coro):
    return asyncio.run(coro)


class _FakeRequest:
    """جسم Request مزيّف — كل ما يحتاجه telegram_webhook: .headers.get(...)
    وawait .json()."""

    def __init__(self, body: dict | bytes | None, headers: dict | None = None):
        self._body = body
        self.headers = headers or {}

    async def json(self):
        if isinstance(self._body, (bytes, str)):
            raise ValueError("ليس JSON صالحًا")
        return self._body


@pytest.fixture(autouse=True)
def _set_webhook_secret(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", WEBHOOK_SECRET)
    yield


# ---------------------------------------------------------------------------
# بوابة التوكن/السر/bot_kind
# ---------------------------------------------------------------------------


def test_webhook_disabled_returns_503_without_secret_env(monkeypatch):
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    resp = _run(telegram_webhook("anything", "customer", _FakeRequest({})))
    assert resp.status_code == 503


def test_webhook_wrong_token_returns_404():
    resp = _run(telegram_webhook("wrong-token", "customer", _FakeRequest({})))
    assert resp.status_code == 404


def test_webhook_invalid_bot_kind_returns_404():
    resp = _run(telegram_webhook(WEBHOOK_SECRET, "bogus", _FakeRequest({})))
    assert resp.status_code == 404


def test_webhook_wrong_secret_header_returns_404():
    resp = _run(
        telegram_webhook(
            WEBHOOK_SECRET,
            "customer",
            _FakeRequest({}, headers={"X-Telegram-Bot-Api-Secret-Token": "not-the-secret"}),
        )
    )
    assert resp.status_code == 404


def test_webhook_correct_secret_header_passes_gate(monkeypatch):
    monkeypatch.delenv("TELEGRAM_CUSTOMER_BOT_TOKEN", raising=False)
    monkeypatch.delenv("MASAR_OWNER_CHAT_ID", raising=False)
    resp = _run(
        telegram_webhook(
            WEBHOOK_SECRET,
            "customer",
            _FakeRequest({"my_chat_member": {}}, headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET}),
        )
    )
    assert resp.status_code == 200


def test_webhook_get_disabled_returns_503(monkeypatch):
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    resp = _run(telegram_webhook_get("anything", "customer"))
    assert resp.status_code == 503


def test_webhook_get_wrong_token_returns_404():
    resp = _run(telegram_webhook_get("wrong-token", "customer"))
    assert resp.status_code == 404


def test_webhook_get_invalid_bot_kind_returns_404():
    resp = _run(telegram_webhook_get(WEBHOOK_SECRET, "bogus"))
    assert resp.status_code == 404


def test_webhook_get_method_not_allowed():
    resp = _run(telegram_webhook_get(WEBHOOK_SECRET, "customer"))
    assert resp.status_code == 405


def test_webhook_invalid_json_body_returns_200_ok():
    resp = _run(telegram_webhook(WEBHOOK_SECRET, "customer", _FakeRequest(b"not json")))
    assert resp.status_code == 200
    assert json.loads(resp.body) == {"ok": True}


def test_webhook_non_dict_body_returns_200_ok():
    resp = _run(telegram_webhook(WEBHOOK_SECRET, "customer", _FakeRequest([1, 2, 3])))
    assert resp.status_code == 200


def test_webhook_unsupported_update_returns_200_ok(monkeypatch):
    monkeypatch.delenv("TELEGRAM_CUSTOMER_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ADMIN_BOT_TOKEN", raising=False)
    resp = _run(telegram_webhook(WEBHOOK_SECRET, "customer", _FakeRequest({"my_chat_member": {}})))
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# توجيه حسب bot_kind — بلا توكن بوت مضبوط = تجاهل بصمت (200)
# ---------------------------------------------------------------------------


def test_webhook_regular_message_without_bot_token_is_ignored(monkeypatch):
    monkeypatch.delenv("TELEGRAM_CUSTOMER_BOT_TOKEN", raising=False)
    monkeypatch.delenv("MASAR_OWNER_CHAT_ID", raising=False)
    update = {"message": {"chat": {"id": 555}, "from": {"id": 555}, "text": "مرحبا"}}
    resp = _run(telegram_webhook(WEBHOOK_SECRET, "customer", _FakeRequest(update)))
    assert resp.status_code == 200


def test_webhook_routes_to_admin_handler_on_admin_bot_kind_for_owner_chat(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_BOT_TOKEN", "ADMINTOKEN")
    monkeypatch.setenv("MASAR_OWNER_CHAT_ID", "999")

    called = {}

    async def fake_admin_handle_update(update, event, client):
        called["handler"] = "admin"
        called["chat_id"] = event.chat_id

    async def fake_answer_callback_query(self, callback_query_id, *, text=None, show_alert=False):
        return {}

    monkeypatch.setattr(telegram_api.telegram_admin, "handle_update", fake_admin_handle_update)
    monkeypatch.setattr(telegram_api.TelegramClient, "answer_callback_query", fake_answer_callback_query)

    update = {"message": {"chat": {"id": 999}, "from": {"id": 999}, "text": "hi"}}
    resp = _run(telegram_webhook(WEBHOOK_SECRET, "admin", _FakeRequest(update)))
    assert resp.status_code == 200
    assert called == {"handler": "admin", "chat_id": 999}


def test_webhook_routes_to_customer_handler_on_customer_bot_kind_for_non_owner_chat(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CUSTOMER_BOT_TOKEN", "CUSTTOKEN")
    monkeypatch.setenv("MASAR_OWNER_CHAT_ID", "999")

    called = {}

    async def fake_onboarding_handle_update(update, event, client):
        called["handler"] = "customer"
        called["chat_id"] = event.chat_id

    monkeypatch.setattr(telegram_api.telegram_onboarding, "handle_update", fake_onboarding_handle_update)

    update = {"message": {"chat": {"id": 555}, "from": {"id": 555}, "text": "hi"}}
    resp = _run(telegram_webhook(WEBHOOK_SECRET, "customer", _FakeRequest(update)))
    assert resp.status_code == 200
    assert called == {"handler": "customer", "chat_id": 555}


def test_webhook_owner_chat_on_customer_bot_kind_is_treated_as_customer(monkeypatch):
    """القدرة الجديدة التي طلبها أحمد صراحةً: صاحب MASAR_OWNER_CHAT_ID
    (هو نفسه) يراسل بوت العملاء من حسابه الشخصي فيُعامَل كعميل عادي فعليًا
    (لا كأدمن)، لأن التوجيه الآن حسب bot_kind بالمسار لا chat_id."""
    monkeypatch.setenv("TELEGRAM_CUSTOMER_BOT_TOKEN", "CUSTTOKEN")
    monkeypatch.setenv("MASAR_OWNER_CHAT_ID", "677475661")

    called = {}

    async def fake_onboarding_handle_update(update, event, client):
        called["handler"] = "customer"
        called["chat_id"] = event.chat_id

    async def fake_admin_handle_update(update, event, client):
        called["handler"] = "admin"

    monkeypatch.setattr(telegram_api.telegram_onboarding, "handle_update", fake_onboarding_handle_update)
    monkeypatch.setattr(telegram_api.telegram_admin, "handle_update", fake_admin_handle_update)

    update = {"message": {"chat": {"id": 677475661}, "from": {"id": 677475661}, "text": "hi"}}
    resp = _run(telegram_webhook(WEBHOOK_SECRET, "customer", _FakeRequest(update)))
    assert resp.status_code == 200
    assert called == {"handler": "customer", "chat_id": 677475661}


def test_webhook_non_owner_chat_on_admin_bot_kind_is_silently_ignored(monkeypatch):
    """بوابة الأمان الثانوية على مسار /admin: غير صاحب MASAR_OWNER_CHAT_ID
    لا يُعامَل كأدمن حتى لو وصل مسار بوت الأدمن (السرّي أصلًا بتوكن المسار)
    — رفض بصمت، بلا استدعاء أي معالج."""
    monkeypatch.setenv("TELEGRAM_ADMIN_BOT_TOKEN", "ADMINTOKEN")
    monkeypatch.setenv("MASAR_OWNER_CHAT_ID", "999")

    called = {"handler": None}

    async def fake_admin_handle_update(update, event, client):
        called["handler"] = "admin"

    monkeypatch.setattr(telegram_api.telegram_admin, "handle_update", fake_admin_handle_update)

    update = {"message": {"chat": {"id": 555}, "from": {"id": 555}, "text": "hi"}}
    resp = _run(telegram_webhook(WEBHOOK_SECRET, "admin", _FakeRequest(update)))
    assert resp.status_code == 200
    assert called["handler"] is None


# ---------------------------------------------------------------------------
# fb:<customer_id>:<send_queue_id>:<kind> — الوصلة الجديدة بعد الدمج
# ---------------------------------------------------------------------------


def _feedback_update(data: str, *, message_id: int = 3, chat_id: int = 12345) -> dict:
    return {
        "callback_query": {
            "id": "cb1",
            "data": data,
            "from": {"id": chat_id},
            "message": {"message_id": message_id, "chat": {"id": chat_id}},
        }
    }


def test_feedback_callback_without_customer_bot_token_is_ignored(monkeypatch):
    monkeypatch.delenv("TELEGRAM_CUSTOMER_BOT_TOKEN", raising=False)
    resp = _run(telegram_webhook(WEBHOOK_SECRET, "customer", _FakeRequest(_feedback_update("fb:7:99:celebrate"))))
    assert resp.status_code == 200


def _mock_get_customer_by_telegram(monkeypatch, mapping: dict[int, int]):
    """يموّه customers_api.get_customer_by_telegram: chat_id -> customer_id
    الفعلي الحقيقي (نفس عقد الدالة الحقيقية: dict به customer_id/status، أو
    HTTPException(404) إن لم يكن chat_id مربوطًا بأي عميل)."""

    async def fake_get_customer_by_telegram(chat_id):
        if chat_id not in mapping:
            raise HTTPException(status_code=404, detail="لا يوجد عميل بمعرّف محادثة تيليجرام هذا")
        return {"customer_id": mapping[chat_id], "status": "active"}

    monkeypatch.setattr(telegram_api.customers_api, "get_customer_by_telegram", fake_get_customer_by_telegram)


def test_feedback_callback_calls_submit_feedback_and_answers_and_edits_markup(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CUSTOMER_BOT_TOKEN", "CUSTTOKEN")
    # الزرّ يحمل customer_id=7، والمحادثة (chat_id=12345، الافتراضي بـ
    # _feedback_update) مربوطة فعليًا بنفس العميل 7 — الحالة الشرعية.
    _mock_get_customer_by_telegram(monkeypatch, {12345: 7})

    calls: dict = {}

    async def fake_submit_feedback(customer_id, body):
        calls["submit_feedback"] = (customer_id, body.send_queue_id, body.kind)
        return {"ok": True, "already_recorded": False}

    answered: list[dict] = []

    async def fake_answer_callback_query(self, callback_query_id, *, text=None, show_alert=False):
        answered.append({"id": callback_query_id, "text": text, "show_alert": show_alert})
        return {}

    edited: list[dict] = []

    async def fake_edit_message_reply_markup(self, chat_id, message_id, *, buttons):
        edited.append({"chat_id": chat_id, "message_id": message_id, "buttons": buttons})
        return {}

    monkeypatch.setattr(feedback_api, "submit_feedback", fake_submit_feedback)
    monkeypatch.setattr(telegram_api.TelegramClient, "answer_callback_query", fake_answer_callback_query)
    monkeypatch.setattr(telegram_api.TelegramClient, "edit_message_reply_markup", fake_edit_message_reply_markup)

    resp = _run(
        telegram_webhook(WEBHOOK_SECRET, "customer", _FakeRequest(_feedback_update("fb:7:99:celebrate")))
    )

    assert resp.status_code == 200
    assert calls["submit_feedback"] == (7, 99, "celebrate")
    assert len(answered) == 1
    assert "🎉" in answered[0]["text"]
    assert answered[0]["show_alert"] is False
    assert len(edited) == 1
    assert edited[0]["buttons"] == []


def test_feedback_callback_thumbs_down_confirmation_text(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CUSTOMER_BOT_TOKEN", "CUSTTOKEN")
    _mock_get_customer_by_telegram(monkeypatch, {12345: 7})

    async def fake_submit_feedback(customer_id, body):
        return {"ok": True, "already_recorded": False}

    answered: list[dict] = []

    async def fake_answer_callback_query(self, callback_query_id, *, text=None, show_alert=False):
        answered.append({"text": text})
        return {}

    async def fake_edit_message_reply_markup(self, chat_id, message_id, *, buttons):
        return {}

    monkeypatch.setattr(feedback_api, "submit_feedback", fake_submit_feedback)
    monkeypatch.setattr(telegram_api.TelegramClient, "answer_callback_query", fake_answer_callback_query)
    monkeypatch.setattr(telegram_api.TelegramClient, "edit_message_reply_markup", fake_edit_message_reply_markup)

    resp = _run(
        telegram_webhook(WEBHOOK_SECRET, "customer", _FakeRequest(_feedback_update("fb:7:99:thumbs_down")))
    )
    assert resp.status_code == 200
    assert "👎" in answered[0]["text"]


def test_feedback_callback_malformed_data_answers_generic_error(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CUSTOMER_BOT_TOKEN", "CUSTTOKEN")

    answered: list[dict] = []

    async def fake_answer_callback_query(self, callback_query_id, *, text=None, show_alert=False):
        answered.append({"text": text})
        return {}

    monkeypatch.setattr(telegram_api.TelegramClient, "answer_callback_query", fake_answer_callback_query)

    resp = _run(
        telegram_webhook(WEBHOOK_SECRET, "customer", _FakeRequest(_feedback_update("fb:not-a-number:99:celebrate")))
    )
    assert resp.status_code == 200
    assert answered[0]["text"] == "⚠️ طلب غير صالح"


def test_feedback_callback_unknown_kind_answers_generic_error(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CUSTOMER_BOT_TOKEN", "CUSTTOKEN")

    answered: list[dict] = []

    async def fake_answer_callback_query(self, callback_query_id, *, text=None, show_alert=False):
        answered.append({"text": text})
        return {}

    monkeypatch.setattr(telegram_api.TelegramClient, "answer_callback_query", fake_answer_callback_query)

    resp = _run(
        telegram_webhook(WEBHOOK_SECRET, "customer", _FakeRequest(_feedback_update("fb:7:99:not_a_kind")))
    )
    assert resp.status_code == 200
    assert answered[0]["text"] == "⚠️ طلب غير صالح"


def test_feedback_callback_http_exception_shows_alert(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CUSTOMER_BOT_TOKEN", "CUSTTOKEN")
    _mock_get_customer_by_telegram(monkeypatch, {12345: 7})

    async def fake_submit_feedback(customer_id, body):
        raise HTTPException(status_code=404, detail="لا يوجد تقديم بهذا المعرّف لهذا العميل")

    answered: list[dict] = []

    async def fake_answer_callback_query(self, callback_query_id, *, text=None, show_alert=False):
        answered.append({"text": text, "show_alert": show_alert})
        return {}

    monkeypatch.setattr(feedback_api, "submit_feedback", fake_submit_feedback)
    monkeypatch.setattr(telegram_api.TelegramClient, "answer_callback_query", fake_answer_callback_query)

    resp = _run(
        telegram_webhook(WEBHOOK_SECRET, "customer", _FakeRequest(_feedback_update("fb:7:99:celebrate")))
    )
    assert resp.status_code == 200
    assert answered[0]["show_alert"] is True
    assert "لا يوجد تقديم" in answered[0]["text"]


# ---------------------------------------------------------------------------
# REVIEW.md البند 8.1 [blocker] — انتحال هوية عبر customer_id مزيَّف
# بـcallback_data: customer_id المكتوب بالزر رقم مكشوف/قابل للتخمين، ولا
# يجوز الوثوق به بلا تحقّق ضد chat_id الفعلي الذي أرسل الضغطة (عبر
# customers_api.get_customer_by_telegram).
# ---------------------------------------------------------------------------


def test_feedback_callback_spoofed_customer_id_is_rejected_without_submitting(monkeypatch):
    """chat_id=12345 عميل حقيقي (customer_id=7 فعليًا) لكنه يرسل callback_data
    بـcustomer_id=42 (عميل آخر مختلف تمامًا، خمَّن رقمًا تسلسليًا آخر) —
    يجب الرفض الصامت بلا استدعاء submit_feedback إطلاقًا."""
    monkeypatch.setenv("TELEGRAM_CUSTOMER_BOT_TOKEN", "CUSTTOKEN")
    _mock_get_customer_by_telegram(monkeypatch, {12345: 7})

    submit_called = {"called": False}

    async def fake_submit_feedback(customer_id, body):
        submit_called["called"] = True
        return {"ok": True, "already_recorded": False}

    answered: list[dict] = []

    async def fake_answer_callback_query(self, callback_query_id, *, text=None, show_alert=False):
        answered.append({"text": text, "show_alert": show_alert})
        return {}

    edited: list[dict] = []

    async def fake_edit_message_reply_markup(self, chat_id, message_id, *, buttons):
        edited.append({"chat_id": chat_id})
        return {}

    monkeypatch.setattr(feedback_api, "submit_feedback", fake_submit_feedback)
    monkeypatch.setattr(telegram_api.TelegramClient, "answer_callback_query", fake_answer_callback_query)
    monkeypatch.setattr(telegram_api.TelegramClient, "edit_message_reply_markup", fake_edit_message_reply_markup)

    resp = _run(
        telegram_webhook(
            WEBHOOK_SECRET, "customer", _FakeRequest(_feedback_update("fb:42:99:thumbs_down", chat_id=12345))
        )
    )

    assert resp.status_code == 200
    assert submit_called["called"] is False  # لم يُسجَّل أي تقييم كاذب باسم العميل 42
    assert len(answered) == 1
    text = answered[0]["text"]
    assert text is not None
    # لا كشف للسبب الحقيقي بنص الرد — لا "mismatch"، لا رقم customer_id،
    # لا أي ما يفيد بأن الزر مزوَّر أو أن هناك عميلًا آخر بالمرة
    for leak in ("mismatch", "7", "42", "customer_id"):
        assert leak not in text
    assert len(edited) == 0  # لا إزالة لوحة أزرار — لا تفاعل أبعد من الرفض


def test_feedback_callback_unlinked_chat_is_rejected_without_submitting(monkeypatch):
    """chat_id غير مربوط بأي عميل إطلاقًا (لا سجلّ بـcustomers.telegram_chat_id)
    يحاول ضغطة تغذية راجعة بـcustomer_id مخمَّن — نفس الرفض الصامت."""
    monkeypatch.setenv("TELEGRAM_CUSTOMER_BOT_TOKEN", "CUSTTOKEN")
    _mock_get_customer_by_telegram(monkeypatch, {})  # لا أحد مربوط

    submit_called = {"called": False}

    async def fake_submit_feedback(customer_id, body):
        submit_called["called"] = True
        return {"ok": True, "already_recorded": False}

    answered: list[dict] = []

    async def fake_answer_callback_query(self, callback_query_id, *, text=None, show_alert=False):
        answered.append({"text": text})
        return {}

    monkeypatch.setattr(feedback_api, "submit_feedback", fake_submit_feedback)
    monkeypatch.setattr(telegram_api.TelegramClient, "answer_callback_query", fake_answer_callback_query)

    resp = _run(
        telegram_webhook(
            WEBHOOK_SECRET, "customer", _FakeRequest(_feedback_update("fb:7:99:celebrate", chat_id=99999))
        )
    )

    assert resp.status_code == 200
    assert submit_called["called"] is False
    assert len(answered) == 1
