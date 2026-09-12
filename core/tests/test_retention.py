"""اختبارات core/app/retention.py (B8: بديل ركفلو n8n السابق
job-bot-customer-retention-auto__B2PYcIMOQN7i5VXA.json). تُحاكي كل طبقة
القاعدة (اتصال/معاملة SQLAlchemy) بكائنات بديلة بسيطة — بلا قاعدة بيانات
حقيقية — وتُحاكي telegram_notify.send_message/telegram_notify_admin.notify_admin
بالكامل، فتختبر فقط منطق الفلترة/الإشعار/التسجيل بـretention.py نفسه.

تشغيل: cd core && python -m pytest tests/test_retention.py -v
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app import retention


class _FakeConn:
    """بديل بسيط لـConnection — يُسجّل كل استدعاء execute() (خصوصًا
    UPDATE ...) دون تنفيذ SQL فعلي، subscriptions/customers نفسها تُقدّم
    عبر monkeypatch على _fetch_reminder_candidates/_fetch_expiry_candidates/
    _fetch_customer (راجع الاختبارات أدناه) لا عبر هذا الكائن."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, dict]] = []

    def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params or {}))
        return self

    def mappings(self):
        return self

    def all(self):
        return []

    def first(self):
        return None


class _FakeEngineCtx:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self) -> None:
        self.conn = _FakeConn()

    def begin(self):
        return _FakeEngineCtx(self.conn)

    def connect(self):
        return _FakeEngineCtx(self.conn)


def _utc(dt_riyadh_naive: datetime) -> datetime:
    """يحوّل وقت رياض ساذج إلى UTC حقيقي (عكس pacing.to_riyadh_naive) —
    مطابق لكيفية تخزين subscriptions.ends_at فعليًا."""
    return (dt_riyadh_naive - timedelta(hours=3)).replace(tzinfo=timezone.utc)


def test_send_reminders_sends_within_window_and_marks_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    today = date(2026, 9, 10)
    ends_at = _utc(datetime(2026, 9, 12, 10, 0))  # يومان متبقيان — ضمن REMINDER_WINDOW_DAYS
    subs = [{"id": 1, "customer_id": 100, "ends_at": ends_at, "reminder_sent_date": None}]

    monkeypatch.setattr(retention, "_fetch_reminder_candidates", lambda conn: subs)
    monkeypatch.setattr(
        retention, "_fetch_customer",
        lambda conn, cid: {"id": cid, "name": "أحمد", "phone": "0500000000", "telegram_chat_id": 777},
    )

    sent_msgs = []
    monkeypatch.setattr(
        retention, "send_message", lambda chat_id, text, **kw: sent_msgs.append((chat_id, text, kw))
    )
    monkeypatch.setattr(retention, "support_whatsapp", lambda: "+966500000000")
    admin_msgs = []
    monkeypatch.setattr(retention, "notify_admin", lambda text: admin_msgs.append(text) or True)

    engine = _FakeEngine()
    result = retention._send_reminders(engine, today)

    assert result["reminders_sent"] == 1
    assert result["reminders_skipped_no_chat"] == 0
    assert result["reminders_errors"] == 0
    assert len(sent_msgs) == 1
    assert sent_msgs[0][0] == 777
    assert "خلال 2 يوم" in sent_msgs[0][1]  # days_left محسوب صحيحًا (Sep12 - Sep10 = يومان)
    assert "+966500000000" in sent_msgs[0][1]
    # B4/v2-B6: زرّا التجديد/التواصل بدل نص واتساب فقط.
    assert sent_msgs[0][2]["reply_markup"] == retention._ACTION_KEYBOARD
    assert len(admin_msgs) == 1
    # UPDATE ...reminder_sent_date تم تنفيذه فعليًا (سجل واحد فقط بهذا الاختبار).
    assert any("reminder_sent_date" in stmt for stmt, _ in engine.conn.executed)


