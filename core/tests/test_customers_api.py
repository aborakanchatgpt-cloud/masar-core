"""اختبارات نقاط B5c الجديدة بـcore/app/customers_api.py (docs/reports/B5a-B2b-review.md
+ n8n/README.md NEEDS-CORE #2/#3/#4):

    GET  /customers/by-telegram/{chat_id}   → {customer_id, status} أو 404
    POST /customers/{id}/status             {status: active|paused} + سجلّ تدقيق
    POST /customers/{id}/cv                 رفع سيرة PDF خام (multipart، magic bytes، حجم)

يحتاج قاعدة بيانات Postgres حقيقية — يُتخطّى تلقائيًا (skip) إن تعذّر الاتصال.
"""
from __future__ import annotations

import asyncio
import io
import os
import uuid

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import customers_api

DEFAULT_TEST_DB_URL = "postgresql+psycopg://masar_test:masar_test@localhost:5432/masar_test"


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
        "لا اتصال بقاعدة بيانات Postgres محلية للاختبار — يُتخطّى test_customers_api.py كليًا.",
        allow_module_level=True,
    )


@pytest.fixture()
def engine() -> Engine:
    return _ENGINE


@pytest.fixture(autouse=True)
def _patch_engine(monkeypatch, engine):
    monkeypatch.setattr(customers_api, "get_engine", lambda: engine)


@pytest.fixture()
def customer_id(engine: Engine):
    tag = uuid.uuid4().hex[:10]
    with engine.begin() as conn:
        cid = conn.execute(
            text(
                "INSERT INTO customers (name, email_service, status, target_daily, telegram_chat_id) "
                "VALUES (:n, :e, 'active', 17, :tg) RETURNING id"
            ),
            {"n": f"Customers API Test {tag}", "e": f"customers-api-{tag}@masar.invalid", "tg": None},
        ).scalar_one()
    yield cid
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM customer_status_audit WHERE customer_id = :cid"), {"cid": cid})
        conn.execute(text("DELETE FROM customers WHERE id = :cid"), {"cid": cid})


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# GET /customers/by-telegram/{chat_id}
# ---------------------------------------------------------------------------


def test_by_telegram_returns_customer_id_and_status(engine, customer_id):
    chat_id = 900_000_000 + customer_id
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE customers SET telegram_chat_id = :tg WHERE id = :id"),
            {"tg": chat_id, "id": customer_id},
        )

    result = _run(customers_api.get_customer_by_telegram(chat_id))
    assert result == {"customer_id": customer_id, "status": "active"}


def test_by_telegram_unknown_chat_id_returns_404(engine, customer_id):
    with pytest.raises(HTTPException) as exc_info:
        _run(customers_api.get_customer_by_telegram(0))
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# POST /customers/{id}/status
# ---------------------------------------------------------------------------


def test_status_transition_active_to_paused_records_audit(engine, customer_id):
    body = customers_api.CustomerStatusRequest(status="paused", note="طلب العميل توقفًا مؤقتًا")
    result = _run(customers_api.update_customer_status(customer_id, body))

    assert result == {
        "ok": True, "customer_id": customer_id, "old_status": "active", "new_status": "paused",
    }

    with engine.connect() as conn:
        row = conn.execute(text("SELECT status FROM customers WHERE id = :id"), {"id": customer_id}).first()
        audit = conn.execute(
            text(
                "SELECT old_status, new_status, note FROM customer_status_audit "
                "WHERE customer_id = :id ORDER BY id DESC LIMIT 1"
            ),
            {"id": customer_id},
        ).mappings().first()
    assert row[0] == "paused"
    assert audit["old_status"] == "active"
    assert audit["new_status"] == "paused"
    assert audit["note"] == "طلب العميل توقفًا مؤقتًا"


def test_status_invalid_value_returns_400(engine, customer_id):
    body = customers_api.CustomerStatusRequest(status="expired", note=None)
    with pytest.raises(HTTPException) as exc_info:
        _run(customers_api.update_customer_status(customer_id, body))
    assert exc_info.value.status_code == 400


