"""اختبارات core/app/telegram_payments.py + telegram_admin_payments.py
(B9/B3) — تدفّق الباقات والدفع الكامل ببوت العميل (تسجيل ذاتي → اسم →
باقة → شروط → بيانات تحويل → إيصال → مبلغ → اسم مُحوّل → إشعار أدمن)
وقرار الأدمن ✅/❌ (تفعيل فعلي عبر catalog.create_order أو رفض).

قسمان: دوال نقية (بلا قاعدة بيانات، تعمل دومًا)، وتدفّق كامل بقاعدة بيانات
حقيقية مهاجَرة حتى 0018 (`payment_requests`/`bank_accounts`) — يُتخطّى
تلقائيًا (skip) إن تعذّر الاتصال، نفس نمط بقية اختبارات B9 الموجودة
(`test_telegram_onboarding_db.py`/`test_telegram_admin_b5_db.py`)."""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import catalog
from app import customers_api
from app import telegram_admin_payments as admin_payments
from app import telegram_admin_settings as settings_mod
from app import telegram_onboarding as ob
from app import telegram_payments as pay
from app.telegram_client import ChatEvent

DEFAULT_TEST_DB_URL = "postgresql+psycopg://masar_test:masar_test@localhost:5432/masar_test"


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# دوال نقية — تعمل دومًا، بلا قاعدة بيانات
# ---------------------------------------------------------------------------


def test_build_packages_message_and_buttons():
    products = [
        {
            "code": "SUB30", "name_ar": "اشتراك شهري", "kind": "subscription",
            "price_sar": 90, "days": 30, "applications_included": 510,
        },
        {
            "code": "CR100", "name_ar": "رصيد 100 تقديم", "kind": "credits",
            "price_sar": 60, "days": None, "applications_included": 100,
        },
    ]
    msg = pay.build_packages_message(products)
    assert "90 ريال" in msg
    assert "60 ريال" in msg
    assert "ضمان" in msg  # وصف الاشتراك فقط

    buttons = pay.build_packages_buttons(products)
    assert buttons[0][0]["callback_data"] == "pkg:SUB30"
    assert buttons[1][0]["callback_data"] == "pkg:CR100"
    assert buttons[-1][0]["callback_data"] == "pkgwhich"


def test_parse_amount_variants():
    assert pay._parse_amount("90") == 90.0
    assert pay._parse_amount("90 ريال") == 90.0
    assert pay._parse_amount("90.50") == 90.5
    assert pay._parse_amount("تسعين") is None
    assert pay._parse_amount("   ") is None
    assert pay._parse_amount("0") == 0.0  # يُرفَض لاحقًا بفحص amount<=0 بالمستدعي، لا هنا


def test_build_bank_message_single_bank():
    msg = pay.build_bank_message(
        [{"bank_name": "بنك الراجحي", "account_holder": "شركة مسار", "iban": "SA0000000000000000"}],
        "اشتراك شهري",
        90.0,
    )
    assert "بنك الراجحي" in msg
    assert "شركة مسار" in msg
    assert "SA0000000000000000" in msg
    assert "90" in msg
    assert "1)" not in msg  # حساب واحد فقط — بلا ترقيم


def test_build_bank_message_multiple_banks_numbered():
    msg = pay.build_bank_message(
        [
            {"bank_name": "بنك الراجحي", "account_holder": "شركة مسار", "iban": "SA1111111111111111"},
            {"bank_name": "بنك الأهلي", "account_holder": "شركة مسار", "iban": "SA2222222222222222"},
        ],
        "اشتراك شهري",
        90.0,
    )
    assert "بنك الراجحي" in msg and "بنك الأهلي" in msg
    assert "SA1111111111111111" in msg and "SA2222222222222222" in msg
    assert "1) 🏦" in msg and "2) 🏦" in msg


def test_build_bank_message_account_number_only_no_iban():
    """B3-متابعة٢: iban اختياري الآن — حساب بلا آيبان (رقم حساب فقط) يظهر
    برقم الحساب فقط، بلا سطر IBAN فارغ أو مضلّل."""
    msg = pay.build_bank_message(
        [{"bank_name": "بنك سامبا", "account_holder": "شركة مسار", "account_number": "1234567890", "iban": None}],
        "اشتراك شهري",
        90.0,
    )
    assert "1234567890" in msg
    assert "رقم الحساب" in msg
    assert "IBAN" not in msg


