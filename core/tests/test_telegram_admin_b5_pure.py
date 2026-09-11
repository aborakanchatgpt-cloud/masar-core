"""اختبارات B9/B5 نقية (بلا قاعدة بيانات حقيقية) — صيغة 📊 نظرة عامة
المُنسَّقة (`telegram_admin_commands.reply_overview`، مموّهة بالكامل عبر
`overview_api.overview`)، `telegram_admin_settings._mask_iban` (دالة صرفة)،
و`telegram_admin_search.search_customers` (مموّه بمحرّك مزيّف بسيط — نفس
فلسفة `test_telegram_notify_admin.py`: لا حاجة لـPostgres حقيقي لتغطية
منطق اختيار مسار "رقم" مقابل "اسم" داخل الدالة نفسها).

هذا الملف يُنفّذ محليًا دومًا (لا `pytest.skip`) — خلاف test_telegram_admin_b5_db.py.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app import overview_api
from app import telegram_admin_commands as commands
from app import telegram_admin_search as search_mod
from app import telegram_admin_settings as settings_mod


def _run(coro):
    return asyncio.run(coro)


class RecordingTelegramClient:
    def __init__(self):
        self.sent: list[dict[str, Any]] = []

    async def send_message(self, chat_id, text_, *, buttons=None, disable_web_page_preview=True):
        self.sent.append({"chat_id": chat_id, "text": text_, "buttons": buttons})
        return {"message_id": len(self.sent)}


# ---------------------------------------------------------------------------
# 📊 نظرة عامة — صيغة منسّقة
# ---------------------------------------------------------------------------


def test_reply_overview_formats_all_sections(monkeypatch):
    fake_overview = {
        "customers_by_status": {"active": 5, "pending": 2, "paused": 1, "expired": 0},
        "sends_today": 10,
        "sends_last_7_days": 60,
        "bounces_today": 1,
        "send_queue_by_status": {"queued": 3},
        "mail_links_by_status": {"ok": 4, "failed": 1},
        "pending_reports": 2,
        "pending_guarantees": 1,
        "sends_failed_today": 3,
        "pending_payment_requests": 0,
        "top_families": [["hse", 4], ["quality", 2]],
        "top_cities": [["الرياض", 5]],
        "discovery_freshness": {"latest_job_discovered_at": None},
        "dry_run": False,
    }

    async def fake_overview_call():
        return fake_overview

    monkeypatch.setattr(overview_api, "overview", fake_overview_call)

    client = RecordingTelegramClient()
    _run(commands.reply_overview(client, 12345))

    assert len(client.sent) == 1
    text_ = client.sent[0]["text"]
    assert "نشط 5" in text_
    assert "بانتظار الدفع 2" in text_
    assert "صناديق بريد مربوطة: 4 (فشل: 1)" in text_
    assert "فشل اليوم: 3" in text_
    assert "طلبات دفع معلّقة: 0" in text_
    assert "hse (4)" in text_ and "quality (2)" in text_
    assert "الرياض (5)" in text_
    assert "حقيقي ✅" in text_  # dry_run=False


def test_reply_overview_dry_run_and_empty_families(monkeypatch):
    fake_overview = {
        "customers_by_status": {},
        "sends_today": 0,
        "sends_last_7_days": 0,
        "bounces_today": 0,
        "send_queue_by_status": {},
        "mail_links_by_status": {},
        "pending_reports": 0,
        "pending_guarantees": 0,
        "sends_failed_today": 0,
        "pending_payment_requests": 0,
        "top_families": [],
        "top_cities": [],
        "discovery_freshness": {"latest_job_discovered_at": None},
        "dry_run": True,
    }

    async def fake_overview_call():
        return fake_overview

    monkeypatch.setattr(overview_api, "overview", fake_overview_call)

    client = RecordingTelegramClient()
    _run(commands.reply_overview(client, 12345))

    text_ = client.sent[0]["text"]
    assert "تجريبي 🧪" in text_
    assert "لا بيانات بعد" in text_
    assert "نشط 0" in text_


# ---------------------------------------------------------------------------
# ⚙️ الإعدادات — _mask_iban (دالة صرفة)
# ---------------------------------------------------------------------------


def test_mask_iban_masks_middle_keeps_edges():
    assert settings_mod._mask_iban("SA0380000000608010167519") == "SA03 **** **** 7519"


def test_mask_iban_none_or_empty_reports_undefined():
    assert settings_mod._mask_iban(None) == "غير محدَّد"
    assert settings_mod._mask_iban("") == "غير محدَّد"


def test_mask_iban_short_value_returned_as_is():
    assert settings_mod._mask_iban("SA03") == "SA03"


# ---------------------------------------------------------------------------
# 🔍 البحث — اختيار مسار رقم/اسم (محرّك مزيّف)
# ---------------------------------------------------------------------------


class _FakeResultId:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _FakeResultRows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeConn:
    def __init__(self, id_row, name_rows):
        self._id_row = id_row
        self._name_rows = name_rows

    def execute(self, stmt, params):
        sql = str(stmt)
        if "WHERE id = :id" in sql:
            return _FakeResultId(self._id_row)
        if "ILIKE" in sql:
            return _FakeResultRows(self._name_rows)
        # مسار الجوال (IN (:p1, :p2)) — لا نتائج بهذا الاختبار
        return _FakeResultRows([])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeEngine:
    def __init__(self, id_row=None, name_rows=None):
        self._id_row = id_row
        self._name_rows = name_rows or []

    def connect(self):
        return _FakeConn(self._id_row, self._name_rows)


def test_search_customers_digit_query_tries_id_first(monkeypatch):
    monkeypatch.setattr(search_mod, "get_engine", lambda: _FakeEngine(id_row={"id": 7, "name": "أحمد", "status": "active"}))
    results = search_mod.search_customers("7")
    assert results == [{"id": 7, "name": "أحمد", "status": "active"}]


def test_search_customers_digit_query_no_id_match_returns_empty(monkeypatch):
    monkeypatch.setattr(search_mod, "get_engine", lambda: _FakeEngine(id_row=None, name_rows=[]))
    assert search_mod.search_customers("999999999") == []


def test_search_customers_text_query_uses_name_ilike(monkeypatch):
    rows = [{"id": 1, "name": "Ahmed Test", "status": "active"}]
    monkeypatch.setattr(search_mod, "get_engine", lambda: _FakeEngine(name_rows=rows))
    assert search_mod.search_customers("Ahmed") == rows


def test_search_customers_blank_query_returns_empty_without_db_call():
    class _Boom:
        def connect(self):
            raise AssertionError("لا يجب استدعاء get_engine لاستعلام فارغ")

    import app.telegram_admin_search as mod

    original = mod.get_engine
    mod.get_engine = lambda: _Boom()
    try:
        assert mod.search_customers("   ") == []
    finally:
        mod.get_engine = original