def test_status_unknown_customer_returns_404(engine, customer_id):
    body = customers_api.CustomerStatusRequest(status="paused", note=None)
    with pytest.raises(HTTPException) as exc_info:
        _run(customers_api.update_customer_status(customer_id + 999_999, body))
    assert exc_info.value.status_code == 404


def test_status_expired_customer_rejects_transition(engine, customer_id):
    with engine.begin() as conn:
        conn.execute(text("UPDATE customers SET status = 'expired' WHERE id = :id"), {"id": customer_id})

    body = customers_api.CustomerStatusRequest(status="active", note=None)
    with pytest.raises(HTTPException) as exc_info:
        _run(customers_api.update_customer_status(customer_id, body))
    assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# POST /customers/{id}/cv
# ---------------------------------------------------------------------------


_MINIMAL_PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>"


def _upload_file(content: bytes, filename: str = "cv.pdf") -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=filename)


def test_cv_upload_accepts_valid_pdf_and_records_hash(monkeypatch, tmp_path, engine, customer_id):
    monkeypatch.setenv("CV_DATA_DIR", str(tmp_path))

    result = _run(customers_api.upload_customer_cv(customer_id, file=_upload_file(_MINIMAL_PDF)))

    assert result["ok"] is True
    assert result["bytes"] == len(_MINIMAL_PDF)
    stored_path = tmp_path / str(customer_id) / "uploaded_cv.pdf"
    assert stored_path.is_file()
    assert stored_path.read_bytes() == _MINIMAL_PDF

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT cv_pdf_path, cv_pdf_sha256, cv_pdf_uploaded_at FROM customers WHERE id = :id"),
            {"id": customer_id},
        ).mappings().first()
    assert row["cv_pdf_path"] == str(stored_path)
    assert row["cv_pdf_sha256"] == result["cv_pdf_sha256"]
    assert row["cv_pdf_uploaded_at"] is not None


def test_cv_upload_rejects_non_pdf(monkeypatch, tmp_path, engine, customer_id):
    monkeypatch.setenv("CV_DATA_DIR", str(tmp_path))
    with pytest.raises(HTTPException) as exc_info:
        _run(customers_api.upload_customer_cv(customer_id, file=_upload_file(b"this is not a pdf at all")))
    assert exc_info.value.status_code == 400


def test_cv_upload_rejects_oversized_file(monkeypatch, tmp_path, engine, customer_id):
    monkeypatch.setenv("CV_DATA_DIR", str(tmp_path))
    oversized = b"%PDF-1.4\n" + b"0" * (customers_api.CV_MAX_BYTES + 1)
    with pytest.raises(HTTPException) as exc_info:
        _run(customers_api.upload_customer_cv(customer_id, file=_upload_file(oversized)))
    assert exc_info.value.status_code == 400


def test_cv_upload_unknown_customer_returns_404(monkeypatch, tmp_path, engine, customer_id):
    monkeypatch.setenv("CV_DATA_DIR", str(tmp_path))
    with pytest.raises(HTTPException) as exc_info:
        _run(customers_api.upload_customer_cv(customer_id + 999_999, file=_upload_file(_MINIMAL_PDF)))
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# CustomerCreateRequest: email_service اختياري الآن (B5c، ترحيل 0010)
# ---------------------------------------------------------------------------


def test_create_customer_without_email_service(engine):
    body = customers_api.CustomerCreateRequest(name="بلا بريد بعد", telegram_chat_id=None)
    result = _run(customers_api.create_customer(body))
    assert result["ok"] is True

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT email_service FROM customers WHERE id = :id"), {"id": result["customer_id"]}
        ).first()
    assert row[0] is None

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM wallets WHERE customer_id = :id"), {"id": result["customer_id"]})
        conn.execute(text("DELETE FROM customers WHERE id = :id"), {"id": result["customer_id"]})
