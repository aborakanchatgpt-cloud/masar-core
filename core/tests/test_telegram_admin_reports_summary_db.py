"""B6/v3 — اختبارات توصيل بوت الأدمن لـ"📊 تقرير شامل"
(`app.telegram_admin_reports_summary`): قائمة الفترة، تنفيذ الأزرار السريعة مباشرة،
وتدفّق المدى المخصَّص (خطوتان نصّيتان + رفض صيغة تاريخ خاطئة).

نفس نمط test_telegram_admin_commands_notify_db.py (RecordingTelegramClient +
monkeypatch لـget_engine) — يحتاج قاعدة بيانات Postgres حقيقية مهاجرة،
يُتخطّى تلقائيًا (skip) إن تعذّر الاتصال.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import telegram_admin_reports_summary as summary_mod

DEFAULT_TEST_DB_URL = "postgresql+psycopg://masar_test:masar_test@localhost:5432/masar_test"


def _run(coro):
    return asyncio.run(coro)


def _make_engine() -> Engine | None:
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DB_URL)
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception:  # noqa: BLE001
        return None


_ENGINE = _make_engine()

if _ENGINE is None:
    pytest.skip(
        "لا اتصال بقاعدة بيانات Postgres محلية للاختبار — يُتخطّى "
        "test_telegram_admin_reports_summary_db.py كليًا.",
        allow_module_level=True,
    )


class RecordingTelegramClient:
    def __init__(self):
        self.sent: list[dict[str, Any]] = []

    async def send_message(self, chat_id, text_, *, buttons=None, disable_web_page_preview=True):
        self.sent.append({"chat_id": chat_id, "text": text_, "buttons": buttons})
        return {"message_id": len(self.sent)}


class FakeSession:
    """محاكاة بسيطة لـ_save_session/_get_session/_clear_session بملف
    telegram_admin.py (بلا حاجة لجدول telegram_sessions الفعلي بهذا
    الاختبار — الوظيفتان المُمرَّرتان لملف التوصيل مجرّد callbacks)."""

    def __init__(self):
        self.step: str | None = None
        self.data: dict[str, Any] = {}

    def save(self, chat_id: int, step: str, data: dict[str, Any]) -> None:
        self.step, self.data = step, data

    def clear(self, chat_id: int) -> None:
        self.step, self.data = None, {}


@pytest.fixture()
def engine() -> Engine:
    return _ENGINE


@pytest.fixture(autouse=True)
def _patch_engine(monkeypatch, engine):
    monkeypatch.setattr(summary_mod, "get_engine", lambda: engine)


@pytest.fixture()
def customer(engine):
    tag = uuid.uuid4().hex[:10]
    with engine.begin() as conn:
        customer_id = conn.execute(
            text(
                "INSERT INTO customers (name, email_service, status, target_daily) "
                "VALUES (:n, :e, 'active', 17) RETURNING id"
            ),
            {"n": f"Summary Wiring {tag}", "e": f"summary-wiring-{tag}@masar.invalid"},
        ).scalar_one()
    yield customer_id
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM payment_requests WHERE customer_id = :cid"), {"cid": customer_id})
        conn.execute(text("DELETE FROM customers WHERE id = :cid"), {"cid": customer_id})


async def _menu():
    client = RecordingTelegramClient()
    await summary_mod.reply_summary_menu(client, 111)
    return client


def test_reply_summary_menu_shows_quick_periods_and_custom_button():
    client = _run(_menu())
    assert len(client.sent) == 1
    buttons = client.sent[0]["buttons"]
    flat = [b["callback_data"] for row in buttons for b in row]
    assert "summary:period:day" in flat
    assert "summary:period:week" in flat
    assert "summary:period:month" in flat
    assert "summary:custom" in flat


def test_send_period_report_sends_a_message_with_report_text(customer):
    client = RecordingTelegramClient()
    _run(summary_mod.send_period_report(client, 222, "week"))
    assert len(client.sent) == 1
    assert "📊 التقرير الشامل" in client.sent[0]["text"]


def test_start_custom_range_saves_from_step_and_prompts():
    client = RecordingTelegramClient()
    session = FakeSession()
    _run(summary_mod.start_custom_range(client, 333, session.save))
    assert session.step == "summary_custom_from"
    assert "YYYY-MM-DD" in client.sent[0]["text"]


def test_custom_range_rejects_bad_from_date_format():
    client = RecordingTelegramClient()
    session = FakeSession()
    _run(
        summary_mod.handle_custom_step_text(
            "summary_custom_from", 444, client, {}, "2026/09/01", session.save, session.clear
        )
    )
    assert "⚠️" in client.sent[0]["text"]
    assert session.step is None  # لم تُحفَظ جلسة جديدة عند الرفض


def test_custom_range_from_then_to_produces_report(customer):
    client = RecordingTelegramClient()
    session = FakeSession()
    _run(
        summary_mod.handle_custom_step_text(
            "summary_custom_from", 555, client, {}, "2026-09-01", session.save, session.clear
        )
    )
    assert session.step == "summary_custom_to"
    assert session.data == {"from": "2026-09-01"}

    _run(
        summary_mod.handle_custom_step_text(
            "summary_custom_to", 555, client, session.data, "2026-09-11", session.save, session.clear
        )
    )
    assert session.step is None  # clear_session استُدعيت بعد إتمام الخطوتين
    assert "📊 التقرير الشامل" in client.sent[-1]["text"]
    assert "من 2026-09-01 إلى 2026-09-11" in client.sent[-1]["text"]


def test_custom_range_rejects_bad_to_date_format_and_keeps_from():
    client = RecordingTelegramClient()
    session = FakeSession()
    session.save(666, "summary_custom_to", {"from": "2026-09-01"})
    _run(
        summary_mod.handle_custom_step_text(
            "summary_custom_to", 666, client, session.data, "not-a-date", session.save, session.clear
        )
    )
    assert "⚠️" in client.sent[0]["text"]
    assert session.step == "summary_custom_to"  # الجلسة لم تُمسَح — لا يزال بانتظار تاريخ نهاية صحيح
    assert session.data == {"from": "2026-09-01"}
