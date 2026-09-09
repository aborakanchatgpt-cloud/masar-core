"""اختبارات `core/app/reclassify.run()` (مراجعة B2b) ضد Postgres محلي حقيقي —
نفس نمط `test_sender_idempotency.py`: يُتخطّى تلقائيًا (skip، لا فشل) إن
تعذّر الاتصال، لا اختبار وهمي.

يغطي: (1) صفّ داخل النطاق بعنوان يُطابق الآن كلمة مفتاحية حقيقية لكن
family محفوظة NULL/خاطئة مسبقًا → يُصحّح، (2) صفّ out_of_scope يُحسب
"مصنّف" بمقياس family_classified_pct_in_region لكن ليس بـfamily_real_pct،
(3) صفّ خارج النطاق (out_of_region=true) لا يُلمَس إطلاقًا، (4) idempotency:
تشغيلة ثانية بلا تغيير بالمعجم بينهما → rows_changed=0.

تشغيل: cd core && TEST_DATABASE_URL=postgresql+psycopg://... python -m pytest tests/test_reclassify.py -v
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import reclassify

_REPO_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

DEFAULT_TEST_DB_URL = "postgresql+psycopg://masar_test:masar_test@localhost:5432/masar_test"


def _make_engine() -> Engine | None:
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DB_URL)
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception:  # noqa: BLE001 — أي فشل اتصال يعني: تخطّ هذا الملف كليًا
        return None


_ENGINE = _make_engine()

if _ENGINE is None:
    pytest.skip(
        "لا اتصال بقاعدة بيانات Postgres محلية للاختبار (TEST_DATABASE_URL/"
        f"{DEFAULT_TEST_DB_URL}) — يُتخطّى test_reclassify.py كليًا.",
        allow_module_level=True,
    )


@pytest.fixture()
def engine() -> Engine:
    # discovery.get_engine() يعتمد DATABASE_URL/الحالة العامة (_engine_singleton)
    # لا هذا الـfixture — نضبط DATABASE_URL لتشغيلة الاختبار حتى يستخدم
    # reclassify.run() نفس قاعدة الاختبار عبر discovery.get_engine().
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DB_URL)
    os.environ["DATABASE_URL"] = url.replace("postgresql+psycopg://", "postgresql://", 1)
    os.environ.setdefault("DATA_DIR", str(_REPO_DATA_DIR))
    import app.discovery as discovery_module

    discovery_module._engine_singleton = None
    discovery_module._schema_ensured = False
    discovery_module._families_cache = None
    discovery_module._family_patterns_cache = None
    return _ENGINE


@pytest.fixture()
def seeded(engine: Engine):
    """يُدرج شركة + مصدر + 4 صفوف jobs بحالات مختلفة، وينظّفها عند الانتهاء."""
    tag = uuid.uuid4().hex[:10]
    with engine.begin() as conn:
        company_id = conn.execute(
            text("INSERT INTO companies (name, status) VALUES (:n, 'active') RETURNING id"),
            {"n": f"Reclassify Test Co {tag}"},
        ).scalar_one()
        source_id = conn.execute(
            text(
                """
                INSERT INTO sources (company_id, source_type, source_url, enabled)
                VALUES (:c, 'greenhouse', :u, true) RETURNING id
                """
            ),
            {"c": company_id, "u": f"https://example.test/{tag}"},
        ).scalar_one()

        job_ids: dict[str, int] = {}

        # (1) داخل النطاق، عنوان يُطابق كلمة مفتاحية حقيقية (out_of_scope)،
        # لكن family محفوظة NULL مسبقًا (تحاكي صفًا لم يُعاد حسابه منذ
        # توسّع المعجم) — reclassify.run() يجب أن يُصحّحها.
        job_ids["stale_out_of_scope"] = conn.execute(
            text(
                """
                INSERT INTO jobs (source_id, company_id, title, dedup_key, out_of_region, family, description_snippet)
                VALUES (:s, :c, 'Sales Manager', :dk, false, NULL, '')
                RETURNING id
                """
            ),
            {"s": source_id, "c": company_id, "dk": f"dk-stale-out-{tag}"},
        ).scalar_one()

        # (2) داخل النطاق، عنوان يُطابق عائلة حقيقية (construction_pm)، family
        # محفوظة NULL مسبقًا.
        job_ids["stale_real"] = conn.execute(
            text(
                """
                INSERT INTO jobs (source_id, company_id, title, dedup_key, out_of_region, family, description_snippet)
                VALUES (:s, :c, 'Senior Project Manager', :dk, false, NULL, '')
                RETURNING id
                """
            ),
            {"s": source_id, "c": company_id, "dk": f"dk-stale-real-{tag}"},
        ).scalar_one()

        # (3) خارج النطاق (out_of_region=true) — يجب ألا يُلمَس إطلاقًا رغم أن
        # العنوان يُطابق أيضًا.
        job_ids["out_of_region"] = conn.execute(
            text(
                """
                INSERT INTO jobs (source_id, company_id, title, dedup_key, out_of_region, family, description_snippet)
                VALUES (:s, :c, 'Sales Manager', :dk, true, NULL, '')
                RETURNING id
                """
            ),
            {"s": source_id, "c": company_id, "dk": f"dk-outregion-{tag}"},
        ).scalar_one()

        # (4) داخل النطاق، لا يُطابق أي شيء (يبقى NULL/غير مصنّف فعليًا).
        job_ids["truly_unclassified"] = conn.execute(
            text(
                """
                INSERT INTO jobs (source_id, company_id, title, dedup_key, out_of_region, family, description_snippet)
                VALUES (:s, :c, 'Zzqx Freelance Notion Expert Gig', :dk, false, NULL, '')
                RETURNING id
                """
            ),
            {"s": source_id, "c": company_id, "dk": f"dk-unclassified-{tag}"},
        ).scalar_one()

    yield job_ids

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM jobs WHERE company_id = :c"), {"c": company_id})
        conn.execute(text("DELETE FROM sources WHERE id = :s"), {"s": source_id})
        conn.execute(text("DELETE FROM companies WHERE id = :c"), {"c": company_id})


def test_reclassify_corrects_stale_family_and_skips_out_of_region(engine: Engine, seeded: dict[str, int]) -> None:
    summary = reclassify.run()

    assert summary["rows_changed"] >= 2  # على الأقل الصفّان (1) و(2) تغيّرا

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT family FROM jobs WHERE id = :id"), {"id": seeded["stale_out_of_scope"]}
        ).scalar_one()
        assert row == "out_of_scope"

        row = conn.execute(
            text("SELECT family FROM jobs WHERE id = :id"), {"id": seeded["stale_real"]}
        ).scalar_one()
        assert row == "construction_pm"

        # الصفّ خارج النطاق يبقى NULL كما كان — لم يُلمَس إطلاقًا (لا حتى
        # قراءةً ضمن دفعات reclassify، التي تُقيّد بـout_of_region=false).
        row = conn.execute(
            text("SELECT family FROM jobs WHERE id = :id"), {"id": seeded["out_of_region"]}
        ).scalar_one()
        assert row is None


def test_reclassify_is_idempotent(engine: Engine, seeded: dict[str, int]) -> None:
    first = reclassify.run()
    second = reclassify.run()

    assert second["rows_changed"] == 0
    assert second["jobs_in_region_total"] == first["jobs_in_region_total"]
    assert second["classified_real"] == first["classified_real"]
    assert second["out_of_scope"] == first["out_of_scope"]


def test_reclassify_pct_definitions(engine: Engine, seeded: dict[str, int]) -> None:
    """out_of_scope يُحسب ضمن family_classified_pct_in_region لكن ليس ضمن
    family_real_pct_in_region (مراجعة B2b — تعريف discovery_api.py:stats)."""
    summary = reclassify.run()

    expected_classified = (summary["classified_real"] + summary["out_of_scope"]) / summary["jobs_in_region_total"]
    expected_real = summary["classified_real"] / summary["jobs_in_region_total"]
    assert summary["family_classified_pct_in_region"] == round(expected_classified, 4)
    assert summary["family_real_pct_in_region"] == round(expected_real, 4)
    assert summary["family_classified_pct_in_region"] >= summary["family_real_pct_in_region"]
