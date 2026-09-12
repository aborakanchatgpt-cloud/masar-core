"""B4 (دفعة ثانية) — إشعار العميل الفوري عبر بوت العميل عند تفعيل/إيقاف
حسابه (`reply_set_status`) أو تمديد اشتراكه (`reply_extend_subscription`)،
كلاهما بـ`app.telegram_admin_commands` — يعيد استخدام
`app.telegram_notify_admin.notify_customer` (best-effort) و
`app.reports.fetch_customer_chat_id` الموجودتين مسبقًا؛ التغيير الوحيد هنا
هو ربطهما بهاتين الدالتين. نفس نمط test_telegram_admin_b4_inbox_db.py.

يحتاج قاعدة بيانات Postgres حقيقية مهاجرة (subscriptions/customer_status_audit)
— يُتخطى تلقائيًا (skip) إن تعذّر الاتصال.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import customers_api, telegram_admin_commands as cmds
from app import telegram_notify_admin

DEFAULT_TEST_DB_URL = "postgresql+psycopg://masar_test:masar_test@localhost:5432/masar_test"


def _run(coro):
    return asyncio.run(coro)


def _make_engine() -> Engine | None:
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DB_URL)
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM customer_status_audit LIMIT 1"))
            conn.execute(text("SELECT 1 FROM subscriptions LIMIT 1"))
        return engine
    except Exception:  # noqa: BLE001
        return None


_ENGINE = _make_engine()

if _ENGINE is None:
    pytest.skip(
        "لا اتصال بقاعدة بيانات Postgres محلية مهاجرة — يُتخطى test_telegram_admin_commands_notify_db.py كليًا.",
        allow_module_level=True,
    )


class RecordingTelegramClient:
    def __init__(self):
        self.sent: list[dict[str, Any]] = []

    async def send_message(self, chat_id, text_, *, buttons=None, disable_web_page_preview=True):
        self.sent.append({"chat_id": chat_id, "text": text_, "buttons": buttons})
        return {"message_id": len(self.sent)}


@pytest.fixture()
def engine() -> Engine:
    return _ENGINE


@pytest.fixture(autouse=True)
def _patch_engine(monkeypatch, engine):
    monkeypatch.setattr(cmds, "get_engine", lambda: engine)
    monkeypatch.setattr(customers_api, "get_engine", lambda: engine)
    monkeypatch.setattr(telegram_notify_admin, "get_engine", lambda: engine)


@pytest.fixture()
def customer_with_chat(engine):
    created_ids: list[int] = []

    def _factory(*, status: str = "active"):
        tag = uuid.uuid4().hex[:10]
        chat_id = int(uuid.uuid4().int % 900000000) + 10**9
        with engine.begin() as conn:
            cid = conn.execute(
                text(
                    "INSERT INTO customers (name, email_service, status, target_daily, telegram_chat_id) "
                    "VALUES (:n, :e, :st, 17, :tg) RETURNING id"
                ),
                {"n": f"NotifyTest {tag}", "e": f"notify-{tag}@masar.invalid", "st": status, "tg": chat_id},
            ).scalar_one()
        created_ids.append(cid)
        return cid, chat_id

    yield _factory

    with engine.begin() as conn:
        for cid in created_ids:
            conn.execute(text("DELETE FROM customer_status_audit WHERE customer_id = :id"), {"id": cid})
            conn.execute(text("DELETE FROM subscriptions WHERE customer_id = :id"), {"id": cid})
            conn.execute(text("DELETE FROM customers WHERE id = :id"), {"id": cid})


def test_reply_set_status_activate_notifies_customer(engine, customer_with_chat, monkeypatch):
    customer_id, chat_id = customer_with_chat(status="pending")
    sent_calls: list[tuple] = []
    monkeypatch.setattr(telegram_notify_admin, "send_message", lambda cid, txt, **kw: sent_calls.append((cid, txt)))

    admin_client = RecordingTelegramClient()
    _run(cmds.reply_set_status(admin_client, 555, customer_id, "active"))

    assert "تم تحديث حالة العميل" in admin_client.sent[-1]["text"]
    assert len(sent_calls) == 1
    assert sent_calls[0][0] == chat_id
    assert "تم تفعيل حسابك" in sent_calls[0][1]


def test_reply_set_status_pause_notifies_customer(engine, customer_with_chat, monkeypatch):
    customer_id, chat_id = customer_with_chat(status="active")
    sent_calls: list[tuple] = []
    monkeypatch.setattr(telegram_notify_admin, "send_message", lambda cid, txt, **kw: sent_calls.append((cid, txt)))

    admin_client = RecordingTelegramClient()
    _run(cmds.reply_set_status(admin_client, 555, customer_id, "paused"))

    assert len(sent_calls) == 1
    assert sent_calls[0][0] == chat_id
    assert "تم إيقاف حسابك" in sent_calls[0][1]


def test_reply_set_status_without_telegram_chat_id_skips_notify_silently(engine, monkeypatch):
    tag = uuid.uuid4().hex[:10]
    with engine.begin() as conn:
        customer_id = conn.execute(
            text(
                "INSERT INTO customers (name, email_service, status, target_daily) "
                "VALUES (:n, :e, 'pending', 17) RETURNING id"
            ),
            {"n": f"NoTgChat {tag}", "e": f"notgchat-{tag}@masar.invalid"},
        ).scalar_one()
    sent_calls: list[tuple] = []
    monkeypatch.setattr(telegram_notify_admin, "send_message", lambda cid, txt, **kw: sent_calls.append((cid, txt)))

    admin_client = RecordingTelegramClient()
    _run(cmds.reply_set_status(admin_client, 555, customer_id, "active"))

    assert sent_calls == []
    assert "تم تحديث حالة العميل" in admin_client.sent[-1]["text"]

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM customer_status_audit WHERE customer_id = :id"), {"id": customer_id})
        conn.execute(text("DELETE FROM customers WHERE id = :id"), {"id": customer_id})


def test_reply_extend_subscription_notifies_customer(engine, customer_with_chat, monkeypatch):
    customer_id, chat_id = customer_with_chat(status="active")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO subscriptions (customer_id, product_code, status, starts_at, ends_at) "
                "VALUES (:cid, 'SUB30', 'active', now(), now() + interval '10 days')"
            ),
            {"cid": customer_id},
        )
    sent_calls: list[tuple] = []
    monkeypatch.setattr(telegram_notify_admin, "send_message", lambda cid, txt, **kw: sent_calls.append((cid, txt)))

    admin_client = RecordingTelegramClient()
    _run(cmds.reply_extend_subscription(admin_client, 555, customer_id, 30))

    assert "تم تمديد اشتراك العميل" in admin_client.sent[-1]["text"]
    assert len(sent_calls) == 1
    assert sent_calls[0][0] == chat_id
    assert "تم تمديد اشتراكك 30 يومًا" in sent_calls[0][1]
