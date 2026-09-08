"""اختبار F1 بمراجعة B3B4-live-review.md (استنتاج عالٍ 1): `create_mail_link`
كان يفرض اختبار SMTP+IMAP حقيقيين بصرف النظر عن DRY_RUN — يمنع اختبار المسار
الكامل ببيئة بلا حساب Gmail اختباري حقيقي (mailpit لا يوفّر IMAP إطلاقًا).
هذا الملف يتحقق من `skip_verify`:

    1. `skip_verify=true` مقبول ويضبط status='ok' مباشرة حين DRY_RUN فعّال
       (MAIL_LIVE ليست true) — بلا أي اتصال SMTP/IMAP حقيقي.
    2. `skip_verify=true` مرفوض بـ400 خارج DRY_RUN (MAIL_LIVE=true).
    3. المسار الافتراضي (skip_verify=false، غير مُمَرّر) لم يتغيّر: لا يزال
       يستدعي _test_smtp/_test_imap فعليًا.

يحتاج هذا الاختبار قاعدة بيانات Postgres حقيقية (نفس نمط
test_sender_idempotency.py) — يُتخطّى تلقائيًا (skip، لا فشل) إن تعذّر
الاتصال. الإعداد الافتراضي:
`postgresql+psycopg://masar_test:masar_test@localhost:5432/masar_test`
(قابل للتجاوز بـTEST_DATABASE_URL). يحتاج أيضًا MAIL_FERNET_KEY صالحًا
بالبيئة (mail_crypto.encrypt_secret) — يُتخطّى الملف أيضًا إن غاب."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import mail_api

DEFAULT_TEST_DB_URL = "postgresql+psycopg://masar_test:masar_test@localhost:5432/masar_test"


def _make_engine() -> Engine | None:
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DB_URL)
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception:  # noqa: BLE001 — أي فشل اتصال يعني: تخطَّ هذا الملف كليًا
        return None


_ENGINE = _make_engine()

if _ENGINE is None:
    pytest.skip(
        "لا اتصال بقاعدة بيانات Postgres محلية للاختبار (TEST_DATABASE_URL/"
        f"{DEFAULT_TEST_DB_URL}) — يُتخطّى test_mail_link_skip_verify.py كليًا.",
        allow_module_level=True,
    )


if not os.environ.get("MAIL_FERNET_KEY"):
    os.environ["MAIL_FERNET_KEY"] = Fernet.generate_key().decode("ascii")


@pytest.fixture()
def engine() -> Engine:
    return _ENGINE


@pytest.fixture(autouse=True)
def _patch_engine(monkeypatch, engine):
    # mail_api.py يستدعي get_engine() (المستوردة من app.discovery) في كل
    # نقطة نهاية — تمويهها هنا يعيد استخدام نفس اتصال الاختبار بدل محاولة
    # إنشاء اتصال جديد بإعدادات الإنتاج (DATABASE_URL قد لا تكون معرّفة هنا).
    monkeypatch.setattr(mail_api, "get_engine", lambda: engine)


@pytest.fixture()
def customer_id(engine):
    tag = uuid.uuid4().hex[:10]
    with engine.begin() as conn:
        cid = conn.execute(
            text(
                "INSERT INTO customers (name, email_service, status, target_daily) "
                "VALUES (:n, :e, 'active', 17) RETURNING id"
            ),
            {"n": f"Skip Verify Test {tag}", "e": f"skipverify-{tag}@masar.invalid"},
        ).scalar_one()
    yield cid
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM customers WHERE id = :cid"), {"cid": cid})


def _body(customer_id: int, *, skip_verify: bool = False) -> mail_api.MailLinkCreateRequest:
    return mail_api.MailLinkCreateRequest(
        customer_id=customer_id,
        address="skip-verify-test@example.invalid",
        app_password="fake-app-password-not-real",
        skip_verify=skip_verify,
    )


# ---------------------------------------------------------------------------
# 1. skip_verify مقبول بوضع DRY_RUN (MAIL_LIVE ليست true).
# ---------------------------------------------------------------------------


def test_skip_verify_accepted_in_dry_run_sets_status_ok(monkeypatch, customer_id):
    monkeypatch.setenv("MAIL_LIVE", "false")

    def _boom(*a, **k):  # pragma: no cover
        raise AssertionError("لا يجب استدعاء اختبار SMTP/IMAP الحقيقي مع skip_verify=true")

    monkeypatch.setattr(mail_api, "_test_smtp", _boom)
    monkeypatch.setattr(mail_api, "_test_imap", _boom)

    result = asyncio.run(mail_api.create_mail_link(_body(customer_id, skip_verify=True)))

    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["error"] is None
    assert result["verified_via"] == "skipped-dry-run"

    with _ENGINE.connect() as conn:
        row = conn.execute(
            text("SELECT status, verified_via, last_error FROM mail_links WHERE customer_id = :id"),
            {"id": customer_id},
        ).mappings().first()
    assert row["status"] == "ok"
    assert row["verified_via"] == "skipped-dry-run"
    assert row["last_error"] is None


# ---------------------------------------------------------------------------
# 2. skip_verify مرفوض بـ400 خارج DRY_RUN (MAIL_LIVE=true).
# ---------------------------------------------------------------------------


def test_skip_verify_rejected_outside_dry_run(monkeypatch, customer_id):
    monkeypatch.setenv("MAIL_LIVE", "true")

    def _boom(*a, **k):  # pragma: no cover
        raise AssertionError("لا يجب الوصول لاختبار SMTP/IMAP — يجب الرفض قبله بـ400")

    monkeypatch.setattr(mail_api, "_test_smtp", _boom)
    monkeypatch.setattr(mail_api, "_test_imap", _boom)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(mail_api.create_mail_link(_body(customer_id, skip_verify=True)))

    assert exc_info.value.status_code == 400

    with _ENGINE.connect() as conn:
        row = conn.execute(
            text("SELECT id FROM mail_links WHERE customer_id = :id"), {"id": customer_id}
        ).first()
    assert row is None  # لم يُدرَج أي صفّ — الرفض حدث قبل أي كتابة بقاعدة البيانات


# ---------------------------------------------------------------------------
# 3. المسار الافتراضي (skip_verify=false) لم يتغيّر: يستدعي الاختبار الحقيقي.
# ---------------------------------------------------------------------------


def test_default_path_without_skip_verify_still_calls_real_smtp_imap_test(monkeypatch, customer_id):
    monkeypatch.setenv("MAIL_LIVE", "false")
    calls = {"smtp": 0, "imap": 0}

    def _fake_smtp(host, port, address, password):
        calls["smtp"] += 1
        return "فشل تسجيل الدخول SMTP — تحقق من العنوان/كلمة مرور التطبيق"

    def _fake_imap(host, port, address, password):  # pragma: no cover
        calls["imap"] += 1
        return None

    monkeypatch.setattr(mail_api, "_test_smtp", _fake_smtp)
    monkeypatch.setattr(mail_api, "_test_imap", _fake_imap)

    result = asyncio.run(mail_api.create_mail_link(_body(customer_id, skip_verify=False)))

    assert calls["smtp"] == 1
    assert calls["imap"] == 0  # smtp فشل فيُتخطّى imap (نفس المنطق الأصلي)
    assert result["status"] == "failed"
    assert result["verified_via"] is None

    with _ENGINE.connect() as conn:
        row = conn.execute(
            text("SELECT status, verified_via FROM mail_links WHERE customer_id = :id"),
            {"id": customer_id},
        ).mappings().first()
    assert row["status"] == "failed"
    assert row["verified_via"] is None


def test_default_path_success_sets_status_ok_and_null_verified_via(monkeypatch, customer_id):
    monkeypatch.setenv("MAIL_LIVE", "false")
    monkeypatch.setattr(mail_api, "_test_smtp", lambda *a, **k: None)
    monkeypatch.setattr(mail_api, "_test_imap", lambda *a, **k: None)

    result = asyncio.run(mail_api.create_mail_link(_body(customer_id, skip_verify=False)))

    assert result["status"] == "ok"
    assert result["verified_via"] is None
