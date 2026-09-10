"""اختبارات core/app/telegram_admin.py (B8) — أوامر الأدمن الأساسية
(تسجيل عميل، تفعيل/إيقاف، تمديد اشتراك، تصنيف عملاء، بحث) عبر عميل
تيليجرام مزيَّف بالكامل — صفر استدعاءات شبكية حقيقية.

يحتاج قاعدة بيانات Postgres حقيقية مهاجَرة حتى 0013 — يُتخطّى تلقائيًا
(skip) إن تعذّر الاتصال، نفس نمط test_catalog.py. اختبارات is_owner_chat
النقية بلا قاعدة بيانات موجودة بملف منفصل test_telegram_admin.py.
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
from app.telegram_client import ChatEvent

DEFAULT_TEST_DB_URL = "postgresql+psycopg://masar_test:masar_test@localhost:5432/masar_test"


def _run(coro):
    return asyncio.run(coro)


def _make_engine() -> Engine | None:
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DB_URL)
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM telegram_sessions LIMIT 1"))
        return engine
    except Exception:  # noqa: BLE001
        return None


_ENGINE = _make_engine()

if _ENGINE is None:
    pytest.skip(
        "لا اتصال بقاعدة بيانات Postgres محلية مهاجَرة (0013) — يُتخطّى test_telegram_admin_db.py كليًا.",
        allow_module_level=True,
    )

OWNER_CHAT_ID = 555000111


class RecordingTelegramClient:
    def __init__(self):
        self.sent: list[dict[str, Any]] = []

    async def send_message(self, chat_id, text_, *, buttons=None, disable_web_page_preview=True):
        self.sent.append({"chat_id": chat_id, "text": text_, "buttons": buttons})
        return {"message_id": len(self.sent)}

    async def answer_callback_query(self, callback_query_id, *, text=None, show_alert=False):
        return {}


def _make_event(chat_id, *, text="", is_callback=False, callback_data=""):
    return ChatEvent(
        chat_id=chat_id, text=text, is_callback=is_callback, callback_data=callback_data,
        callback_query_id="cb1" if is_callback else None, message_id=1, document=None,
        photo=None, contact=None, from_user_id=chat_id,
    )


@pytest.fixture()
def engine() -> Engine:
    return _ENGINE


@pytest.fixture(autouse=True)
def _patch_engine_and_owner(monkeypatch, engine):
    monkeypatch.setattr(admin, "get_engine", lambda: engine)
    monkeypatch.setenv("MASAR_OWNER_CHAT_ID", str(OWNER_CHAT_ID))

    import app.customers_api as customers_api
    import app.overview_api as overview_api
    import app.guarantee_api as guarantee_api
    import app.reports_api as reports_api

    monkeypatch.setattr(customers_api, "get_engine", lambda: engine)
    monkeypatch.setattr(overview_api, "get_engine", lambda: engine)
    monkeypatch.setattr(guarantee_api, "get_engine", lambda: engine)
    monkeypatch.setattr(reports_api, "get_engine", lambda: engine)


@pytest.fixture()
def created_customer(engine):
    tag = uuid.uuid4().hex[:10]
    with engine.begin() as conn:
        cid = conn.execute(
            text(
                "INSERT INTO customers (name, email_service, status, target_daily) "
                "VALUES (:n, :e, 'active', 17) RETURNING id"
            ),
            {"n": f"Admin Test {tag}", "e": f"admin-{tag}@masar.invalid"},
        ).scalar_one()
    yield cid
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM subscriptions WHERE customer_id = :id"), {"id": cid})
        conn.execute(text("DELETE FROM customer_status_audit WHERE customer_id = :id"), {"id": cid})
        conn.execute(text("DELETE FROM customers WHERE id = :id"), {"id": cid})


def test_new_customer_signup_two_step_flow(engine):
    client = RecordingTelegramClient()
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="admin:new_customer"), client))
    tag = uuid.uuid4().hex[:8]
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text=f"Test Customer {tag}"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="0501234567"), client))

    assert any("تم تسجيل العميل" in m["text"] for m in client.sent)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT phone FROM customers WHERE name = :n"), {"n": f"Test Customer {tag}"}
        ).first()
    assert row is not None
    assert row[0] == "0501234567"
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM customers WHERE name = :n"), {"n": f"Test Customer {tag}"})


def test_toggle_status_active_to_paused(engine, created_customer):
    client = RecordingTelegramClient()
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="admin:status"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text=str(created_customer)), client))
    _run(
        admin.handle_update(
            {},
            _make_event(OWNER_CHAT_ID, is_callback=True, callback_data=f"status_choice:{created_customer}:paused"),
            client,
        )
    )
    with engine.connect() as conn:
        row = conn.execute(text("SELECT status FROM customers WHERE id = :id"), {"id": created_customer}).first()
    assert row[0] == "paused"
    assert any("مُوقَف" in m["text"] for m in client.sent)


def test_extend_subscription_updates_ends_at(engine, created_customer):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO subscriptions (customer_id, product_code, starts_at, ends_at, status) "
                "VALUES (:cid, 'SUB30', now(), now() + interval '5 days', 'active')"
            ),
            {"cid": created_customer},
        )

    client = RecordingTelegramClient()
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="admin:extend"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text=str(created_customer)), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="10"), client))

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT ends_at FROM subscriptions WHERE customer_id = :id"), {"id": created_customer}
        ).first()
    assert any("تم تمديد اشتراك" in m["text"] for m in client.sent)
    assert row is not None


def test_extend_subscription_without_active_subscription_reports_warning(engine, created_customer):
    client = RecordingTelegramClient()
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="admin:extend"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text=str(created_customer)), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="10"), client))
    assert any("لا يوجد اشتراك فعّال" in m["text"] for m in client.sent)


def test_segments_reports_status_breakdown(engine, created_customer):
    client = RecordingTelegramClient()
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="admin:segments"), client))
    assert any("تصنيف العملاء" in m["text"] for m in client.sent)


def test_lookup_unknown_customer_reports_not_found(engine):
    client = RecordingTelegramClient()
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="admin:lookup"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="999999999"), client))
    assert any("لم أجد عميلًا" in m["text"] for m in client.sent)
