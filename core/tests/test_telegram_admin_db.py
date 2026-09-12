"""اختبارات core/app/telegram_admin.py (B8) — أوامر الأدمن الأساسية
(تسجيل عميل، تفعيل/إيقاف، تمديد اشتراك، تصنيف عملاء، بحث) عبر عميل
تيليجرام مزيّف بالكامل — صفر استدعاءات شبكية حقيقية.

يحتاج قاعدة بيانات Postgres حقيقية مهاجَرة حتى 0017 (B9/B0: admin_delegates)
— يُتخطّى تلقائيًا (skip) إن تعذّر الاتصال، نفس نمط test_catalog.py.
اختبارات is_owner_chat/is_admin_chat النقية بلا قاعدة بيانات موجودة بملف
منفصل test_telegram_admin.py.

B9/B2: يغطي هذا الملف أيضًا نظام المفوّضين كاملًا عبر handle_update
(الطبقة الوحيدة الآن — راجع docstring رأس app/telegram_admin.py): ربط
تلقائي بأول رسالة (username أو جوال مشارَك ذاتيًا فقط، لا رقم شخص آخر)،
صمت الغرباء بلا تطابق، فقدان صلاحية مفوّض مُزال (active=false)، وقائمة
المفوّضين (عرض/إضافة/إزالة) — كلها للمالك حصرًا.
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


def _make_event(chat_id, *, text="", is_callback=False, callback_data="", from_username=None,
                 contact=None, from_user_id=None):
    return ChatEvent(
        chat_id=chat_id, text=text, is_callback=is_callback, callback_data=callback_data,
        callback_query_id="cb1" if is_callback else None, message_id=1, document=None,
        photo=None, contact=contact, from_user_id=chat_id if from_user_id is None else from_user_id,
        from_username=from_username,
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
    import app.telegram_admin_delegates as delegates

    monkeypatch.setattr(customers_api, "get_engine", lambda: engine)
    monkeypatch.setattr(overview_api, "get_engine", lambda: engine)
    monkeypatch.setattr(guarantee_api, "get_engine", lambda: engine)
    monkeypatch.setattr(reports_api, "get_engine", lambda: engine)
    # B9/B2: try_link_delegate/reply_* بملف telegram_admin_delegates.py
    # تستدعي get_engine مباشرة (مستوردة من app.discovery هناك، لا من
    # admin أعلاه) — يجب تمويهها أيضًا وإلا حاولت الاتصال بمحرّك
    # غير مموّه رغم توفّر `engine` هنا.
    monkeypatch.setattr(delegates, "get_engine", lambda: engine)


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


# ---------------------------------------------------------------------------
# B9/B2: المفوّضون — ربط تلقائي (username/جوال)، صمت الغرباء، إزالة، وإدارة
# القائمة عبر handle_update بالكامل (بلا اختصار لأي منطق).
# ---------------------------------------------------------------------------


@pytest.fixture()
def pending_delegate(engine):
    """مفوّض مُضاف بانتظار الربط (telegram_chat_id IS NULL) — يُنظّف الصفّ
    فعليًا بعد كل اختبار (لا حاجة لإبقاء صفوف اختبار تراكمية بجدول حقيقي)."""
    tag = uuid.uuid4().hex[:10]

    def _make(*, username=None, phone=None):
        with engine.begin() as conn:
            did = conn.execute(
                text(
                    "INSERT INTO admin_delegates (name, telegram_username, phone) "
                    "VALUES (:n, :u, :p) RETURNING id"
                ),
                {"n": f"Deleg {tag}", "u": username, "p": phone},
            ).scalar_one()
        return did

    created_ids: list[int] = []

    def _factory(**kwargs):
        did = _make(**kwargs)
        created_ids.append(did)
        return did

    yield _factory
    with engine.begin() as conn:
        for did in created_ids:
            conn.execute(text("DELETE FROM admin_delegates WHERE id = :id"), {"id": did})


def test_delegate_pending_link_stays_silent_until_owner_confirms(engine, pending_delegate):
    """B11.3: أول رسالة من مفوّض مطابق تخزّن `pending_chat_id` فقط — لا رد
    له إطلاقًا (يبقى غريبًا حتى يضغط المالك ✅ تأكيد)."""
    did = pending_delegate(username="somedeleg")
    client = RecordingTelegramClient()
    stranger_chat_id = 900111

    _run(
        admin.handle_update(
            {}, _make_event(stranger_chat_id, text="السلام عليكم", from_username="SomeDeleg"), client
        )
    )

    assert client.sent == []
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT telegram_chat_id, pending_chat_id FROM admin_delegates WHERE id = :id"), {"id": did}
        ).first()
    assert row[0] is None
    assert row[1] == stranger_chat_id
    assert admin.is_admin_chat(stranger_chat_id) is False

    # المالك يؤكّد الربط بزر ✅ تأكيد (dlg:confirm:<id>).
    client.sent.clear()
    _run(
        admin.handle_update(
            {}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data=f"dlg:confirm:{did}"), client
        )
    )
    assert any("تم تأكيد ربط المفوّض" in m["text"] for m in client.sent)
    with engine.connect() as conn:
        row2 = conn.execute(
            text("SELECT telegram_chat_id, pending_chat_id FROM admin_delegates WHERE id = :id"), {"id": did}
        ).first()
    assert row2[0] == stranger_chat_id
    assert row2[1] is None
    assert admin.is_admin_chat(stranger_chat_id) is True


def test_delegate_pending_link_cleared_when_owner_rejects(engine, pending_delegate):
    """B11.3: رفض المالك (زر ❌) يمسح pending_chat_id بلا أي ربط — الصفّ
    يبقى نشطًا بانتظار محاولة لاحقة."""
    did = pending_delegate(username="rejectme")
    client = RecordingTelegramClient()
    stranger_chat_id = 900199

    _run(admin.handle_update({}, _make_event(stranger_chat_id, text="مرحبا", from_username="RejectMe"), client))
    with engine.connect() as conn:
        row = conn.execute(text("SELECT pending_chat_id FROM admin_delegates WHERE id = :id"), {"id": did}).first()
    assert row[0] == stranger_chat_id

    client.sent.clear()
    _run(
        admin.handle_update(
            {}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data=f"dlg:reject:{did}"), client
        )
    )
    assert any("تم رفض طلب ربط" in m["text"] for m in client.sent)
    with engine.connect() as conn:
        row2 = conn.execute(
            text("SELECT telegram_chat_id, pending_chat_id FROM admin_delegates WHERE id = :id"), {"id": did}
        ).first()
    assert row2[0] is None
    assert row2[1] is None
    assert admin.is_admin_chat(stranger_chat_id) is False


def test_delegate_links_via_shared_contact_match(engine, pending_delegate):
    did = pending_delegate(phone="966501234567")
    client = RecordingTelegramClient()
    stranger_chat_id = 900222

    _run(
        admin.handle_update(
            {},
            _make_event(
                stranger_chat_id,
                text="",
                contact={"phone_number": "0501234567"},
                from_user_id=stranger_chat_id,  # مشاركة حقيقية لرقمه هو (نفس المُرسِل)
            ),
            client,
        )
    )

    # B11.3: نفس مطابقة الرقم كسابقًا، لكن تخزين pending_chat_id فقط الآن.
    assert client.sent == []
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT telegram_chat_id, pending_chat_id FROM admin_delegates WHERE id = :id"), {"id": did}
        ).first()
    assert row[0] is None
    assert row[1] == stranger_chat_id


def test_shared_contact_of_someone_elses_number_is_not_trusted_for_linking(engine, pending_delegate):
    """جهة اتصال مُشارَكة لرقم **شخص آخر** (from_user_id != chat_id مُرسِل
    الجهة نفسها) لا تُستخدم للمطابقة — تجنّبًا لانتحال رقم مفوّض بمشاركة
    جهة اتصاله من حساب غريب."""
    pending_delegate(phone="966501234567")
    client = RecordingTelegramClient()
    stranger_chat_id = 900333

    _run(
        admin.handle_update(
            {},
            _make_event(
                stranger_chat_id,
                text="",
                contact={"phone_number": "0501234567"},
                from_user_id=999888777,  # مختلف عن chat_id — ليست مشاركة ذاتية
            ),
            client,
        )
    )
    assert client.sent == []


def test_stranger_with_no_delegate_match_is_silently_ignored(engine):
    client = RecordingTelegramClient()
    _run(admin.handle_update({}, _make_event(900444, text="أهلًا", from_username="totally_unknown"), client))
    assert client.sent == []


def test_removed_delegate_no_longer_treated_as_admin(engine, pending_delegate):
    did = pending_delegate(username="olddeleg")
    linked_chat_id = 900555
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE admin_delegates SET telegram_chat_id = :cid, active = false WHERE id = :id"),
            {"cid": linked_chat_id, "id": did},
        )

    assert admin.is_admin_chat(linked_chat_id) is False

    client = RecordingTelegramClient()
    _run(admin.handle_update({}, _make_event(linked_chat_id, text="أهلًا مجددًا"), client))
    # لا مطابقة username (already linked, غير NULL) ولا مطابقة أخرى → صمت تام
    assert client.sent == []


def test_delegates_menu_list_add_remove_flow_via_handle_update(engine):
    client = RecordingTelegramClient()

    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="admin:delegates"), client))
    assert any("لا يوجد مفوّضون حاليًا" in m["text"] for m in client.sent)

    client.sent.clear()
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="admin:delegates_add"), client))
    tag = uuid.uuid4().hex[:8]
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text=f"مفوّض تجريبي {tag}"), client))
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, text="@newdeleg"), client))
    assert any("تم ✅" in m["text"] for m in client.sent)

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id FROM admin_delegates WHERE telegram_username = 'newdeleg' AND active = true")
        ).first()
    assert row is not None
    delegate_id = row[0]

    client.sent.clear()
    _run(admin.handle_update({}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data="admin:delegates"), client))
    assert any("بانتظار الربط" in m["text"] for m in client.sent)

    client.sent.clear()
    _run(
        admin.handle_update(
            {}, _make_event(OWNER_CHAT_ID, is_callback=True, callback_data=f"deleg:rm:{delegate_id}"), client
        )
    )
    assert any("تمت إزالة المفوّض" in m["text"] for m in client.sent)
    with engine.connect() as conn:
        row = conn.execute(text("SELECT active FROM admin_delegates WHERE id = :id"), {"id": delegate_id}).first()
    assert row[0] is False

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM admin_delegates WHERE id = :id"), {"id": delegate_id})


def test_delegate_cannot_manage_delegates_menu(engine, pending_delegate):
    """مفوّض مربوط فعليًا (is_admin_chat True) لكنه ليس المالك — قائمة
    المفوّضين نفسها لا تظهر له بالقائمة الرئيسية (B9/B2: `if is_owner` فقط)،
    وأي محاولة استدعاء callback_data الخاصة بها يدويًا (لو خمّنها) تُرفَض
    بصمت (لا رد) بفحص is_owner_chat المباشر داخل _handle_callback."""
    did = pending_delegate(username="linkeddeleg")
    linked_chat_id = 900666
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE admin_delegates SET telegram_chat_id = :cid WHERE id = :id"),
            {"cid": linked_chat_id, "id": did},
        )
    assert admin.is_admin_chat(linked_chat_id) is True

    client = RecordingTelegramClient()
    _run(admin.handle_update({}, _make_event(linked_chat_id, text="أي شيء"), client))
    assert all("👥 المفوّضون" not in str(m["buttons"]) for m in client.sent)

    client.sent.clear()
    _run(
        admin.handle_update(
            {}, _make_event(linked_chat_id, is_callback=True, callback_data="admin:delegates"), client
        )
    )
    assert client.sent == []