def test_send_reminders_skips_outside_window(monkeypatch: pytest.MonkeyPatch) -> None:
    today = date(2026, 9, 10)
    ends_at = _utc(datetime(2026, 9, 30, 10, 0))  # بعيد جدًا — خارج REMINDER_WINDOW_DAYS
    subs = [{"id": 2, "customer_id": 200, "ends_at": ends_at, "reminder_sent_date": None}]
    monkeypatch.setattr(retention, "_fetch_reminder_candidates", lambda conn: subs)
    monkeypatch.setattr(retention, "_fetch_customer", lambda conn, cid: {"telegram_chat_id": 1, "name": "س"})
    sent_msgs = []
    monkeypatch.setattr(retention, "send_message", lambda *a, **kw: sent_msgs.append(a))
    monkeypatch.setattr(retention, "notify_admin", lambda text: True)

    result = retention._send_reminders(_FakeEngine(), today)
    assert result["reminders_sent"] == 0
    assert sent_msgs == []


def test_send_reminders_skips_if_already_reminded_today(monkeypatch: pytest.MonkeyPatch) -> None:
    today = date(2026, 9, 10)
    ends_at = _utc(datetime(2026, 9, 11, 10, 0))
    subs = [{"id": 3, "customer_id": 300, "ends_at": ends_at, "reminder_sent_date": today}]
    monkeypatch.setattr(retention, "_fetch_reminder_candidates", lambda conn: subs)
    monkeypatch.setattr(retention, "_fetch_customer", lambda conn, cid: {"telegram_chat_id": 1, "name": "س"})
    sent_msgs = []
    monkeypatch.setattr(retention, "send_message", lambda *a, **kw: sent_msgs.append(a))
    monkeypatch.setattr(retention, "notify_admin", lambda text: True)

    result = retention._send_reminders(_FakeEngine(), today)
    assert result["reminders_sent"] == 0
    assert sent_msgs == []


def test_send_reminders_no_chat_id_still_notifies_admin_and_marks_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    today = date(2026, 9, 10)
    ends_at = _utc(datetime(2026, 9, 11, 10, 0))
    subs = [{"id": 4, "customer_id": 400, "ends_at": ends_at, "reminder_sent_date": None}]
    monkeypatch.setattr(retention, "_fetch_reminder_candidates", lambda conn: subs)
    monkeypatch.setattr(
        retention, "_fetch_customer", lambda conn, cid: {"name": "س", "phone": None, "telegram_chat_id": None}
    )
    sent_msgs = []
    monkeypatch.setattr(retention, "send_message", lambda *a, **kw: sent_msgs.append(a))
    admin_msgs = []
    monkeypatch.setattr(retention, "notify_admin", lambda text: admin_msgs.append(text) or True)

    result = retention._send_reminders(_FakeEngine(), today)
    assert result["reminders_sent"] == 1
    assert result["reminders_skipped_no_chat"] == 1
    assert sent_msgs == []
    assert len(admin_msgs) == 1


def test_send_expiry_notices_sends_and_marks_notified(monkeypatch: pytest.MonkeyPatch) -> None:
    subs = [{"id": 9, "customer_id": 900}]
    monkeypatch.setattr(retention, "_fetch_expiry_candidates", lambda conn: subs)
    monkeypatch.setattr(
        retention, "_fetch_customer",
        lambda conn, cid: {"name": "منى", "phone": "0511111111", "telegram_chat_id": 55},
    )
    sent_msgs = []
    monkeypatch.setattr(
        retention, "send_message", lambda chat_id, text, **kw: sent_msgs.append((chat_id, text, kw))
    )
    monkeypatch.setattr(retention, "support_whatsapp", lambda: "+966500000000")
    admin_msgs = []
    monkeypatch.setattr(retention, "notify_admin", lambda text: admin_msgs.append(text) or True)

    engine = _FakeEngine()
    result = retention._send_expiry_notices(engine)

    assert result["expiry_sent"] == 1
    assert sent_msgs[0][0] == 55
    assert sent_msgs[0][2]["reply_markup"] == retention._ACTION_KEYBOARD
    assert len(admin_msgs) == 1
    assert any("expiry_notified_at" in stmt for stmt, _ in engine.conn.executed)


def test_run_retention_round_combines_both_phases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retention, "_send_reminders", lambda engine, today: {"reminders_sent": 2, "reminders_skipped_no_chat": 0, "reminders_errors": 0})
    monkeypatch.setattr(retention, "_send_expiry_notices", lambda engine: {"expiry_sent": 1, "expiry_skipped_no_chat": 0, "expiry_errors": 0})

    result = retention.run_retention_round(engine=object(), today=date(2026, 9, 10))
    assert result["ok"] is True
    assert result["date"] == "2026-09-10"
    assert result["reminders_sent"] == 2
    assert result["expiry_sent"] == 1
