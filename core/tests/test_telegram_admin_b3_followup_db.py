"""اختبارات B3-متابعة (دفعة سريعة بعد B3 الأساسية) — دعم أكثر من بنك واحد
لبيانات التحويل (جدول bank_accounts، ترحيلة 0018) وفئات استهداف ✉️ رسالة
لعميل (عميل محدد/كل العملاء/النشطون/منتهو الاشتراك)، عبر `admin.handle_update`
بالكامل — نفس نمط `test_telegram_admin_b5_db.py` (ملف منفصل لحجم repo_write
وتمييز الدفعة، لا لسبب معماري).

يحتاج قاعدة بيانات Postgres حقيقية مهاجَرة حتى 0019 (حقول account_number/
name_language + iban nullable) — يُتخطّى تلقائيًا (skip) إن تعذّر الاتصال، نفس نمط
بقية اختبارات B9/B3.
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
            conn.execute(text("SELECT 1 FROM bank_accounts LIMIT 1"))
        return engine
    except Exception:  # noqa: BLE001
        return None


_ENGINE = _make_engine()

if _ENGINE is None:
    pytest.skip(
        "لا اتصال بقاعدة بيانات Postgres محلية مهاجَرة (0018) — يُتخطّى test_telegram_admin_b3_followup_db.py كليًا.",
        allow_module_level=True,
    )

OWNER_CHAT_ID = 555000333


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
    monkeypatch.setattr(search_mod, "get_engine", lambda: engine)
    monkeypatch.setattr(settings_mod, "get_engine", lambda: engine)
    monkeypatch.setenv("MASAR_OWNER_CHAT_ID", str(OWNER_CHAT_ID))


@pytest.fixture()
def created_customer(engine):
    def _make(*, telegram_chat_id: int | None = None, status: str = "active", name: str | None = None):
        tag = uuid.uuid4().hex[:10]
        with engine.begin() as conn:
            cid = conn.execute(
                text(
                    "INSERT INTO customers (name, email_service, status, target_daily, telegram_chat_id) "
                    "VALUES (:n, :e, :st, 17, :tg) RETURNING id"
                ),
                {"n": name or f"B3F Test {tag}", "e": f"b3f-{tag}@masar.invalid", "st": status, "tg": telegram_chat_id},
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
            conn.execute(text("DELETE FROM customers WHERE id = :id"), {"id": cid})


@pytest.fixture()
def created_bank_account(engine):
    created_ids: list[int] = []

    def _factory(**kwargs):
        cid = settings_mod.create_bank_account(
            kwargs.get("bank_name", "بنك تجريبي"),
            kwargs.get("account_holder", "مسار"),
            account_number=kwargs.get("account_number"),
            iban=kwargs.get("iban", "SA9999999999999999"),
            name_language=kwargs.get("name_language"),
        )
        created_ids.append(cid)
        return cid

    yield _factory

    with engine.begin() as conn:
        for cid in created_ids:
            conn.execute(text("DELETE FROM bank_accounts WHERE id = :id"), {"id": cid})


# =========================================================================
# 🏦 بيانات التحويل — قائمة حسابات
# =========================================================================


def test_bank_add_flow_via_admin_handle_update(engine):
    """B3-متابعة٢: التدفّق الكامل الآن يمرّ بخطوة أزرار للغة بعد اسم البنك،
    ثم رقم الحساب (يُكتب هنا، لا يُتخطّى) فالآيبان."""
    client = RecordingTelegramClient()

    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="settings:bank_add"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="بنك الرياض"), client))
    assert any("settings:bank_lang:ar" in str(m.get("buttons")) for m in client.sent)
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="settings:bank_lang:ar"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="مؤسسة مسار"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="1122334455"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="SA1234567890123456789"), client))

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT bank_name, account_holder, account_number, iban, name_language, active "
                "FROM bank_accounts WHERE bank_name = 'بنك الرياض'"
            )
        ).mappings().first()
    assert row is not None
    assert row["account_holder"] == "مؤسسة مسار"
    assert row["account_number"] == "1122334455"
    assert row["iban"] == "SA1234567890123456789"
    assert row["name_language"] == "ar"
    assert row["active"] is True
    assert any("✅ تمت إضافة الحساب البنكي" in m["text"] for m in client.sent)

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM telegram_sessions WHERE chat_id = :cid"), {"cid": OWNER_CHAT_ID})
        conn.execute(text("DELETE FROM bank_accounts WHERE bank_name = 'بنك الرياض'"))


def test_bank_add_flow_skip_account_number_via_button(engine):
    """تخطي رقم الحساب بالزر — يُحفَظ الحساب بآيبان فقط (account_number NULL)."""
    client = RecordingTelegramClient()

    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="settings:bank_add"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="بنك سامبا"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="settings:bank_lang:en"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="Masar Est"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="settings:bank_skip:number"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="SA9876543210987654321"), client))

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT account_number, iban, name_language FROM bank_accounts WHERE bank_name = 'بنك سامبا'"
            )
        ).mappings().first()
    assert row is not None
    assert row["account_number"] is None
    assert row["iban"] == "SA9876543210987654321"
    assert row["name_language"] == "en"

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM telegram_sessions WHERE chat_id = :cid"), {"cid": OWNER_CHAT_ID})
        conn.execute(text("DELETE FROM bank_accounts WHERE bank_name = 'بنك سامبا'"))


def test_bank_add_flow_skip_iban_after_account_number(engine):
    """تخطي الآيبان بالزر — مسموح فقط لأن رقم الحساب مُعبَّأ مسبقًا."""
    client = RecordingTelegramClient()

    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="settings:bank_add"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="بنك الإنماء"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="settings:bank_lang:ar"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="مسار"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="55667788"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="settings:bank_skip:iban"), client))

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT account_number, iban FROM bank_accounts WHERE bank_name = 'بنك الإنماء'")
        ).mappings().first()
    assert row is not None
    assert row["account_number"] == "55667788"
    assert row["iban"] is None
    assert any("✅ تمت إضافة الحساب البنكي" in m["text"] for m in client.sent)

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM telegram_sessions WHERE chat_id = :cid"), {"cid": OWNER_CHAT_ID})
        conn.execute(text("DELETE FROM bank_accounts WHERE bank_name = 'بنك الإنماء'"))


def test_bank_add_flow_rejects_skipping_both_number_and_iban(engine):
    """تخطي رقم الحساب ثم محاولة تخطي الآيبان أيضًا — تُرفَض، يُطلَب الآيبان
    فعليًا حتى يوجد حقل واحد على الأقل مُعبَّأ (قيد المنتج، وقيد CHECK دفاعًا)."""
    client = RecordingTelegramClient()

    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="settings:bank_add"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="بنك الجزيرة"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="settings:bank_lang:ar"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="مسار"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="settings:bank_skip:number"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="settings:bank_skip:iban"), client))

    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM bank_accounts WHERE bank_name = 'بنك الجزيرة'")
        ).first()
    assert exists is None  # لم يُحفَظ شيء بعد — الرفض لم يُنشئ صفًا ناقصًا
    assert any("يجب إدخال رقم الحساب أو الآيبان على الأقل" in m["text"] for m in client.sent)

    # يكمل بكتابة آيبان فعليًا فينجح
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="SA1111122222333334444"), client))
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT account_number, iban FROM bank_accounts WHERE bank_name = 'بنك الجزيرة'")
        ).mappings().first()
    assert row is not None
    assert row["account_number"] is None
    assert row["iban"] == "SA1111122222333334444"

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM telegram_sessions WHERE chat_id = :cid"), {"cid": OWNER_CHAT_ID})
        conn.execute(text("DELETE FROM bank_accounts WHERE bank_name = 'بنك الجزيرة'"))


def test_bank_edit_account_number_and_toggle_language(engine, created_bank_account):
    account_id = created_bank_account(
        bank_name="بنك أصلي٢", account_holder="مسار", account_number="000111", iban="SA0000000000000001"
    )
    client = RecordingTelegramClient()

    _run(
        admin.handle_update(
            {},
            _make_event(OWNER_CHAT_ID, is_callback=True, callback_data=f"settings:bank_edit:{account_id}:account_number"),
            client,
        )
    )
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="999888"), client))

    with engine.connect() as conn:
        number = conn.execute(
            text("SELECT account_number FROM bank_accounts WHERE id = :id"), {"id": account_id}
        ).scalar()
    assert number == "999888"

    # لغة الاسم NULL افتراضيًا (لم تُحدّد بـcreated_bank_account) — أول تبديل يجعلها 'ar'
    _run(
        admin.handle_update(
            {}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data=f"settings:bank_lang_toggle:{account_id}"), client
        )
    )
    with engine.connect() as conn:
        lang = conn.execute(
            text("SELECT name_language FROM bank_accounts WHERE id = :id"), {"id": account_id}
        ).scalar()
    assert lang == "ar"

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM telegram_sessions WHERE chat_id = :cid"), {"cid": OWNER_CHAT_ID})


def test_bank_edit_and_toggle_via_admin_handle_update(engine, created_bank_account):
    account_id = created_bank_account(bank_name="بنك أصلي", account_holder="مسار", iban="SA0000000000000000")
    client = RecordingTelegramClient()

    # تعديل اسم البنك
    _run(
        admin.handle_update(
            {}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data=f"settings:bank_edit:{account_id}:bank_name"), client
        )
    )
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="بنك مُعدَّل"), client))

    with engine.connect() as conn:
        name = conn.execute(text("SELECT bank_name FROM bank_accounts WHERE id = :id"), {"id": account_id}).scalar()
    assert name == "بنك مُعدَّل"

    # إيقاف الحساب — لا يظهر بـactive_bank_accounts() بعدها
    assert any(a["id"] == account_id for a in settings_mod.active_bank_accounts())
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data=f"settings:bank_toggle:{account_id}"), client))
    assert not any(a["id"] == account_id for a in settings_mod.active_bank_accounts())

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM telegram_sessions WHERE chat_id = :cid"), {"cid": OWNER_CHAT_ID})


def test_bank_list_shows_only_owner(engine, created_bank_account):
    created_bank_account()
    client = RecordingTelegramClient()
    delegate_chat_id = 777000111  # ليس مالكًا ولا مفوّضًا مربوطًا — is_admin_chat سيعيد False أصلًا
    _run(admin.handle_update({}, _make_event(delegate_chat_id, is_callback=True, callback_data="settings:bank"), client))
    assert client.sent == []  # صمت تام لغير المصرّح، نفس فلسفة fail-closed


# =========================================================================
# ✉️ رسالة لعميل — فئات استهداف
# =========================================================================


def test_msg_menu_shows_four_category_buttons():
    client = RecordingTelegramClient()
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="admin:msg"), client))
    buttons = client.sent[-1]["buttons"]
    callback_datas = {b["callback_data"] for row in buttons for b in row}
    assert {"admin:msg_cat:specific", "admin:msg_cat:all", "admin:msg_cat:active", "admin:msg_cat:expired"} <= callback_datas


def test_msg_cat_specific_still_asks_for_search(engine):
    client = RecordingTelegramClient()
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="admin:msg_cat:specific"), client))
    assert any("ابحث عن العميل" in m["text"] for m in client.sent)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM telegram_sessions WHERE chat_id = :cid"), {"cid": OWNER_CHAT_ID})


def test_msg_cat_with_zero_matches_tells_admin_and_does_not_prompt_text(engine, created_customer):
    created_customer(telegram_chat_id=None, status="expired")  # بلا ربط تيليجرام — لا يُحتسَب
    client = RecordingTelegramClient()
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="admin:msg_cat:expired"), client))
    assert any("لا يوجد عملاء يطابقون هذه الفئة" in m["text"] for m in client.sent)


def test_msg_bulk_active_target_previews_then_sends_and_logs(engine, created_customer, monkeypatch):
    active_with_chat = [created_customer(telegram_chat_id=900001 + i, status="active") for i in range(2)]
    created_customer(telegram_chat_id=None, status="active")  # بلا ربط — يُستثنى من العدد
    created_customer(telegram_chat_id=900050, status="paused")  # حالة مختلفة — يُستثنى

    sent_calls: list[tuple[int, str]] = []

    def fake_send(chat_id, text_):
        sent_calls.append((chat_id, text_))

    monkeypatch.setattr(search_mod, "send_customer_message", fake_send)

    client = RecordingTelegramClient()
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="admin:msg_cat:active"), client))
    assert any("(2 عميل)" in m["text"] for m in client.sent)

    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="عرض خاص لكم هذا الأسبوع 🌿"), client))
    preview = client.sent[-1]
    assert "معاينة الرسالة" in preview["text"] and "عرض خاص" in preview["text"]
    assert any(b["callback_data"] == "msgbulk:send" for row in preview["buttons"] for b in row)

    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="msgbulk:send"), client))

    assert len(sent_calls) == 2
    assert {cid for cid, _ in sent_calls} == {900001, 900002}
    with engine.connect() as conn:
        logged = conn.execute(
            text(
                "SELECT count(*) FROM customer_messages WHERE customer_id = ANY(:ids) AND direction = 'out' "
                "AND text = 'عرض خاص لكم هذا الأسبوع 🌿'"
            ),
            {"ids": active_with_chat},
        ).scalar()
    assert logged == 2
    assert any("نجح 2" in m["text"] for m in client.sent)

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM telegram_sessions WHERE chat_id = :cid"), {"cid": OWNER_CHAT_ID})
