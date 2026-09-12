"""اختبارات core/app/reports_relay.py (B8: بديل ركفلو n8n السابق
masar_daily_report_relay.json). تُحاكي طبقة القاعدة (reports.fetch_pending_reports
إلخ) وطبقة الإرسال (telegram_notify.send_message) بالكامل عبر monkeypatch —
بلا اتصال قاعدة بيانات حقيقي وبلا أي اتصال شبكة، فقط منطق التوجيه/التقسيم/
تعليم التسليم بـreports_relay.py نفسه.

تشغيل: cd core && python -m pytest tests/test_reports_relay.py -v
"""
from __future__ import annotations

import pytest

from app import reports, reports_relay


class _FakeEngine:
    """كائن بديل بلا أي منطق — كل الدوال التي تستقبله بهذا الملف مُموّهة
    (monkeypatched)، فلا تستدعيه فعليًا؛ يمرَّر فقط لأن run_relay_round
    يتوقّع Engine بتوقيعه."""


def _pending_row(report_id: int, customer_id: int, *, text_body: str = "نص التقرير", apps: list | None = None) -> dict:
    return {
        "id": report_id,
        "customer_id": customer_id,
        "report_date": "2026-09-10",
        "payload": {"text": text_body, "today_applications": apps or []},
        "status": "queued",
        "channel": None,
    }


def test_run_relay_round_delivers_and_marks_delivered(monkeypatch: pytest.MonkeyPatch) -> None:
    pending = [_pending_row(1, 100, apps=[{"send_queue_id": 5}])]
    sent_calls: list[dict] = []
    delivered_calls: list[tuple[int, str]] = []

    monkeypatch.setattr(reports, "fetch_pending_reports", lambda engine, limit=200: pending)
    monkeypatch.setattr(reports, "fetch_customer_chat_id", lambda engine, cid: 777)

    def fake_mark_delivered(engine, report_id, channel):
        delivered_calls.append((report_id, channel))
        return {"id": report_id, "status": "delivered"}

    monkeypatch.setattr(reports, "mark_report_delivered", fake_mark_delivered)

    def fake_send_message(chat_id, text, *, reply_markup=None, **kw):
        sent_calls.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return {"ok": True}

    monkeypatch.setattr(reports_relay, "send_message", fake_send_message)

    result = reports_relay.run_relay_round(engine=_FakeEngine())

    assert result == {"ok": True, "pending_count": 1, "delivered": 1, "skipped_no_chat": 0, "errors": 0}
    assert len(sent_calls) == 1
    assert sent_calls[0]["chat_id"] == 777
    assert sent_calls[0]["text"] == "نص التقرير"
    # لوحة أزرار مرفقة (send_queue_id=5 موجود) على الجزء الوحيد (الأخير).
    assert sent_calls[0]["reply_markup"] == {
        "inline_keyboard": [[{"text": "👎", "callback_data": "fb:100:5:thumbs_down"},
                              {"text": "🎉", "callback_data": "fb:100:5:celebrate"}]]
    }
    assert delivered_calls == [(1, "telegram")]


def test_run_relay_round_skips_customer_without_chat_id(monkeypatch: pytest.MonkeyPatch) -> None:
    pending = [_pending_row(2, 200)]
    monkeypatch.setattr(reports, "fetch_pending_reports", lambda engine, limit=200: pending)
    monkeypatch.setattr(reports, "fetch_customer_chat_id", lambda engine, cid: None)

    delivered_calls = []
    monkeypatch.setattr(reports, "mark_report_delivered", lambda *a, **kw: delivered_calls.append(a) or {"id": 2, "status": "delivered"})

    send_calls = []
    monkeypatch.setattr(reports_relay, "send_message", lambda *a, **kw: send_calls.append(a))

    result = reports_relay.run_relay_round(engine=_FakeEngine())

    assert result["skipped_no_chat"] == 1
    assert result["delivered"] == 0
    assert send_calls == []
    assert delivered_calls == []  # لا تعليم كمُسلّم — يبقى queued لمحاولة لاحقة


