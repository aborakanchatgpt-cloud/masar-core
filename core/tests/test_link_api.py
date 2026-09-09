"""اختبارات core/app/link_api.py (B5a البند 5) — صفحة ربط البريد الذاتي:
إنشاء رمز (admin)، صفحة العرض (رابط صالح/غير صالح/منتهِّ/مُستخدم)، الإرسال
الناجح (skip_verify فقط مع DRY_RUN + ?skip_verify=1)، رفض كلمة مرور غير
16 حرفًا، والحدّ الأقصى 5 محاولات لكل رمز.

يحتاج قاعدة بيانات Postgres حقيقية وMAIL_FERNET_KEY صالحًا — يُتخطّى
تلقائيًا (skip) إن تعذّرا.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from starlette.datastructures import FormData
from starlette.requests import Request

from app import link_api

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
        "لا اتصال بقاعدة بيانات Postgres محلية للاختبار — يُتخطّى test_link_api.py كليًا.",
        allow_module_level=True,
    )

if not os.environ.get("MAIL_FERNET_KEY"):
    os.environ["MAIL_FERNET_KEY"] = Fernet.generate_key().decode("ascii")


@pytest.fixture()
def engine() -> Engine:
    return _ENGINE


@pytest.fixture(autouse=True)
def _patch_engine(monkeypatch, engine):
    monkeypatch.setattr(link_api, "get_engine", lambda: engine)


@pytest.fixture()
def customer_id(engine):
    tag = uuid.uuid4().hex[:10]
    with engine.begin() as conn:
        cid = conn.execute(
            text(
                "INSERT INTO customers (name, email_service, status, target_daily) "
                "VALUES (:n, :e, 'active', 17) RETURNING id"
            ),
            {"n": f"Link Test {tag}", "e": f"link-{tag}@masar.invalid"},
        ).scalar_one()
    yield cid
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM link_tokens WHERE customer_id = :cid"), {"cid": cid})
        conn.execute(text("DELETE FROM mail_links WHERE customer_id = :cid"), {"cid": cid})
        conn.execute(text("DELETE FROM customers WHERE id = :cid"), {"cid": cid})


def _run(coro):
    return asyncio.run(coro)


def _make_form_request(form_dict: dict) -> Request:
    """يبني Request بجسد form-urlencoded حقيقي (لا تمويه) — request.form()
    الفعلية بـStarlette تحتاج جسدًا حقيقيًا مُرمّزًا، لا اعتراضًا يدويًا."""
    body = "&".join(f"{k}={v}" for k, v in form_dict.items()).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
    }
    return Request(scope, receive)


# ---------------------------------------------------------------------------
# 1. إنشاء رمز (admin) — 200 + مسار /link/<token>.
# ---------------------------------------------------------------------------


def test_create_link_token(engine, customer_id):
    result = _run(link_api.create_link_token(customer_id))
    assert result["ok"] is True
    assert result["path"].startswith("/link/")

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT customer_id, attempts, used_at FROM link_tokens WHERE id = :id"),
            {"id": result["link_token_id"]},
        ).mappings().first()
    assert row["customer_id"] == customer_id
    assert row["attempts"] == 0
    assert row["used_at"] is None


# ---------------------------------------------------------------------------
# 2. صفحة العرض — رابط غير موجود/منتهِّ/مُستخدم → صفحة خطأ ودّية (200، بلا
#    نموذج)؛ رابط صالح → صفحة تحمل النموذج.
# ---------------------------------------------------------------------------


def test_show_form_invalid_token_renders_friendly_error():
    response = _run(link_api.show_link_form("not-a-real-token"))
    assert response.status_code == 200
    assert "رابط غير صالح" in response.body.decode("utf-8")


def test_show_form_valid_token_renders_form(engine, customer_id):
    created = _run(link_api.create_link_token(customer_id))
    token = created["path"].removeprefix("/link/")

    response = _run(link_api.show_link_form(token))
    body = response.body.decode("utf-8")
    assert response.status_code == 200
    assert "gmail_address" in body
    assert "app_password" in body
    assert "consent" in body


def test_show_form_expired_token_renders_friendly_error(engine, customer_id):
    with engine.begin() as conn:
        token_hash = link_api._hash_token("expired-token-value")
        conn.execute(
            text(
                "INSERT INTO link_tokens (customer_id, token_hash, expires_at, attempts, created_at) "
                "VALUES (:cid, :hash, :expires, 0, now())"
            ),
            {"cid": customer_id, "hash": token_hash, "expires": datetime.now(timezone.utc) - timedelta(hours=1)},
        )

    response = _run(link_api.show_link_form("expired-token-value"))
    assert "انتهت الصلاحية" in response.body.decode("utf-8")


# ---------------------------------------------------------------------------
# 3. الإرسال — نجاح فعلي عبر skip_verify (DRY_RUN فقط)، رفض كلمة مرور غير
#    16 حرفًا، وتحديد المحاولات بـ5.
# ---------------------------------------------------------------------------


def test_submit_success_with_skip_verify_in_dry_run(engine, customer_id, monkeypatch):
    monkeypatch.setenv("MAIL_LIVE", "false")
    created = _run(link_api.create_link_token(customer_id))
    token = created["path"].removeprefix("/link/")

    request = _make_form_request(
        {"gmail_address": "test.career%40gmail.com", "app_password": "abcd1234abcd1234", "consent": "on"}
    )
    response = _run(link_api.submit_link_form(token, request, skip_verify=1))
    body = response.body.decode("utf-8")

    assert "تم الربط بنجاح" in body
    assert "abcd1234abcd1234" not in body  # لا كلمة مرور بأي استجابة أبدًا

    with engine.connect() as conn:
        mail_link = conn.execute(
            text("SELECT status, verified_via FROM mail_links WHERE customer_id = :cid"), {"cid": customer_id}
        ).mappings().first()
        token_row = conn.execute(
            text("SELECT used_at FROM link_tokens WHERE customer_id = :cid"), {"cid": customer_id}
        ).mappings().first()
    assert mail_link["status"] == "ok"
    assert mail_link["verified_via"] == "skipped-dry-run"
    assert token_row["used_at"] is not None


def test_submit_rejects_password_with_wrong_length(engine, customer_id, monkeypatch):
    monkeypatch.setenv("MAIL_LIVE", "false")
    created = _run(link_api.create_link_token(customer_id))
    token = created["path"].removeprefix("/link/")

    request = _make_form_request(
        {"gmail_address": "test.career%40gmail.com", "app_password": "short", "consent": "on"}
    )
    response = _run(link_api.submit_link_form(token, request, skip_verify=1))
    body = response.body.decode("utf-8")

    assert "تحقق من كلمة المرور" in body
    with engine.connect() as conn:
        exists = conn.execute(text("SELECT id FROM mail_links WHERE customer_id = :cid"), {"cid": customer_id}).first()
    assert exists is None


def test_submit_rate_limited_after_five_attempts(engine, customer_id, monkeypatch):
    monkeypatch.setenv("MAIL_LIVE", "false")
    created = _run(link_api.create_link_token(customer_id))
    token = created["path"].removeprefix("/link/")

    for _ in range(5):
        request = _make_form_request({"gmail_address": "x%40y.com", "app_password": "short", "consent": "on"})
        _run(link_api.submit_link_form(token, request, skip_verify=1))

    request = _make_form_request(
        {"gmail_address": "test.career%40gmail.com", "app_password": "abcd1234abcd1234", "consent": "on"}
    )
    response = _run(link_api.submit_link_form(token, request, skip_verify=1))
    body = response.body.decode("utf-8")
    assert "توقّفنا مؤقتًا" in body

    with engine.connect() as conn:
        exists = conn.execute(text("SELECT id FROM mail_links WHERE customer_id = :cid"), {"cid": customer_id}).first()
    assert exists is None  # المحاولة السادسة لم تُعالَج إطلاقًا
