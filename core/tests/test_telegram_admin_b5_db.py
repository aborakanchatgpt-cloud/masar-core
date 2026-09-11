"""اختبارات B9/B5 ببوت الأدمن — بحث موسّع (رقم/جوال/اسم) + بطاقة عميل
وأزرار إجراء، ✉️ رسالة لعميل، وإعدادات (بيانات تحويل/باقات) — عبر
`admin.handle_update` بالكامل (نفس نمط `test_telegram_admin_db.py`، بملف
منفصل فقط لحجم `repo_write` — راجع docstring `app.telegram_admin_commands`).

**B3-متابعة:** تغطية 🏦 بيانات التحويل (قائمة حسابات متعددة) انتقلت
لملف منفصل `test_telegram_admin_b3_followup_db.py` — راجع التعليق أسفل قسم
⚙️ الإعدادات أدناه.

يحتاج قاعدة بيانات Postgres حقيقية مهاجَرة حتى 0017 — يُتخطّى تلقائيًا
(skip) إن تعذّر الاتصال، نفس نمط test_telegram_admin_db.py.
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
from app import telegram_admin_settings as settings_mod
from app.telegram_client import ChatEvent

DEFAULT_TEST_DB_URL = "postgresql+psycopg://masar_test:masar_test@localhost:5432/masar_test"


def _run(coro):
    return asyncio.run(coro)


def _make_engine() -> Engine | None:
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DB_URL)
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM app_settings LIMIT 1"))
        return engine
    except Exception:  # noqa: BLE001
        return None


_ENGINE = _make_engine()

if _ENGINE is None:
    pytest.skip(
        "لا اتصال بقاعدة بيانات Postgres محلية مهاجَرة (0017) — يُتخطّى test_telegram_admin_b5_db.py كليًا.",
        allow_module_level=True,
    )

OWNER_CHAT_ID = 555000222


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
        photo=None, contact=None, from_user_id=chat_id, from_username=None,
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
    import app.telegram_admin_commands as commands_mod

    monkeypatch.setattr(customers_api, "get_engine", lambda: engine)
    monkeypatch.setattr(overview_api, "get_engine", lambda: engine)
    monkeypatch.setattr(guarantee_api, "get_engine", lambda: engine)
    monkeypatch.setattr(reports_api, "get_engine", lambda: engine)
    monkeypatch.setattr(commands_mod, "get_engine", lambda: engine)
    monkeypatch.setattr(search_mod, "get_engine", lambda: engine)
    monkeypatch.setattr(settings_mod, "get_engine", lambda: engine)


@pytest.fixture()
def created_customer(engine):
    tag = uuid.uuid4().hex[:10]

    def _make(*, telegram_chat_id: int | None = None, name: str | None = None):
        with engine.begin() as conn:
            cid = conn.execute(
                text(
                    "INSERT INTO customers (name, email_service, status, target_daily, telegram_chat_id) "
                    "VALUES (:n, :e, 'active', 17, :tg) RETURNING id"
                ),
                {"n": name or f"B5 Test {tag}", "e": f"b5-{tag}@masar.invalid", "tg": telegram_chat_id},
            ).scalar_one()
        return cid

    created_ids: list[int] = []

    def _factory(**kwargs):
        cid = _make(**kwargs)
        created_ids.append(cid)
        return cid

    yield _factory

    with engine.begin() as conn:
        for cid in created_ids:
            conn.execute(text("DELETE FROM customer_messages WHERE customer_id = :id"), {"id": cid})
            conn.execute(text("DELETE FROM customer_status_audit WHERE customer_id = :id"), {"id": cid})
            conn.execute(text("DELETE FROM subscriptions WHERE customer_id = :id"), {"id": cid})
            conn.execute(text("DELETE FROM customers WHERE id = :id"), {"id": cid})


@pytest.fixture()
def temp_product(engine):
    """باقة مؤقتة للاختبار — لا تلمس SUB30/CR100/... الحقيقية (test_catalog.py
    يفترض sub30.price_sar is None، فتعديلها هنا كان سيُسرّب حالة بين الملفات)."""
    code = f"B5TEST{uuid.uuid4().hex[:6].upper()}"
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO products (code, name_ar, type, price_sar, days, applications_included, active) "
                "VALUES (:c, 'باقة اختبار B5', 'subscription', 100.00, 30, 100, true)"
            ),
            {"c": code},
        )
    yield code
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM products WHERE code = :c"), {"c": code})


# ---------------------------------------------------------------------------
# 🔍 بحث عن عميل — رقم/جوال/اسم
# ---------------------------------------------------------------------------


def test_search_by_partial_name_single_match_shows_card(engine, created_customer):
    tag = uuid.uuid4().hex[:10]
    cid = created_customer(name=f"UniqueSearchName{tag}")
    client = RecordingTelegramClient()
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="admin:lookup"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text=f"UniqueSearchName{tag}"), client))
    assert any(f"عميل #{cid}" in m["text"] for m in client.sent)
    assert any("⏸️ إيقاف" in str(m["buttons"]) for m in client.sent if m["buttons"])


def test_search_multiple_matches_shows_pick_buttons(engine, created_customer):
    tag = uuid.uuid4().hex[:10]
    created_customer(name=f"DupSearch{tag} One")
    created_customer(name=f"DupSearch{tag} Two")
    client = RecordingTelegramClient()
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="admin:lookup"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text=f"DupSearch{tag}"), client))
    assert any("وجدت 2 نتيجة" in m["text"] for m in client.sent)


def test_search_by_id_not_found_reports_not_found(engine):
    client = RecordingTelegramClient()
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="admin:lookup"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="999999999"), client))
    assert any("لم أجد عميلًا" in m["text"] for m in client.sent)


def test_card_toggle_status_active_to_paused(engine, created_customer):
    cid = created_customer()
    client = RecordingTelegramClient()
    _run(
        admin.handle_update(
            {}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data=f"admin:card_toggle:{cid}:paused"), client
        )
    )
    with engine.connect() as conn:
        row = conn.execute(text("SELECT status FROM customers WHERE id = :id"), {"id": cid}).first()
    assert row[0] == "paused"
    assert any("مُوقَف" in m["text"] for m in client.sent)


# ---------------------------------------------------------------------------
# ✉️ رسالة لعميل
# ---------------------------------------------------------------------------


def test_message_customer_without_telegram_link_warns(engine, created_customer):
    cid = created_customer(telegram_chat_id=None)
    client = RecordingTelegramClient()
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data=f"admin:msgto:{cid}"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="أهلًا، كيف حالك؟"), client))
    assert any("غير مربوط بتيليجرام" in m["text"] for m in client.sent)


def test_message_customer_success_logs_customer_messages(monkeypatch, engine, created_customer):
    customer_chat_id = 909090
    cid = created_customer(telegram_chat_id=customer_chat_id)
    sent_to_customer: list[tuple] = []

    def fake_send(chat_id, text_, **kwargs):
        sent_to_customer.append((chat_id, text_))

    monkeypatch.setattr(search_mod, "send_customer_message", fake_send)

    client = RecordingTelegramClient()
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data=f"admin:msgto:{cid}"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="رسالة اختبار B5"), client))

    assert sent_to_customer == [(customer_chat_id, "رسالة اختبار B5")]
    assert any("أُرسلت الرسالة" in m["text"] for m in client.sent)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT direction, text FROM customer_messages WHERE customer_id = :id"), {"id": cid}
        ).first()
    assert row == ("out", "رسالة اختبار B5")


# ---------------------------------------------------------------------------
# ⚙️ الإعدادات — بيانات التحويل والباقات (للمالك فقط)
# ---------------------------------------------------------------------------


def test_settings_hidden_from_non_owner_delegate(engine):
    """B9/B2+B5: مفوّض لا يرى ⚙️ الإعدادات — حتى لو خمّن callback_data
    مباشرة، الفحص الإضافي (`is_owner_chat`) يمنعه بصمت (لا ردّ)."""
    tag = uuid.uuid4().hex[:10]
    with engine.begin() as conn:
        delegate_chat_id = 777000 + hash(tag) % 1000
        conn.execute(text("DELETE FROM admin_delegates WHERE telegram_chat_id = :c"), {"c": delegate_chat_id})
        conn.execute(
            text(
                "INSERT INTO admin_delegates (name, telegram_chat_id, active) VALUES (:n, :c, true)"
            ),
            {"n": f"Deleg {tag}", "c": delegate_chat_id},
        )
    client = RecordingTelegramClient()
    _run(
        admin.handle_update(
            {}, _make_event(delegate_chat_id, is_callback=True, callback_data="admin:settings"), client
        )
    )
    assert client.sent == []
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM admin_delegates WHERE telegram_chat_id = :c"), {"c": delegate_chat_id})


# B3-متابعة: بيانات التحويل الأحادية (app_settings: bank_name/account_holder/
# iban + callback_data "settings:bank_edit:{field}" بلا مُعرّف حساب) استُبدلت
# بقائمة حسابات بنكية (جدول bank_accounts، callback_data "settings:bank_edit:
# {id}:{field}") — راجع docstring app.telegram_admin_settings. تغطية إضافة/
# تعديل/إيقاف حساب بنكي الآن بملف منفصل test_telegram_admin_b3_followup_db.py
# (نفس نمط فصل ملفات الدفعات بالمشروع)، لا هنا.


def test_settings_whatsapp_edit_roundtrip(engine):
    original = settings_mod.get_setting("support_whatsapp")
    client = RecordingTelegramClient()
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="settings:whatsapp"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="+966500000000"), client))
    assert settings_mod.get_setting("support_whatsapp") == "+966500000000"
    assert settings_mod.support_whatsapp() == "+966500000000"
    if original:
        settings_mod.set_setting("support_whatsapp", original)


def test_settings_package_price_edit_and_toggle(engine, temp_product):
    client = RecordingTelegramClient()
    _run(
        admin.handle_update(
            {}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data=f"settings:pkg_edit:{temp_product}:price_sar"), client
        )
    )
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="199.50"), client))
    with engine.connect() as conn:
        price = conn.execute(text("SELECT price_sar FROM products WHERE code = :c"), {"c": temp_product}).scalar()
    assert float(price) == 199.50

    client2 = RecordingTelegramClient()
    _run(
        admin.handle_update(
            {}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data=f"settings:pkg_toggle:{temp_product}"), client2
        )
    )
    with engine.connect() as conn:
        active = conn.execute(text("SELECT active FROM products WHERE code = :c"), {"c": temp_product}).scalar()
    assert active is False


def test_settings_package_edit_non_numeric_reprompts_without_losing_context(engine, temp_product):
    """نفس نمط B9/A6 (extend_days): إدخال غير رقمي يُعيد الطلب بدل قيمة
    مخمّنة — الجلسة تبقى بانتظار نفس الحقل/الباقة."""
    client = RecordingTelegramClient()
    _run(
        admin.handle_update(
            {}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data=f"settings:pkg_edit:{temp_product}:days"), client
        )
    )
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="ثلاثين"), client))
    assert any("اكتب رقمًا صحيحًا" in m["text"] for m in client.sent)
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="45"), client))
    with engine.connect() as conn:
        days = conn.execute(text("SELECT days FROM products WHERE code = :c"), {"c": temp_product}).scalar()
    assert days == 45