def test_build_bank_message_both_account_number_and_iban():
    msg = pay.build_bank_message(
        [
            {
                "bank_name": "بنك الرياض",
                "account_holder": "شركة مسار",
                "account_number": "555000111",
                "iban": "SA0000000000000000",
            }
        ],
        "اشتراك شهري",
        90.0,
    )
    assert "555000111" in msg
    assert "SA0000000000000000" in msg
    assert "رقم الحساب" in msg and "IBAN" in msg


def test_build_terms_message_includes_domain_link():
    msg = pay.build_terms_message("masar.example.com")
    assert "https://masar.example.com/terms" in msg


def test_build_terms_message_handles_missing_domain():
    msg = pay.build_terms_message("")
    assert "الرابط غير متاح" in msg


# ---------------------------------------------------------------------------
# تدفّق كامل بقاعدة بيانات حقيقية
# ---------------------------------------------------------------------------


def _make_engine() -> Engine | None:
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DB_URL)
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM payment_requests LIMIT 1"))
            conn.execute(text("SELECT 1 FROM bank_accounts LIMIT 1"))
        return engine
    except Exception:  # noqa: BLE001
        return None


_ENGINE = _make_engine()

if _ENGINE is None:
    pytest.skip(
        "لا اتصال بقاعدة بيانات Postgres محلية مهاجَرة (0018) — يُتخطّى test_telegram_payments_b9.py كليًا.",
        allow_module_level=True,
    )


class RecordingTelegramClient:
    def __init__(self, *, file_path: str = "docs/receipt.jpg", file_bytes: bytes = b"fake-receipt-bytes"):
        self.sent: list[dict[str, Any]] = []
        self._file_path = file_path
        self._file_bytes = file_bytes

    async def send_message(self, chat_id, text_, *, buttons=None, disable_web_page_preview=True):
        self.sent.append({"type": "message", "chat_id": chat_id, "text": text_, "buttons": buttons})
        return {"message_id": len(self.sent)}

    async def send_contact_request(self, chat_id, text_, button_text):
        self.sent.append({"type": "contact_request", "chat_id": chat_id, "text": text_})
        return {"message_id": len(self.sent)}

    async def answer_callback_query(self, callback_query_id, *, text=None, show_alert=False):
        return {}

    async def edit_message_reply_markup(self, chat_id, message_id, *, buttons=None):
        self.sent.append({"type": "edit_markup", "chat_id": chat_id, "message_id": message_id, "buttons": buttons})
        return {}

    async def get_file(self, file_id):
        return {"file_path": self._file_path}

    async def download_file_bytes(self, file_path, *, max_bytes=8 * 1024 * 1024):
        return self._file_bytes

    async def send_photo(self, chat_id, data, filename, *, caption=None, buttons=None):
        self.sent.append({"type": "photo", "chat_id": chat_id, "caption": caption, "buttons": buttons})
        return {"message_id": len(self.sent)}

    async def send_document(self, chat_id, data, filename, *, caption=None, buttons=None):
        self.sent.append({"type": "document", "chat_id": chat_id, "caption": caption, "buttons": buttons})
        return {"message_id": len(self.sent)}


def _make_event(
    chat_id, *, text="", is_callback=False, callback_data="", document=None, photo=None, contact=None,
    from_user_id=None,
):
    return ChatEvent(
        chat_id=chat_id,
        text=text,
        is_callback=is_callback,
        callback_data=callback_data,
        callback_query_id="cb1" if is_callback else None,
        message_id=5,
        document=document,
        photo=photo,
        contact=contact,
        from_user_id=from_user_id if from_user_id is not None else chat_id,
    )


@pytest.fixture()
def engine() -> Engine:
    return _ENGINE


