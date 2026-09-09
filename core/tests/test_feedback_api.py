"""اختبارات core/app/feedback_api.py (B5a البند 2) — 👎 يُنشئ استبعاد شركة
(customer_company_exclusions)، 🎉 لا يستبعد شيئًا، idempotency (تكرار نفس
kind لنفس send_queue_id = 200 no-op بلا صفّ مكرّر)، وkind غير معروف → 400.

يحتاج قاعدة بيانات Postgres حقيقية — يُتخطّى تلقائيًا (skip) إن تعذّر الاتصال.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import feedback_api

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
        "لا اتصال بقاعدة بيانات Postgres محلية للاختبار — يُتخطّى test_feedback_api.py كليًا.",
        allow_module_level=True,
    )


@pytest.fixture()
def engine() -> Engine:
    return _ENGINE


@pytest.fixture(autouse=True)
def _patch_engine(monkeypatch, engine):
    monkeypatch.setattr(feedback_api, "get_engine", lambda: engine)


@pytest.fixture()
def ctx(engine: Engine):
    tag = uuid.uuid4().hex[:10]
    with engine.begin() as conn:
        company_id = conn.execute(
            text("INSERT INTO companies (name, status) VALUES (:n, 'active') RETURNING id"),
            {"n": f"Feedback Co {tag}"},
        ).scalar_one()
        source_id = conn.execute(
            text("INSERT INTO sources (company_id, source_type, source_url) VALUES (:cid, 'greenhouse', :url) RETURNING id"),
            {"cid": company_id, "url": f"https://example.invalid/feedback/{tag}"},
        ).scalar_one()
        job_id = conn.execute(
            text(
                "INSERT INTO jobs (source_id, company_id, title, dedup_key, company_name) "
                "VALUES (:sid, :cid, 'Engineer', :dk, 'Feedback Co') RETURNING id"
            ),
            {"sid": source_id, "cid": company_id, "dk": f"dedup-feedback-{tag}"},
        ).scalar_one()
        customer_id = conn.execute(
            text(
                "INSERT INTO customers (name, email_service, status, target_daily) "
                "VALUES (:n, :e, 'active', 17) RETURNING id"
            ),
            {"n": f"Feedback Customer {tag}", "e": f"feedback-{tag}@masar.invalid"},
        ).scalar_one()
        send_queue_id = conn.execute(
            text(
                """
                INSERT INTO send_queue (customer_id, job_id, to_email, subject, body_text, send_after, status, synthetic, created_at)
                VALUES (:cid, :jid, 'hr@example.invalid', 'Application', 'Body', now(), 'sent', false, now())
                RETURNING id
                """
            ),
            {"cid": customer_id, "jid": job_id},
        ).scalar_one()

    ids = {
        "tag": tag, "company_id": company_id, "source_id": source_id, "job_id": job_id,
        "customer_id": customer_id, "send_queue_id": send_queue_id,
    }
    try:
        yield ids
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM customer_company_exclusions WHERE customer_id = :cid"), {"cid": customer_id})
            conn.execute(text("DELETE FROM application_feedback WHERE customer_id = :cid"), {"cid": customer_id})
            conn.execute(text("DELETE FROM customers WHERE id = :cid"), {"cid": customer_id})
            conn.execute(text("DELETE FROM jobs WHERE id = :jid"), {"jid": job_id})
            conn.execute(text("DELETE FROM sources WHERE id = :sid"), {"sid": source_id})
            conn.execute(text("DELETE FROM companies WHERE id = :cid"), {"cid": company_id})


def _run(coro):
    return asyncio.run(coro)


def test_thumbs_down_creates_company_exclusion(engine, ctx):
    body = feedback_api.FeedbackRequest(send_queue_id=ctx["send_queue_id"], kind="thumbs_down", note="أقل من مستواي")
    result = _run(feedback_api.submit_feedback(ctx["customer_id"], body))

    assert result["ok"] is True
    assert result["already_recorded"] is False
    assert result["company_excluded_id"] == ctx["company_id"]

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT reason FROM customer_company_exclusions WHERE customer_id = :cid AND company_id = :co"),
            {"cid": ctx["customer_id"], "co": ctx["company_id"]},
        ).mappings().first()
    assert row is not None
    assert row["reason"] == "customer_feedback_thumbs_down"


def test_celebrate_does_not_exclude_company(engine, ctx):
    body = feedback_api.FeedbackRequest(send_queue_id=ctx["send_queue_id"], kind="celebrate", note=None)
    result = _run(feedback_api.submit_feedback(ctx["customer_id"], body))

    assert result["ok"] is True
    assert result["company_excluded_id"] is None

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id FROM customer_company_exclusions WHERE customer_id = :cid AND company_id = :co"),
            {"cid": ctx["customer_id"], "co": ctx["company_id"]},
        ).first()
    assert row is None


def test_repeat_same_kind_is_idempotent_no_duplicate(engine, ctx):
    body = feedback_api.FeedbackRequest(send_queue_id=ctx["send_queue_id"], kind="thumbs_down", note=None)
    first = _run(feedback_api.submit_feedback(ctx["customer_id"], body))
    second = _run(feedback_api.submit_feedback(ctx["customer_id"], body))

    assert first["already_recorded"] is False
    assert second["already_recorded"] is True

    with engine.connect() as conn:
        feedback_count = conn.execute(
            text("SELECT count(*) FROM application_feedback WHERE customer_id = :cid AND send_queue_id = :sqid"),
            {"cid": ctx["customer_id"], "sqid": ctx["send_queue_id"]},
        ).scalar_one()
        exclusion_count = conn.execute(
            text("SELECT count(*) FROM customer_company_exclusions WHERE customer_id = :cid AND company_id = :co"),
            {"cid": ctx["customer_id"], "co": ctx["company_id"]},
        ).scalar_one()
    assert feedback_count == 1
    assert exclusion_count == 1


def test_invalid_kind_returns_400(engine, ctx):
    body = feedback_api.FeedbackRequest(send_queue_id=ctx["send_queue_id"], kind="angry", note=None)
    with pytest.raises(HTTPException) as exc_info:
        _run(feedback_api.submit_feedback(ctx["customer_id"], body))
    assert exc_info.value.status_code == 400


def test_unknown_customer_returns_404(engine, ctx):
    body = feedback_api.FeedbackRequest(send_queue_id=ctx["send_queue_id"], kind="celebrate", note=None)
    with pytest.raises(HTTPException) as exc_info:
        _run(feedback_api.submit_feedback(ctx["customer_id"] + 999_999, body))
    assert exc_info.value.status_code == 404


def test_send_queue_belonging_to_different_customer_returns_404(engine, ctx):
    with engine.begin() as conn:
        other_customer_id = conn.execute(
            text(
                "INSERT INTO customers (name, email_service, status, target_daily) "
                "VALUES (:n, :e, 'active', 17) RETURNING id"
            ),
            {"n": f"Feedback Other {ctx['tag']}", "e": f"feedback-other-{ctx['tag']}@masar.invalid"},
        ).scalar_one()
    try:
        body = feedback_api.FeedbackRequest(send_queue_id=ctx["send_queue_id"], kind="celebrate", note=None)
        with pytest.raises(HTTPException) as exc_info:
            _run(feedback_api.submit_feedback(other_customer_id, body))
        assert exc_info.value.status_code == 404
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM customers WHERE id = :cid"), {"cid": other_customer_id})
