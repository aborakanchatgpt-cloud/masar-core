"""اختبار core/app/overview_api.py (B5a البند 4) — يتحقق أن كل الأقسام
المطلوبة حاضرة بالاستجابة وأن الأعداد تعكس بيانات حقيقية أُدرجت للاختبار
(لا قيم وهمية ثابتة).

يحتاج قاعدة بيانات Postgres حقيقية — يُتخطّى تلقائيًا (skip) إن تعذّر الاتصال.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import overview_api

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
        "لا اتصال بقاعدة بيانات Postgres محلية للاختبار — يُتخطّى test_overview_api.py كليًا.",
        allow_module_level=True,
    )


@pytest.fixture()
def engine() -> Engine:
    return _ENGINE


@pytest.fixture(autouse=True)
def _patch_engine(monkeypatch, engine):
    monkeypatch.setattr(overview_api, "get_engine", lambda: engine)


def test_overview_has_all_required_sections(engine, monkeypatch):
    monkeypatch.setenv("MAIL_LIVE", "false")
    result = asyncio.run(overview_api.overview())

    for key in (
        "customers_by_status",
        "sends_today",
        "sends_last_7_days",
        "bounces_today",
        "send_queue_by_status",
        "mail_links_by_status",
        "pending_reports",
        "pending_guarantees",
        "discovery_freshness",
        "dry_run",
    ):
        assert key in result

    assert isinstance(result["customers_by_status"], dict)
    assert isinstance(result["sends_today"], int)
    assert result["dry_run"] is True  # MAIL_LIVE=false → DRY_RUN فعّال


def test_overview_counts_reflect_real_data(engine):
    tag = uuid.uuid4().hex[:10]
    before = asyncio.run(overview_api.overview())
    before_active = before["customers_by_status"].get("active", 0)

    with engine.begin() as conn:
        customer_id = conn.execute(
            text(
                "INSERT INTO customers (name, email_service, status, target_daily) "
                "VALUES (:n, :e, 'active', 17) RETURNING id"
            ),
            {"n": f"Overview Test {tag}", "e": f"overview-{tag}@masar.invalid"},
        ).scalar_one()

    try:
        after = asyncio.run(overview_api.overview())
        after_active = after["customers_by_status"].get("active", 0)
        assert after_active == before_active + 1  # الاستعلام يعكس الإدراج الجديد فورًا
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM customers WHERE id = :cid"), {"cid": customer_id})
