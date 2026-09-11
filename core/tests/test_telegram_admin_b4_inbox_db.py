"""اختبارات B4 — قناة "📞 تواصل معنا" المصنَّفة (عميل → أدمن) عبر
`telegram_onboarding.handle_update`، وزرّ "↩️ رد" على إشعار الأدمن عبر
`telegram_admin.handle_update` (يعيد استخدام `send_customer_message_and_log`
الموجودة). نفس نمط `test_telegram_admin_b3_followup_db.py`.

يحتاج قاعدة بيانات Postgres حقيقية مهاجَرة حتى 0020 (عمود category بجدول
customer_messages) — يُتخطّى تلقائيًا (skip) إن تعذّر الاتصال.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import telegram_admin as admin
from app import telegram_admin_search as search_mod
from app import telegram_onboarding as ob
from app.telegram_client import ChatEvent

DEFAULT_TEST_DB_URL = "postgresql+psycopg://masar_test:masar_test@localhost:5432/masar_test"


def _run(coro):
    return asyncio.run(coro)


def _make_engine() -> Engine | None:
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DB_URL)
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT category FROM customer_messages LIMIT 1"))
        return engine
    except Exception:  # noqa: BLE001
        return None


_ENGINE = _make_engine()

if _ENGINE is None:
    pytest.skip(
        "لا اتصال بقاعدة بيانات Postgres محلية مهاجَرة (0020) — يُتخطّى test_telegram_admin_b4_inbox_db.py كليًا.",
        allow_module_level=True,
    )

OWNER_CHAT_ID = 555000444


class RecordingTelegramClient:
    def __init__(self):
        self.sent: list[dict[str, Any]] = []

    async def send_message(self, chat_id, text_, *, buttons=None, disable_web_page_preview=True):
        self.sent.append({"chat_id": chat_id, "text": text_, "buttons": buttons})
        return {"message_id": len(self.sent)}

    async def answer_callback_query(self, callback_query_id, *, text=None, show_alert=False):
        return {}


def _make_event(chat_id, *, text="", is_callback=False, callback_data="", from_user_id=None):
    return ChatEvent(
        chat_id=chat_id, text=text, is_callback=is_callback, callback_data=callback_data,
        callback_query_id="cb1" if is_callback else None, message_id=1, document=None,
        photo=None, contact=None, from_user_id=from_user_id if from_user_id is not None else chat_id,
    )


@pytest.fixture()
def engine() -> Engine:
    return _ENGINE


@pytest.fixture(autouse=True)
def _patch_engine_and_owner(monkeypatch, engine):
    monkeypatch.setattr(admin, "get_engine", lambda: engine)
    monkeypatch.setattr(search_mod, "get_engine", lambda: engine)
    monkeypatch.setattr(ob, "get_engine", lambda: engine)
    monkeypatch.setenv("MASAR_OWNER_CHAT_ID", str(OWNER_CHAT_ID))


@pytest.fixture()
def created_customer(engine):
    created_ids: list[int] = []

    def _factory(*, telegram_chat_id: int | None = None, name: str | None = None):
        tag = uuid.uuid4().hex[:10]
        with engine.begin() as conn:
            cid = conn.execute(
                text(
                    "INSERT INTO customers (name, email_service, status, target_daily, telegram_chat_id) "
                    "VALUES (:n, :e, 'active', 17, :tg) RETURNING id"
                ),
                {"n": name or f"B4Inbox {tag}", "e": f"b4inbox-{tag}@masar.invalid", "tg": telegram_chat_id},
            ).scalar_one()
        created_ids.append(cid)
        return cid

    yield _factory

    with engine.begin() as conn:
        for cid in created_ids:
            conn.execute(text("DELETE FROM customer_messages WHERE customer_id = :id"), {"id": cid})
            conn.execute(text("DELETE FROM telegram_sessions WHERE chat_id = ANY(:ids)"), {"ids": [cid, OWNER_CHAT_ID]})
            conn.execute(text("DELETE FROM customers WHERE id = :id"), {"id": cid})


def _active_state() -> dict[str, Any]:
    # onboarding_done يتطلّب cv_pdf_path + cities + families — لسنا نختبر
    # مسار onboarding هنا، نبني state جاهزة كما لو اكتمل الملف بالفعل.
    return {"name": "أحمد التجريبي", "phone": "966500000000", "status": "active"}


def test_contact_flow_asks_category_then_logs_and_notifies_admin_with_reply_button(engine, created_customer, monkeypatch):
    customer_id = created_customer()
    chat_id = customer_id + 10**9
    client = RecordingTelegramClient()
    admin_recorder = RecordingTelegramClient()
    monkeypatch.setattr(ob, "get_admin_bot_client", lambda: admin_recorder)
    state = _active_state()

    # 1) اختيار "📞 تواصل معنا" — يعرض 3 أزرار فئة، بلا سؤال نص مباشرة
    _run(ob._handle_active_menu(_make_event(chat_id, is_callback=True, callback_data="menu:contact"), client, customer_id, state))
    last = client.sent[-1]
    assert last["buttons"] is not None
    cat_callbacks = [b["callback_data"] for row in last["buttons"] for b in row]
    assert "contact:cat:complaint" in cat_callbacks
    assert "contact:cat:heart_to_heart" in cat_callbacks
    assert "contact:cat:note" in cat_callbacks

    # 2) اختيار فئة "شكوى"
    _run(ob._handle_active_menu(_make_event(chat_id, is_callback=True, callback_data="contact:cat:complaint"), client, customer_id, state))
    step, data = ob._get_session(chat_id)
    assert step == "awaiting_contact_message"
    assert data["category"] == "complaint"

    # 3) كتابة الرسالة — يجب أن تُسجَّل direction='in' + category='complaint'
    #    وتُشعِر الأدمن بزر "↩️ رد" يحمل معرّف الرسالة الجديد
    _run(ob._handle_active_menu(_make_event(chat_id, text="التقرير اليومي متأخر"), client, customer_id, state))

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, direction, category, text FROM customer_messages WHERE customer_id = :cid ORDER BY id DESC LIMIT 1"),
            {"cid": customer_id},
        ).mappings().first()
    assert row is not None
    assert row["direction"] == "in"
    assert row["category"] == "complaint"
    assert row["text"] == "التقرير اليومي متأخر"

    admin_notice = admin_recorder.sent[-1]
    assert admin_notice["chat_id"] == str(OWNER_CHAT_ID)
    assert "😔 شكوى" in admin_notice["text"]
    reply_callbacks = [b["callback_data"] for row_ in admin_notice["buttons"] for b in row_]
    assert f"inbox:reply:{row['id']}" in reply_callbacks

    # الجلسة انمسحت بعد الإرسال
    step2, _ = ob._get_session(chat_id)
    assert step2 == ""


def test_admin_reply_button_delivers_message_and_logs_out(engine, created_customer, monkeypatch):
    customer_chat_id = 900777
    customer_id = created_customer(telegram_chat_id=customer_chat_id)
    with engine.begin() as conn:
        message_id = conn.execute(
            text(
                "INSERT INTO customer_messages (customer_id, direction, category, text) "
                "VALUES (:cid, 'in', 'note', 'ملاحظة تجريبية') RETURNING id"
            ),
            {"cid": customer_id},
        ).scalar_one()

    sent_to_customer: list[dict[str, Any]] = []

    def fake_send(chat_id, message_text):
        sent_to_customer.append({"chat_id": chat_id, "text": message_text})

    monkeypatch.setattr(search_mod, "send_customer_message", fake_send)
    admin_client = RecordingTelegramClient()

    # 1) الأدمن يضغط "↩️ رد" على الإشعار
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data=f"inbox:reply:{message_id}"), admin_client))
    step, data = admin._get_session(OWNER_CHAT_ID)
    assert step == "inbox_reply"
    assert data["customer_id"] == customer_id

    # 2) الأدمن يكتب الردّ
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="تم التحقق، شكرًا لتنبيهك"), admin_client))

    assert sent_to_customer == [{"chat_id": customer_chat_id, "text": "تم التحقق، شكرًا لتنبيهك"}]
    with engine.connect() as conn:
        out_row = conn.execute(
            text("SELECT direction, category, text FROM customer_messages WHERE customer_id = :cid ORDER BY id DESC LIMIT 1"),
            {"cid": customer_id},
        ).mappings().first()
    assert out_row["direction"] == "out"
    assert out_row["category"] is None
    assert out_row["text"] == "تم التحقق، شكرًا لتنبيهك"

    step2, _ = admin._get_session(OWNER_CHAT_ID)
    assert step2 == ""


def test_inbox_reply_unknown_message_id_tells_admin_and_starts_no_step(engine, created_customer):
    admin_client = RecordingTelegramClient()
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="inbox:reply:999999999"), admin_client))
    assert any("ما قدرت ألقى" in m["text"] for m in admin_client.sent)
    step, _ = admin._get_session(OWNER_CHAT_ID)
    assert step == ""


def test_fetch_inbox_message_returns_none_for_outbound_direction(engine, created_customer):
    customer_id = created_customer()
    with engine.begin() as conn:
        out_id = conn.execute(
            text("INSERT INTO customer_messages (customer_id, direction, text) VALUES (:cid, 'out', 'رد سابق') RETURNING id"),
            {"cid": customer_id},
        ).scalar_one()
    assert search_mod.fetch_inbox_message(out_id) is None