@pytest.fixture(autouse=True)
def _patch_engine(monkeypatch, engine):
    for mod in (ob, pay, admin_payments, settings_mod, catalog, customers_api):
        monkeypatch.setattr(mod, "get_engine", lambda eng=engine: eng)


@pytest.fixture()
def priced_package(engine):
    """يضبط باقة CR100 (رصيد 100 تقديم) بسعر معروف مؤقتًا للاختبار — تُعاد
    لقيمتها الأصلية (NULL حيًا اليوم، بانتظار أحمد ب⚙️ الإعدادات) بعده."""
    with engine.begin() as conn:
        original = conn.execute(text("SELECT price_sar FROM products WHERE code = 'CR100'")).scalar()
        conn.execute(text("UPDATE products SET price_sar = 60, active = true WHERE code = 'CR100'"))
    yield {"code": "CR100", "name_ar": "رصيد 100 تقديم", "price_sar": 60.0}
    with engine.begin() as conn:
        conn.execute(text("UPDATE products SET price_sar = :p WHERE code = 'CR100'"), {"p": original})


@pytest.fixture()
def bank_details(engine):
    """B3-متابعة: بيانات التحويل انتقلت لجدول bank_accounts — صفّ اختبار
    نشط واحد يُنظّف بعد الاختبار (DELETE محلي فقط، قاعدة بيانات اختبار
    مؤقتة — لا صلة بقاعدة الإنتاج الحية، راجع القواعد المطلقة بالمشروع)."""
    with engine.begin() as conn:
        account_id = conn.execute(
            text(
                "INSERT INTO bank_accounts (bank_name, account_holder, iban, active) "
                "VALUES ('بنك الاختبار', 'مسار', 'SA0000000000000000', true) RETURNING id"
            )
        ).scalar_one()
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM bank_accounts WHERE id = :id"), {"id": account_id})


