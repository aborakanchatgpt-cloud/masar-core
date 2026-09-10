"""اختبارات core/app/telegram_onboarding.py (B8) — مسار onboarding كامل
من عميل "سجّله أحمد" عبر بوت الأدمن (بالاسم والجوال فقط) حتى اكتمال
الملف، عبر عميل تيليجرام مزيَّف بالكامل (RecordingTelegramClient) — صفر
استدعاءات شبكية حقيقية لـTelegram.

يحتاج قاعدة بيانات Postgres حقيقية مهاجَرة حتى 0013 (جدول telegram_sessions)
— يُتخطّى تلقائيًا (skip) إن تعذّر الاتصال، نفس نمط test_catalog.py/
test_link_api.py. الدوال النقية بلا قاعدة بيانات موجودة بملف منفصل
test_telegram_onboarding.py (يعمل دومًا، لا يتأثّر بتخطّي هذا الملف).
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import telegram_onboarding as ob
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
        "لا اتصال بقاعدة بيانات Postgres محلية مهاجَرة (0013) — يُتخطّى test_telegram_onboarding_db.py كليًا.",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# مزيّف عميل تيليجرام — يسجّل كل استدعاء، بلا أي شبكة حقيقية
# ---------------------------------------------------------------------------


class RecordingTelegramClient:
    def __init__(self, *, file_path: str = "docs/f.pdf", file_bytes: bytes = b""):
        self.sent: list[dict[str, Any]] = []
        self._file_path = file_path
        self._file_bytes = file_bytes

    async def send_message(self, chat_id, text_, *, buttons=None, disable_web_page_preview=True):
        self.sent.append({"type": "message", "chat_id": chat_id, "text": text_, "buttons": buttons})
        return {"message_id": len(self.sent)}

    async def send_contact_request(self, chat_id, text_, button_text):
        self.sent.append({"type": "contact_request", "chat_id": chat_id, "text": text_, "button_text": button_text})
        return {"message_id": len(self.sent)}

    async def answer_callback_query(self, callback_query_id, *, text=None, show_alert=False):
        self.sent.append({"type": "answer_callback", "id": callback_query_id})
        return {}

    async def edit_message_text(self, chat_id, message_id, text_, *, buttons=None):
        self.sent.append({"type": "edit", "chat_id": chat_id, "text": text_})
        return {}

    async def get_file(self, file_id):
        return {"file_path": self._file_path}

    async def download_file_bytes(self, file_path, *, max_bytes=8 * 1024 * 1024):
        return self._file_bytes


def _make_event(chat_id, *, text="", is_callback=False, callback_data="", document=None, contact=None, from_user_id=None):
    return ChatEvent(
        chat_id=chat_id,
        text=text,
        is_callback=is_callback,
        callback_data=callback_data,
        callback_query_id="cb1" if is_callback else None,
        message_id=1,
        document=document,
        photo=None,
        contact=contact,
        from_user_id=from_user_id if from_user_id is not None else chat_id,
    )


@pytest.fixture()
def engine() -> Engine:
    return _ENGINE


@pytest.fixture(autouse=True)
def _patch_engine(monkeypatch, engine):
    monkeypatch.setattr(ob, "get_engine", lambda: engine)
    import app.customers_api as customers_api

    monkeypatch.setattr(customers_api, "get_engine", lambda: engine)
    import app.link_api as link_api

    monkeypatch.setattr(link_api, "get_engine", lambda: engine)


@pytest.fixture()
def registered_customer(engine):
    """عميل "سجّله أحمد" — بالاسم والجوال فقط، telegram_chat_id لا يزال NULL
    (تمامًا كخطوة "تسجيل عميل جديد" ببوت الأدمن)."""
    tag = uuid.uuid4().hex[:10]
    # رقم جوال حقيقي (أرقام فقط) — يطابق ما يخزّنه بوت الأدمن فعليًا
    # (app.telegram_admin._reply_create_customer يُطبّع الجوال لأرقام فقط
    # قبل التخزين)، لا نصًا سداسي عشريًا قد يحوي أحرفًا (uuid.hex) كان
    # يفشل هنا بصمت لأن normalize_phone تحذف الأحرف بحق (سلوك مقصود).
    phone = "05" + str(uuid.uuid4().int)[:8]
    with engine.begin() as conn:
        cid = conn.execute(
            text(
                "INSERT INTO customers (name, phone, email_service, status, target_daily) "
                "VALUES (:n, :p, :e, 'active', 17) RETURNING id"
            ),
            {"n": f"Tele Test {tag}", "p": phone, "e": f"tele-{tag}@masar.invalid"},
        ).scalar_one()
    yield {"id": cid, "phone": phone}
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM telegram_sessions WHERE chat_id = ANY(:ids)"), {"ids": [cid, cid + 10**9]})
        conn.execute(text("DELETE FROM profiles WHERE customer_id = :id"), {"id": cid})
        conn.execute(text("DELETE FROM link_tokens WHERE customer_id = :id"), {"id": cid})
        conn.execute(text("DELETE FROM customers WHERE id = :id"), {"id": cid})


def test_session_roundtrip(engine, registered_customer):
    chat_id = registered_customer["id"] + 10**9
    ob._save_session(chat_id, "ask_cities", {"cities": ["الرياض"]})
    step, data = ob._get_session(chat_id)
    assert step == "ask_cities"
    assert data["cities"] == ["الرياض"]
    ob._clear_session(chat_id)
    step2, data2 = ob._get_session(chat_id)
    assert step2 == ""
    assert data2 == {}


def test_handle_update_links_customer_by_shared_contact(engine, registered_customer):
    chat_id = registered_customer["id"] + 10**9
    event = _make_event(
        chat_id,
        contact={"phone_number": registered_customer["phone"], "user_id": chat_id},
    )
    client = RecordingTelegramClient()

    _run(ob.handle_update({}, event, client))

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT telegram_chat_id FROM customers WHERE id = :id"), {"id": registered_customer["id"]}
        ).first()
    assert row[0] == chat_id
    assert any("تم التحقق" in m["text"] for m in client.sent if m["type"] == "message")


def test_handle_update_unmatched_phone_does_not_link_anyone(engine, registered_customer):
    chat_id = registered_customer["id"] + 10**9
    event = _make_event(chat_id, contact={"phone_number": "0500000000", "user_id": chat_id})
    client = RecordingTelegramClient()

    _run(ob.handle_update({}, event, client))

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT telegram_chat_id FROM customers WHERE id = :id"), {"id": registered_customer["id"]}
        ).first()
    assert row[0] is None
    assert any("ما لقينا" in m["text"] for m in client.sent if m["type"] == "message")


def test_full_onboarding_flow_cv_cities_families(engine, registered_customer, monkeypatch, tmp_path):
    monkeypatch.setenv("CV_DATA_DIR", str(tmp_path))
    chat_id = registered_customer["id"] + 10**9
    client = RecordingTelegramClient(file_path="docs/cv.pdf", file_bytes=b"%PDF-1.4 fake but has the magic bytes")

    # 1) ربط الرقم
    _run(ob.handle_update({}, _make_event(chat_id, contact={"phone_number": registered_customer["phone"], "user_id": chat_id}), client))

    # 2) رفع السيرة (PDF) — نموّه extract_pdf_text حتى لا نعتمد على محتوى
    # حقيقي قابل للقراءة داخل بايتات وهمية (لا PDF فعلي هنا، فقط توقيعه).
    monkeypatch.setattr(ob, "extract_pdf_text", lambda content: "نص سيرة ذاتية كافٍ للاختبار " * 3)
    doc_event = _make_event(chat_id, document={"file_id": "f1", "mime_type": "application/pdf"})
    _run(ob.handle_update({}, doc_event, client))

    with engine.connect() as conn:
        cv_row = conn.execute(
            text("SELECT cv_pdf_path FROM customers WHERE id = :id"), {"id": registered_customer["id"]}
        ).first()
        profile_row = conn.execute(
            text("SELECT cv_text FROM profiles WHERE customer_id = :id"), {"id": registered_customer["id"]}
        ).first()
    assert cv_row[0] is not None
    assert profile_row is not None and "سيرة" in profile_row[0]

    # 3) اختيار مدينة عبر زر inline
    city_event = _make_event(chat_id, is_callback=True, callback_data="city:الرياض")
    _run(ob.handle_update({}, city_event, client))
    done_event = _make_event(chat_id, is_callback=True, callback_data="city:done")
    _run(ob.handle_update({}, done_event, client))

    # 4) كتابة مجال مهني يُصنَّف تلقائيًا، ثم تأكيد الإنهاء
    monkeypatch.setattr(ob, "classify_family", lambda text: "accounting" if text else None)
    family_event = _make_event(chat_id, text="محاسبة")
    _run(ob.handle_update({}, family_event, client))
    confirm_event = _make_event(chat_id, is_callback=True, callback_data="families:ok")
    _run(ob.handle_update({}, confirm_event, client))

    with engine.connect() as conn:
        final = conn.execute(
            text("SELECT cities, families FROM customers WHERE id = :id"), {"id": registered_customer["id"]}
        ).mappings().first()
    assert "الرياض" in list(final["cities"])
    assert "accounting" in list(final["families"])
    assert any("تم كل شي بنجاح" in m["text"] for m in client.sent if m["type"] == "message")
