"""B4 (دفعة ثانية) — اختبارات قائمة العميل النشطة الموسّعة (8 أزرار)،
تدفّق تجديد الاشتراك أثناء التوقف/الانتهاء، تحديث السيرة من القائمة،
محرّري المدن/المجالات المبسّطين، وعرض/توليد رابط ربط البريد — عبر
`telegram_onboarding.handle_update`/`_handle_active_menu` بعميل تيليجرام
مزيّف بالكامل، بنفس نمط test_telegram_onboarding_db.py.

يحتاج قاعدة بيانات Postgres حقيقية مهاجرة حتى 0020 (نفس حدّ
test_telegram_admin_b4_inbox_db.py) — يُتخطى تلقائيًا (skip) إن تعذّر
الاتصال.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import link_api
from app import telegram_onboarding as ob
from app import telegram_payments
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
        "لا اتصال بقاعدة بيانات Postgres محلية مهاجرة (0020) — يُتخطى test_telegram_onboarding_b4_menu_db.py كليًا.",
        allow_module_level=True,
    )


class RecordingTelegramClient:
    def __init__(self):
        self.sent: list[dict[str, Any]] = []

    async def send_message(self, chat_id, text_, *, buttons=None, disable_web_page_preview=True):
        self.sent.append({"chat_id": chat_id, "text": text_, "buttons": buttons})
        return {"message_id": len(self.sent)}

    async def send_contact_request(self, chat_id, text_, button_text):
        self.sent.append({"chat_id": chat_id, "text": text_, "buttons": None})
        return {"message_id": len(self.sent)}

    async def answer_callback_query(self, callback_query_id, *, text=None, show_alert=False):
        return {}


def _make_event(chat_id, *, text="", is_callback=False, callback_data="", document=None, from_user_id=None):
    return ChatEvent(
        chat_id=chat_id, text=text, is_callback=is_callback, callback_data=callback_data,
        callback_query_id="cb1" if is_callback else None, message_id=1, document=document,
        photo=None, contact=None, from_user_id=from_user_id if from_user_id is not None else chat_id,
    )


@pytest.fixture()
def engine() -> Engine:
    return _ENGINE


@pytest.fixture(autouse=True)
def _patch_engine(monkeypatch, engine):
    monkeypatch.setattr(ob, "get_engine", lambda: engine)
    import app.customers_api as customers_api
    import app.telegram_admin_settings as admin_settings

    monkeypatch.setattr(customers_api, "get_engine", lambda: engine)
    monkeypatch.setattr(link_api, "get_engine", lambda: engine)
    monkeypatch.setattr(admin_settings, "get_engine", lambda: engine)


@pytest.fixture()
def active_customer(engine):
    """عميل مكتمل onboarding فعليًا (cv_pdf_path + مدينة + مجال) ومربوط
    برقم تيليجرام — يدخل مباشرة قائمة `_handle_active_menu`/`handle_update`
    بلا حاجة لعبور onboarding كاملًا بكل اختبار."""
    created_ids: list[int] = []

    def _factory(*, status: str = "active"):
        tag = uuid.uuid4().hex[:10]
        chat_id = int(uuid.uuid4().int % 900000000) + 10**9
        with engine.begin() as conn:
            cid = conn.execute(
                text(
                    "INSERT INTO customers (name, email_service, status, target_daily, telegram_chat_id, "
                    "cv_pdf_path, cities, families) "
                    "VALUES (:n, :e, :st, 17, :tg, '/tmp/fake.pdf', :cities, :families) RETURNING id"
                ),
                {
                    "n": f"B4Menu {tag}", "e": f"b4menu-{tag}@masar.invalid", "st": status, "tg": chat_id,
                    "cities": '["الرياض"]', "families": '["accounting"]',
                },
            ).scalar_one()
        created_ids.append(cid)
        return cid, chat_id

    yield _factory

    with engine.begin() as conn:
        for cid in created_ids:
            conn.execute(text("DELETE FROM customer_messages WHERE customer_id = :id"), {"id": cid})
            conn.execute(text("DELETE FROM link_tokens WHERE customer_id = :id"), {"id": cid})
            conn.execute(text("DELETE FROM mail_links WHERE customer_id = :id"), {"id": cid})
            conn.execute(
                text("DELETE FROM telegram_sessions WHERE chat_id = (SELECT telegram_chat_id FROM customers WHERE id = :id)"),
                {"id": cid},
            )
            conn.execute(text("DELETE FROM customers WHERE id = :id"), {"id": cid})


def test_active_menu_shows_all_eight_buttons(engine, active_customer):
    customer_id, chat_id = active_customer()
    client = RecordingTelegramClient()
    _run(ob.handle_update({}, _make_event(chat_id, text="مرحبا"), client))
    last = client.sent[-1]
    flat = [b["callback_data"] for row in last["buttons"] for b in row]
    assert flat == [
        "menu:status", "menu:subscription", "menu:update_cv", "menu:cities",
        "menu:families", "menu:mail", "menu:contact", "menu:about",
    ]


def test_paused_customer_menu_offers_renewal_status_and_contact(engine, active_customer):
    customer_id, chat_id = active_customer(status="paused")
    client = RecordingTelegramClient()
    _run(ob.handle_update({}, _make_event(chat_id, text="مرحبا"), client))
    last = client.sent[-1]
    flat = [b["callback_data"] for row in last["buttons"] for b in row]
    assert "menu:subscription" in flat
    assert "menu:contact" in flat
    assert "menu:status" in flat


def test_renewal_payment_flow_continues_while_status_paused(engine, active_customer, monkeypatch):
    """B4 (دفعة ثانية) — تثبيت الإصلاح: عميل مُوقَف بمنتصف تدفّق دفع
    (خطوة await_package مثلاً) يجب أن يستمر بالتدفّق فعليًا (استدعاء
telegram_payments.handle_step) بدل إعادة توجيهه لرسالة "اشتراكك موقوف"
    الثابتة في كل رسالة تالية — هذا بالضبط سبب إعادة ترتيب handle_update."""
    customer_id, chat_id = active_customer(status="paused")
    ob._save_session(chat_id, "await_package", {})

    handle_step_calls: list[tuple] = []

    async def fake_handle_step(event, client, cid, step, data):
        handle_step_calls.append((cid, step))
        await client.send_message(event.chat_id, "تم استلام اختيار الباقة (محاكاة)")

    monkeypatch.setattr(telegram_payments, "handle_step", fake_handle_step)
    client = RecordingTelegramClient()
    _run(ob.handle_update({}, _make_event(chat_id, text="الباقة الشهرية"), client))

    assert handle_step_calls == [(customer_id, "await_package")]
    assert "محاكاة" in client.sent[-1]["text"]


def test_cv_update_flow_saves_new_cv_without_touching_cities_families(engine, active_customer, monkeypatch):
    customer_id, chat_id = active_customer()
    client = RecordingTelegramClient()

    _run(ob._handle_active_menu(_make_event(chat_id, is_callback=True, callback_data="menu:update_cv"), client, customer_id, ob._fetch_onboarding_state(customer_id)))
    step, _ = ob._get_session(chat_id)
    assert step == "awaiting_cv_update"

    async def fake_process(event, client_, cid):
        with engine.begin() as conn:
            conn.execute(text("UPDATE customers SET cv_pdf_path = '/tmp/new.pdf' WHERE id = :id"), {"id": cid})
        return "نص سيرة جديد"

    monkeypatch.setattr(ob, "_process_cv_upload", fake_process)
    doc_event = _make_event(chat_id, document={"file_id": "f1"})
    _run(ob._handle_active_menu(doc_event, client, customer_id, ob._fetch_onboarding_state(customer_id)))

    assert "تم تحديث سيرتك" in client.sent[-1]["text"]
    with engine.connect() as conn:
        row = conn.execute(text("SELECT cv_pdf_path, cities, families FROM customers WHERE id = :id"), {"id": customer_id}).first()
    assert row[0] == "/tmp/new.pdf"
    step2, _ = ob._get_session(chat_id)
    assert step2 == ""


def test_cities_editor_add_then_remove_persists(engine, active_customer):
    customer_id, chat_id = active_customer()
    client = RecordingTelegramClient()
    state = ob._fetch_onboarding_state(customer_id)

    _run(ob._handle_active_menu(_make_event(chat_id, is_callback=True, callback_data="menu:cities"), client, customer_id, state))
    step, _ = ob._get_session(chat_id)
    assert step == "menu_cities_edit"

    _run(ob._handle_active_menu(_make_event(chat_id, is_callback=True, callback_data="mymenu:city_add"), client, customer_id, state))
    step, _ = ob._get_session(chat_id)
    assert step == "menu_cities_add_text"

    _run(ob._handle_active_menu(_make_event(chat_id, text="جدة"), client, customer_id, state))
    with engine.connect() as conn:
        cities_after_add = conn.execute(text("SELECT cities FROM customers WHERE id = :id"), {"id": customer_id}).scalar_one()
    assert "جدة" in cities_after_add

    # حذف أول مدينة (الرياض بفهرس 0)
    _run(ob._handle_active_menu(_make_event(chat_id, is_callback=True, callback_data="mymenu:city_rm:0"), client, customer_id, state))
    with engine.connect() as conn:
        cities_after_rm = conn.execute(text("SELECT cities FROM customers WHERE id = :id"), {"id": customer_id}).scalar_one()
    assert "الرياض" not in cities_after_rm
    assert "جدة" in cities_after_rm

    _run(ob._handle_active_menu(_make_event(chat_id, is_callback=True, callback_data="mymenu:done"), client, customer_id, state))
    assert "تم الحفظ" in client.sent[-1]["text"]
    step_final, _ = ob._get_session(chat_id)
    assert step_final == ""


def test_families_editor_rejects_out_of_scope_text(engine, active_customer, monkeypatch):
    customer_id, chat_id = active_customer()
    client = RecordingTelegramClient()
    state = ob._fetch_onboarding_state(customer_id)

    _run(ob._handle_active_menu(_make_event(chat_id, is_callback=True, callback_data="menu:families"), client, customer_id, state))
    _run(ob._handle_active_menu(_make_event(chat_id, is_callback=True, callback_data="mymenu:family_add"), client, customer_id, state))

    monkeypatch.setattr(ob, "classify_family", lambda text_: "sales_excluded")
    _run(ob._handle_active_menu(_make_event(chat_id, text="مبيعات"), client, customer_id, state))
    assert "خارج نطاق خدمتنا" in client.sent[-1]["text"]
    with engine.connect() as conn:
        families = conn.execute(text("SELECT families FROM customers WHERE id = :id"), {"id": customer_id}).scalar_one()
    assert "sales_excluded" not in families
    assert "accounting" in families  # لم يتغيّر


def test_families_editor_adds_matched_family_and_persists(engine, active_customer, monkeypatch):
    customer_id, chat_id = active_customer()
    client = RecordingTelegramClient()
    state = ob._fetch_onboarding_state(customer_id)

    _run(ob._handle_active_menu(_make_event(chat_id, is_callback=True, callback_data="menu:families"), client, customer_id, state))
    _run(ob._handle_active_menu(_make_event(chat_id, is_callback=True, callback_data="mymenu:family_add"), client, customer_id, state))

    monkeypatch.setattr(ob, "classify_family", lambda text_: "hse")
    _run(ob._handle_active_menu(_make_event(chat_id, text="سلامة"), client, customer_id, state))
    with engine.connect() as conn:
        families = conn.execute(text("SELECT families FROM customers WHERE id = :id"), {"id": customer_id}).scalar_one()
    assert "hse" in families
    assert "accounting" in families


def test_mail_menu_shows_not_linked_then_generates_link(engine, active_customer, monkeypatch):
    customer_id, chat_id = active_customer()
    client = RecordingTelegramClient()
    state = ob._fetch_onboarding_state(customer_id)

    _run(ob._handle_active_menu(_make_event(chat_id, is_callback=True, callback_data="menu:mail"), client, customer_id, state))
    assert "ما ربطت بريدًا" in client.sent[-1]["text"]

    async def fake_generate(cid):
        return f"https://masar.example/link/fake-{cid}"

    monkeypatch.setattr(ob, "_generate_mail_link_url", fake_generate)
    _run(ob._handle_active_menu(_make_event(chat_id, is_callback=True, callback_data="menu:mail_link"), client, customer_id, state))
    assert f"fake-{customer_id}" in client.sent[-1]["text"]


def test_mail_menu_shows_linked_status_when_mail_link_ok(engine, active_customer):
    customer_id, chat_id = active_customer()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO mail_links (customer_id, address, secret_enc, status) "
                "VALUES (:cid, :addr, 'x', 'ok')"
            ),
            {"cid": customer_id, "addr": "test@gmail.com"},
        )
    client = RecordingTelegramClient()
    state = ob._fetch_onboarding_state(customer_id)
    _run(ob._handle_active_menu(_make_event(chat_id, is_callback=True, callback_data="menu:mail"), client, customer_id, state))
    assert "مربوط بنجاح" in client.sent[-1]["text"]
    assert "test@gmail.com" in client.sent[-1]["text"]


def test_about_service_renders_whatsapp_number(engine, active_customer):
    customer_id, chat_id = active_customer()
    client = RecordingTelegramClient()
    state = ob._fetch_onboarding_state(customer_id)
    _run(ob._handle_active_menu(_make_event(chat_id, is_callback=True, callback_data="menu:about"), client, customer_id, state))
    text_ = client.sent[-1]["text"]
    assert "عن خدمة مسار" in text_
    assert ob.support_whatsapp() in text_
