"""اختبار حي (Postgres حقيقي) لتصحيح B5a البند 2 على core/app/send_builder.py:
`_fetch_candidate_opportunities` يجب أن يُستبعد فورًا أي فرصة (opportunity)
لشركة موجودة بـcustomer_company_exclusions لهذا العميل تحديدًا، حتى لو
كانت الفرصة مخطّطة أصلًا (status='planned') قبل تسجيل الاستبعاد — بلا لمس
opportunities.status نفسه (يبقى 'planned' بصمت، فقط لا يدخل طابور الإرسال).

يحتاج قاعدة بيانات Postgres حقيقية — يُتخطّى تلقائيًا (skip) إن تعذّر الاتصال.
"""
from __future__ import annotations

import os
import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import send_builder

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
        "لا اتصال بقاعدة بيانات Postgres محلية للاختبار — يُتخطّى test_send_builder_exclusion.py كليًا.",
        allow_module_level=True,
    )


@pytest.fixture()
def engine() -> Engine:
    return _ENGINE


@pytest.fixture()
def ctx(engine: Engine):
    tag = uuid.uuid4().hex[:10]
    with engine.begin() as conn:
        customer_id = conn.execute(
            text(
                "INSERT INTO customers (name, email_service, status, target_daily) "
                "VALUES (:n, :e, 'active', 17) RETURNING id"
            ),
            {"n": f"Exclusion Test {tag}", "e": f"exclusion-{tag}@masar.invalid"},
        ).scalar_one()

        allowed_company_id = conn.execute(
            text("INSERT INTO companies (name, status) VALUES (:n, 'active') RETURNING id"),
            {"n": f"Allowed Co {tag}"},
        ).scalar_one()
        excluded_company_id = conn.execute(
            text("INSERT INTO companies (name, status) VALUES (:n, 'active') RETURNING id"),
            {"n": f"Excluded Co {tag}"},
        ).scalar_one()

        source_id = conn.execute(
            text("INSERT INTO sources (company_id, source_type, source_url) VALUES (:cid, 'greenhouse', :url) RETURNING id"),
            {"cid": allowed_company_id, "url": f"https://example.invalid/exclusion/{tag}"},
        ).scalar_one()

        allowed_job_id = conn.execute(
            text(
                "INSERT INTO jobs (source_id, company_id, title, dedup_key, company_name) "
                "VALUES (:sid, :cid, 'Engineer', :dk, :cn) RETURNING id"
            ),
            {"sid": source_id, "cid": allowed_company_id, "dk": f"dedup-allowed-{tag}", "cn": f"Allowed Co {tag}"},
        ).scalar_one()
        excluded_job_id = conn.execute(
            text(
                "INSERT INTO jobs (source_id, company_id, title, dedup_key, company_name) "
                "VALUES (:sid, :cid, 'Engineer', :dk, :cn) RETURNING id"
            ),
            {"sid": source_id, "cid": excluded_company_id, "dk": f"dedup-excluded-{tag}", "cn": f"Excluded Co {tag}"},
        ).scalar_one()

        allowed_opp_id = conn.execute(
            text(
                "INSERT INTO opportunities (customer_id, job_id, score, tier, planned_for, status) "
                "VALUES (:cid, :jid, 0.9, 'A', CURRENT_DATE, 'planned') RETURNING id"
            ),
            {"cid": customer_id, "jid": allowed_job_id},
        ).scalar_one()
        excluded_opp_id = conn.execute(
            text(
                "INSERT INTO opportunities (customer_id, job_id, score, tier, planned_for, status) "
                "VALUES (:cid, :jid, 0.9, 'A', CURRENT_DATE, 'planned') RETURNING id"
            ),
            {"cid": customer_id, "jid": excluded_job_id},
        ).scalar_one()

        conn.execute(
            text(
                "INSERT INTO customer_company_exclusions (customer_id, company_id, reason, created_at) "
                "VALUES (:cid, :co, 'test', now())"
            ),
            {"cid": customer_id, "co": excluded_company_id},
        )

    ids = {
        "tag": tag, "customer_id": customer_id,
        "allowed_company_id": allowed_company_id, "excluded_company_id": excluded_company_id,
        "source_id": source_id,
        "allowed_job_id": allowed_job_id, "excluded_job_id": excluded_job_id,
        "allowed_opp_id": allowed_opp_id, "excluded_opp_id": excluded_opp_id,
    }
    try:
        yield ids
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM customer_company_exclusions WHERE customer_id = :cid"), {"cid": customer_id})
            conn.execute(text("DELETE FROM customers WHERE id = :cid"), {"cid": customer_id})
            conn.execute(text("DELETE FROM jobs WHERE id = ANY(:ids)"), {"ids": [allowed_job_id, excluded_job_id]})
            conn.execute(text("DELETE FROM sources WHERE id = :sid"), {"sid": source_id})
            conn.execute(text("DELETE FROM companies WHERE id = ANY(:ids)"), {"ids": [allowed_company_id, excluded_company_id]})


def test_excluded_company_opportunity_never_becomes_a_candidate(engine, ctx):
    with engine.connect() as conn:
        candidates = send_builder._fetch_candidate_opportunities(conn, ctx["customer_id"], date.today())

    candidate_job_ids = {c["job_id"] for c in candidates}
    assert ctx["allowed_job_id"] in candidate_job_ids
    assert ctx["excluded_job_id"] not in candidate_job_ids

    # opportunities.status نفسه لم يُلمَس (يبقى 'planned' بصمت — القرار
    # موثّق صراحةً بتعليق send_builder.py: التبديل مسؤولية planner.py فقط).
    with engine.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM opportunities WHERE id = :id"), {"id": ctx["excluded_opp_id"]}
        ).scalar_one()
    assert status == "planned"