def _cleanup_customer(engine: Engine, chat_id: int, customer_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM telegram_sessions WHERE chat_id = :cid"), {"cid": chat_id})
        conn.execute(text("DELETE FROM payment_requests WHERE customer_id = :id"), {"id": customer_id})
        conn.execute(text("DELETE FROM ledger WHERE customer_id = :id"), {"id": customer_id})
        conn.execute(text("DELETE FROM orders WHERE customer_id = :id"), {"id": customer_id})
        conn.execute(text("DELETE FROM wallets WHERE customer_id = :id"), {"id": customer_id})
        conn.execute(text("DELETE FROM customers WHERE id = :id"), {"id": customer_id})


def test_full_payment_flow_self_register_to_confirmed(engine, priced_package, bank_details, monkeypatch, tmp_path):
    monkeypatch.setenv("CV_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MASAR_OWNER_CHAT_ID", "999888")
    monkeypatch.setenv("TELEGRAM_ADMIN_BOT_TOKEN", "fake-admin-token")

    admin_recorder = RecordingTelegramClient()
    monkeypatch.setattr(pay, "get_admin_bot_client", lambda: admin_recorder)

    tag = uuid.uuid4().hex[:8]
    phone_raw = "05" + str(uuid.uuid4().int)[:8]
    chat_id = int(uuid.uuid4().int % 10**9) + 3_000_000_000
    client = RecordingTelegramClient()

    # 1) أول تواصل بلا contact → طلب مشاركة الرقم
    _run(ob.handle_update({}, _make_event(chat_id), client))
    assert any(m["type"] == "contact_request" for m in client.sent)

    # 2) مشاركة الرقم — لا صفّ مطابق → تسجيل ذاتي فورًا
    _run(ob.handle_update({}, _make_event(chat_id, contact={"phone_number": phone_raw, "user_id": chat_id}), client))
    with engine.connect() as conn:
        new_customer = conn.execute(
            text("SELECT id, status FROM customers WHERE telegram_chat_id = :cid"), {"cid": chat_id}
        ).mappings().first()
    assert new_customer is not None and new_customer["status"] == "pending"
    customer_id = new_customer["id"]

    try:
        # 3) الاسم → عرض الباقات
        _run(ob.handle_update({}, _make_event(chat_id, text=f"عميل اختبار {tag}"), client))
        with engine.connect() as conn:
            name_row = conn.execute(text("SELECT name FROM customers WHERE id = :id"), {"id": customer_id}).scalar()
        assert name_row == f"عميل اختبار {tag}"
        assert any(f"pkg:{priced_package['code']}" in str(m.get("buttons")) for m in client.sent if m["type"] == "message")

        # 4) اختيار الباقة → الشروط (أول مرة، terms_accepted_at لا يزال NULL)
        _run(
            ob.handle_update(
                {}, _make_event(chat_id, is_callback=True, callback_data=f"pkg:{priced_package['code']}"), client
            )
        )
        assert any("terms:accept" in str(m.get("buttons")) for m in client.sent if m["type"] == "message")

        # 5) الموافقة على الشروط → بيانات التحويل + إنشاء payment_requests
        _run(ob.handle_update({}, _make_event(chat_id, is_callback=True, callback_data="terms:accept"), client))
        with engine.connect() as conn:
            terms_row = conn.execute(
                text("SELECT terms_accepted_at FROM customers WHERE id = :id"), {"id": customer_id}
            ).scalar()
            pr_row = conn.execute(
                text("SELECT id, status, expected_amount FROM payment_requests WHERE customer_id = :id"),
                {"id": customer_id},
            ).mappings().first()
        assert terms_row is not None
        assert pr_row is not None and pr_row["status"] == "pending"
        assert float(pr_row["expected_amount"]) == priced_package["price_sar"]
        request_id = pr_row["id"]
        assert any("حوّل المبلغ" in m["text"] for m in client.sent if m["type"] == "message")

        # 6) إرسال الإيصال (صورة)
        _run(ob.handle_update({}, _make_event(chat_id, photo=[{"file_id": "ph1", "file_size": 100}]), client))
        with engine.connect() as conn:
            receipt = conn.execute(
                text("SELECT receipt_path, receipt_kind FROM payment_requests WHERE id = :id"), {"id": request_id}
            ).mappings().first()
        assert receipt["receipt_kind"] == "photo"
        assert receipt["receipt_path"] and os.path.exists(receipt["receipt_path"])

        # 7) المبلغ (مطابق تمامًا لقيمة الباقة)
        _run(ob.handle_update({}, _make_event(chat_id, text=str(int(priced_package["price_sar"]))), client))

        # 8) اسم المُحوّل → إشعار الأدمن بالصورة + زرّي ✅/❌ + رسالة إكمال الملف
        _run(ob.handle_update({}, _make_event(chat_id, text="أحمد المرسل"), client))
        with engine.connect() as conn:
            final_pr = conn.execute(
                text("SELECT declared_amount, sender_name FROM payment_requests WHERE id = :id"), {"id": request_id}
            ).mappings().first()
        assert final_pr["sender_name"] == "أحمد المرسل"
        assert float(final_pr["declared_amount"]) == priced_package["price_sar"]
        photo_notices = [m for m in admin_recorder.sent if m["type"] == "photo"]
        assert photo_notices and f"pay:ok:{request_id}" in str(photo_notices[0]["buttons"])
        assert any("سيرتك الذاتية" in m["text"] for m in client.sent if m["type"] == "message")

        # 9) الجلسة نظيفة الآن — الرسالة التالية من العميل تدخل خطوة CV طبيعيًا
        step, _data = ob._get_session(chat_id)
        assert step == ""

        # 10) قرار الأدمن ✅ — تفعيل فعلي عبر catalog.create_order (رصيد 100)
        admin_client = RecordingTelegramClient()
        _run(admin_payments.decide_payment(admin_client, 999888, 5, request_id, "ok"))
        with engine.connect() as conn:
            activated_status = conn.execute(
                text("SELECT status FROM customers WHERE id = :id"), {"id": customer_id}
            ).scalar()
            pr_final_status = conn.execute(
                text("SELECT status FROM payment_requests WHERE id = :id"), {"id": request_id}
            ).scalar()
            wallet_balance = conn.execute(
                text("SELECT balance FROM wallets WHERE customer_id = :id"), {"id": customer_id}
            ).scalar()
        assert activated_status == "active"
        assert pr_final_status == "confirmed"
        assert wallet_balance == 100
        assert any("تم تأكيد الدفع" in m["text"] for m in admin_client.sent if m["type"] == "message")

        # 11) ضغط مزدوج على نفس القرار — رفض بصمت (بلا تفعيل مزدوج)
        _run(admin_payments.decide_payment(admin_client, 999888, 5, request_id, "ok"))
        assert any("تم البتّ" in m["text"] for m in admin_client.sent if m["type"] == "message")
    finally:
        _cleanup_customer(engine, chat_id, customer_id)


def test_payment_rejected_notifies_customer_with_retry_buttons(engine, priced_package, bank_details, monkeypatch, tmp_path):
    monkeypatch.setenv("CV_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MASAR_OWNER_CHAT_ID", "999888")
    monkeypatch.setenv("TELEGRAM_CUSTOMER_BOT_TOKEN", "fake-customer-token")
    monkeypatch.setattr(pay, "get_admin_bot_client", lambda: None)  # لا حاجة لإشعار الأدمن بهذا الاختبار

    customer_recorder = RecordingTelegramClient()
    monkeypatch.setattr(admin_payments, "get_customer_bot_client", lambda: customer_recorder)

    chat_id = int(uuid.uuid4().int % 10**9) + 4_000_000_000
    with engine.begin() as conn:
        customer_id = conn.execute(
            text(
                "INSERT INTO customers (telegram_chat_id, phone, name, target_daily, status) "
                "VALUES (:cid, :phone, 'عميل رفض', 17, 'pending') RETURNING id"
            ),
            {"cid": chat_id, "phone": "9665" + str(uuid.uuid4().int)[:8]},
        ).scalar_one()
        conn.execute(text("INSERT INTO wallets (customer_id, balance) VALUES (:id, 0)"), {"id": customer_id})
        request_id = conn.execute(
            text(
                "INSERT INTO payment_requests (customer_id, product_code, expected_amount, declared_amount, "
                "sender_name, status) VALUES (:cid, :code, :amount, :amount, 'مرسل', 'pending') RETURNING id"
            ),
            {"cid": customer_id, "code": priced_package["code"], "amount": priced_package["price_sar"]},
        ).scalar_one()

    try:
        admin_client = RecordingTelegramClient()
        _run(admin_payments.decide_payment(admin_client, 999888, 7, request_id, "no"))

        with engine.connect() as conn:
            pr_status = conn.execute(text("SELECT status FROM payment_requests WHERE id = :id"), {"id": request_id}).scalar()
            customer_status = conn.execute(text("SELECT status FROM customers WHERE id = :id"), {"id": customer_id}).scalar()
        assert pr_status == "rejected"
        assert customer_status == "pending"  # لم يُفعّل — الرفض لا يمسّ حالة العميل

        rejection = [m for m in customer_recorder.sent if m["type"] == "message"]
        assert rejection and "ما قدرنا نتأكد" in rejection[0]["text"]
        assert f"payretry:{priced_package['code']}" in str(rejection[0]["buttons"])
        assert any("edit_markup" == m["type"] for m in admin_client.sent) or any(
            m["type"] == "edit_markup" for m in admin_client.sent
        )
    finally:
        _cleanup_customer(engine, chat_id, customer_id)


def test_start_with_no_priced_products_notifies_admin_and_holds(engine, monkeypatch):
    monkeypatch.setenv("MASAR_OWNER_CHAT_ID", "999888")
    monkeypatch.setattr(pay, "_fetch_priced_products", lambda: [])
    admin_recorder = RecordingTelegramClient()
    monkeypatch.setattr(pay, "get_admin_bot_client", lambda: admin_recorder)

    client = RecordingTelegramClient()
    chat_id = 5_555_555_555
    _run(pay.start(client, chat_id, 999999))

    assert any("نجهّز باقاتنا" in m["text"] for m in client.sent if m["type"] == "message")
    assert any("نحتاجك فورا" in m["text"] for m in admin_recorder.sent if m["type"] == "message")
    step, _data = ob._get_session(chat_id)
    assert step == "await_package"
    ob._clear_session(chat_id)