def test_run_relay_round_splits_long_text_and_attaches_keyboard_only_to_last_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    long_text = "\n".join(f"سطر رقم {i} " + ("طويل " * 50) for i in range(50))
    pending = [_pending_row(3, 300, text_body=long_text, apps=[{"send_queue_id": 9}])]

    monkeypatch.setattr(reports, "fetch_pending_reports", lambda engine, limit=200: pending)
    monkeypatch.setattr(reports, "fetch_customer_chat_id", lambda engine, cid: 42)
    monkeypatch.setattr(reports, "mark_report_delivered", lambda *a, **kw: {"id": 3, "status": "delivered"})

    sent_calls = []
    monkeypatch.setattr(
        reports_relay, "send_message",
        lambda chat_id, text, *, reply_markup=None, **kw: sent_calls.append(reply_markup),
    )

    result = reports_relay.run_relay_round(engine=_FakeEngine())

    assert result["delivered"] == 1
    assert len(sent_calls) > 1
    # كل الأجزاء بلا لوحة أزرار عدا الأخير.
    assert all(rm is None for rm in sent_calls[:-1])
    assert sent_calls[-1] is not None


def test_run_relay_round_counts_error_and_continues_on_send_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    pending = [_pending_row(4, 400), _pending_row(5, 500)]
    monkeypatch.setattr(reports, "fetch_pending_reports", lambda engine, limit=200: pending)
    monkeypatch.setattr(reports, "fetch_customer_chat_id", lambda engine, cid: 1)

    delivered_calls = []
    monkeypatch.setattr(reports, "mark_report_delivered", lambda engine, rid, ch: delivered_calls.append(rid) or {"id": rid, "status": "delivered"})

    call_count = {"n": 0}

    def fake_send(chat_id, text, *, reply_markup=None, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("network boom")

    monkeypatch.setattr(reports_relay, "send_message", fake_send)

    result = reports_relay.run_relay_round(engine=_FakeEngine())

    assert result["errors"] == 1
    assert result["delivered"] == 1
    assert delivered_calls == [5]  # فقط التقرير الثاني عُلِّم كمُسلّم


def test_run_relay_round_respects_25_per_second_cap_between_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    """B11.5: ثلاثة تقارير (رسالة واحدة لكل منها) لعملاء مختلفين → تنتظر
    الفاصل الأدنى (1/25 ثانية) بين كل إرسال والتالي، لا قبل أول إرسال بالجولة."""
    pending = [_pending_row(i, 100 + i) for i in range(3)]
    monkeypatch.setattr(reports, "fetch_pending_reports", lambda engine, limit=200: pending)
    monkeypatch.setattr(reports, "fetch_customer_chat_id", lambda engine, cid: cid)
    monkeypatch.setattr(reports, "mark_report_delivered", lambda engine, rid, ch: {"id": rid, "status": "delivered"})
    monkeypatch.setattr(reports_relay, "send_message", lambda *a, **kw: {"ok": True})

    fake_clock = {"t": 1000.0}
    monkeypatch.setattr(reports_relay.time, "monotonic", lambda: fake_clock["t"])
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        fake_clock["t"] += seconds  # الوقت المُحاكى يتقدّم فعليًا بمقدار الانتظار

    monkeypatch.setattr(reports_relay, "_sleep_fn", fake_sleep)

    result = reports_relay.run_relay_round(engine=_FakeEngine())

    assert result["delivered"] == 3
    # لا انتظار قبل أول إرسال؛ انتظار الفاصل الأدنى (0.04s) قبل كل إرسال تالژ
    assert len(sleeps) == 2
    for s in sleeps:
        assert s == pytest.approx(reports_relay._MIN_INTERVAL_SECONDS)


def test_run_relay_round_does_not_wait_when_previous_send_already_slower_than_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """B11.5: لو استغرق الإرسال الفعلي نفسه أطول من الفاصل الأدنى أصلًا
    (شبكة بطيئة) → لا انتظار إضافي قبل التالي."""
    pending = [_pending_row(i, 100 + i) for i in range(2)]
    monkeypatch.setattr(reports, "fetch_pending_reports", lambda engine, limit=200: pending)
    monkeypatch.setattr(reports, "fetch_customer_chat_id", lambda engine, cid: cid)
    monkeypatch.setattr(reports, "mark_report_delivered", lambda engine, rid, ch: {"id": rid, "status": "delivered"})

    fake_clock = {"t": 1000.0}
    monkeypatch.setattr(reports_relay.time, "monotonic", lambda: fake_clock["t"])

    def fake_send(chat_id, text, *, reply_markup=None, **kw):
        fake_clock["t"] += 1.0  # إرسال "بطيء" — أبطأ من الفاصل الأدنى بكثير
        return {"ok": True}

    monkeypatch.setattr(reports_relay, "send_message", fake_send)
    sleeps: list[float] = []
    monkeypatch.setattr(reports_relay, "_sleep_fn", lambda s: sleeps.append(s))

    result = reports_relay.run_relay_round(engine=_FakeEngine())

    assert result["delivered"] == 2
    assert sleeps == []
