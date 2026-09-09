"""اختبارات core/app/guarantee.py (B5a البند 3) — قواعد الضمان الثابتة:
استبعاد المرتد من المُحتسَب، تمديد يومين تلقائي عند العجز، تعويض تناسبي بعد
استهلاك التمديد، رصيد/باقات (credits) بلا اشتراك = 'na'، وانقطاع بريد العميل
يُمدّد الفترة بدل التعويض (لا يصل التقييم لمرحلة التعويض إطلاقًا طالما
البريد معطوب).

يحتاج قاعدة بيانات Postgres حقيقية — يُتخطّى تلقائيًا (skip) إن تعذّر الاتصال.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import guarantee

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
        "لا اتصال بقاعدة بيانات Postgres محلية للاختبار — يُتخطّى test_guarantee.py كليًا.",
        allow_module_level=True,
    )


@pytest.fixture()
def engine() -> Engine:
    return _ENGINE


def _make_customer(engine: Engine, *, price_sar=None, tag: str | None = None) -> int:
    tag = tag or uuid.uuid4().hex[:10]
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO customers (name, email_service, status, target_daily, price_sar) "
                "VALUES (:n, :e, 'active', 17, :price) RETURNING id"
            ),
            {"n": f"Guarantee Test {tag}", "e": f"guarantee-{tag}@masar.invalid", "price": price_sar},
        ).scalar_one()


def _make_subscription(engine: Engine, customer_id: int, *, starts_at: datetime, ends_at: datetime, status: str = "active") -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO subscriptions (customer_id, product_code, starts_at, ends_at, daily_target, status)
                VALUES (:cid, 'SUB30', :starts, :ends, 17, :status) RETURNING id
                """
            ),
            {"cid": customer_id, "starts": starts_at, "ends": ends_at, "status": status},
        ).scalar_one()


def _make_job_and_company(engine: Engine, tag: str) -> tuple[int, int]:
    with engine.begin() as conn:
        company_id = conn.execute(
            text("INSERT INTO companies (name, status) VALUES (:n, 'active') RETURNING id"),
            {"n": f"Guarantee Co {tag}"},
        ).scalar_one()
        source_id = conn.execute(
            text("INSERT INTO sources (company_id, source_type, source_url) VALUES (:cid, 'greenhouse', :url) RETURNING id"),
            {"cid": company_id, "url": f"https://example.invalid/guarantee/{tag}"},
        ).scalar_one()
        job_id = conn.execute(
            text(
                "INSERT INTO jobs (source_id, company_id, title, dedup_key, company_name) "
                "VALUES (:sid, :cid, 'Engineer', :dk, 'Guarantee Co') RETURNING id"
            ),
            {"sid": source_id, "cid": company_id, "dk": f"dedup-guarantee-{tag}"},
        ).scalar_one()
    return job_id, company_id


def _insert_applications(engine: Engine, customer_id: int, job_id: int, *, sent_count: int, bounced_count: int, within: datetime) -> None:
    with engine.begin() as conn:
        for i in range(sent_count):
            conn.execute(
                text(
                    "INSERT INTO applications (customer_id, job_id, sent_at, message_id, status, created_at) "
                    "VALUES (:cid, :jid, :sent_at, :mid, 'sent', now())"
                ),
                {"cid": customer_id, "jid": job_id, "sent_at": within, "mid": f"<{uuid.uuid4().hex}@masar.local>"},
            )
        for i in range(bounced_count):
            conn.execute(
                text(
                    "INSERT INTO applications (customer_id, job_id, sent_at, message_id, status, created_at) "
                    "VALUES (:cid, :jid, :sent_at, :mid, 'bounced', now())"
                ),
                {"cid": customer_id, "jid": job_id, "sent_at": within, "mid": f"<{uuid.uuid4().hex}@masar.local>"},
            )


@pytest.fixture()
def cleanup(engine: Engine):
    created_customers: list[int] = []
    created_jobs_companies: list[tuple[int, int]] = []
    yield created_customers, created_jobs_companies
    with engine.begin() as conn:
        if created_customers:
            conn.execute(text("DELETE FROM guarantee_ledger WHERE customer_id = ANY(:ids)"), {"ids": created_customers})
            conn.execute(text("DELETE FROM applications WHERE customer_id = ANY(:ids)"), {"ids": created_customers})
            conn.execute(text("DELETE FROM subscriptions WHERE customer_id = ANY(:ids)"), {"ids": created_customers})
            conn.execute(text("DELETE FROM mail_links WHERE customer_id = ANY(:ids)"), {"ids": created_customers})
            conn.execute(text("DELETE FROM customers WHERE id = ANY(:ids)"), {"ids": created_customers})
        for job_id, company_id in created_jobs_companies:
            conn.execute(text("DELETE FROM jobs WHERE id = :jid"), {"jid": job_id})
            conn.execute(text("DELETE FROM sources WHERE company_id = :cid"), {"cid": company_id})
            conn.execute(text("DELETE FROM companies WHERE id = :cid"), {"cid": company_id})


# ---------------------------------------------------------------------------
# 1. الهدف مُبلّغ فعليًا → 'computed'، بلا تعويض.
# ---------------------------------------------------------------------------


def test_target_met_marks_computed_no_refund(engine, cleanup):
    created_customers, created_jobs = cleanup
    tag = uuid.uuid4().hex[:10]
    customer_id = _make_customer(engine, price_sar=90.0, tag=tag)
    created_customers.append(customer_id)
    job_id, company_id = _make_job_and_company(engine, tag)
    created_jobs.append((job_id, company_id))

    now = datetime.now(timezone.utc)
    starts = now - timedelta(days=31)
    ends = now - timedelta(hours=1)
    _make_subscription(engine, customer_id, starts_at=starts, ends_at=ends)
    _insert_applications(engine, customer_id, job_id, sent_count=guarantee.MONTHLY_TARGET, bounced_count=0, within=starts + timedelta(days=1))

    result = guarantee.evaluate_period(customer_id, engine=engine, now=now)
    assert result["status"] == "computed"
    assert result["shortfall"] == 0
    assert result["refund_amount"] is None


# ---------------------------------------------------------------------------
# 2. المرتد لا يُحتسب أبدًا (bounce exclusion) — عجز رغم عدد صفوف applications
#    الكلي مساوٍ للهدف، لأن نصفها مرتدّ.
# ---------------------------------------------------------------------------


def test_bounced_applications_excluded_from_counted_sent(engine, cleanup):
    created_customers, created_jobs = cleanup
    tag = uuid.uuid4().hex[:10]
    customer_id = _make_customer(engine, price_sar=90.0, tag=tag)
    created_customers.append(customer_id)
    job_id, company_id = _make_job_and_company(engine, tag)
    created_jobs.append((job_id, company_id))

    now = datetime.now(timezone.utc)
    starts = now - timedelta(days=31)
    ends = now - timedelta(hours=1)
    _make_subscription(engine, customer_id, starts_at=starts, ends_at=ends)
    _insert_applications(
        engine, customer_id, job_id,
        sent_count=guarantee.MONTHLY_TARGET - 10, bounced_count=10, within=starts + timedelta(days=1),
    )

    result = guarantee.evaluate_period(customer_id, engine=engine, now=now)
    assert result["counted_sent"] == guarantee.MONTHLY_TARGET - 10
    assert result["bounced"] == 10
    assert result["status"] == "extended"  # عجز → تمديد أولًا، لا تعويض فورًا


# ---------------------------------------------------------------------------
# 3. عجز أول مرة (بريد سليم) → تمديد يومين تلقائي، لا تعويض بعد.
# ---------------------------------------------------------------------------


def test_first_shortfall_grants_two_day_grace_extension(engine, cleanup):
    created_customers, created_jobs = cleanup
    tag = uuid.uuid4().hex[:10]
    customer_id = _make_customer(engine, price_sar=90.0, tag=tag)
    created_customers.append(customer_id)
    job_id, company_id = _make_job_and_company(engine, tag)
    created_jobs.append((job_id, company_id))

    now = datetime.now(timezone.utc)
    starts = now - timedelta(days=31)
    ends = now - timedelta(hours=1)
    sub_id = _make_subscription(engine, customer_id, starts_at=starts, ends_at=ends)
    _insert_applications(engine, customer_id, job_id, sent_count=400, bounced_count=0, within=starts + timedelta(days=1))

    result = guarantee.evaluate_period(customer_id, engine=engine, now=now)
    assert result["status"] == "extended"
    assert result["extension_days"] == guarantee.GRACE_EXTENSION_DAYS
    assert result["refund_amount"] is None

    with engine.connect() as conn:
        new_ends = conn.execute(text("SELECT ends_at, status FROM subscriptions WHERE id = :id"), {"id": sub_id}).mappings().first()
    assert new_ends["status"] == "extended"
    assert new_ends["ends_at"] == ends + timedelta(days=guarantee.GRACE_EXTENSION_DAYS)


# ---------------------------------------------------------------------------
# 4. لا يزال قاصرًا بعد التمديد (بريد سليم) → تعويض تناسبي (سعر معروف).
# ---------------------------------------------------------------------------


def test_still_short_after_grace_computes_proportional_refund(engine, cleanup):
    created_customers, created_jobs = cleanup
    tag = uuid.uuid4().hex[:10]
    price = 90.0
    customer_id = _make_customer(engine, price_sar=price, tag=tag)
    created_customers.append(customer_id)
    job_id, company_id = _make_job_and_company(engine, tag)
    created_jobs.append((job_id, company_id))

    now = datetime.now(timezone.utc)
    starts = now - timedelta(days=35)
    ends = now - timedelta(hours=1)
    counted = 400
    _make_subscription(engine, customer_id, starts_at=starts, ends_at=ends)
    _insert_applications(engine, customer_id, job_id, sent_count=counted, bounced_count=0, within=starts + timedelta(days=1))

    # التقييم الأول يمنح التمديد (extension_days=2)، لا تعويض بعد.
    first = guarantee.evaluate_period(customer_id, engine=engine, now=now)
    assert first["status"] == "extended"

    # التقييم الثاني (بعد أن انتهت فترة التمديد فعليًا) — لا تقديمات جديدة،
    # لا يزال قاصرًا بنفس العدّ، والتمديد استُهلِك أصلًا → تعويض.
    second = guarantee.evaluate_period(customer_id, engine=engine, now=now + timedelta(days=3))
    assert second["status"] == "refund_pending"
    assert second["shortfall"] == guarantee.MONTHLY_TARGET - counted
    expected_refund = round((guarantee.MONTHLY_TARGET - counted) * price / guarantee.MONTHLY_TARGET, 2)
    assert float(second["refund_amount"]) == expected_refund


# ---------------------------------------------------------------------------
# 5. نفس حالة العجز بعد التمديد لكن **بلا** customers.price_sar محدّد →
#    refund_amount=NULL، status='refund_pending' (ينتظر المالك يملأ السعر).
# ---------------------------------------------------------------------------


def test_refund_pending_with_null_price_when_owner_has_not_set_price(engine, cleanup):
    created_customers, created_jobs = cleanup
    tag = uuid.uuid4().hex[:10]
    customer_id = _make_customer(engine, price_sar=None, tag=tag)
    created_customers.append(customer_id)
    job_id, company_id = _make_job_and_company(engine, tag)
    created_jobs.append((job_id, company_id))

    now = datetime.now(timezone.utc)
    starts = now - timedelta(days=35)
    ends = now - timedelta(hours=1)
    _make_subscription(engine, customer_id, starts_at=starts, ends_at=ends)
    _insert_applications(engine, customer_id, job_id, sent_count=300, bounced_count=0, within=starts + timedelta(days=1))

    guarantee.evaluate_period(customer_id, engine=engine, now=now)  # يمنح التمديد
    second = guarantee.evaluate_period(customer_id, engine=engine, now=now + timedelta(days=3))

    assert second["status"] == "refund_pending"
    assert second["refund_amount"] is None


# ---------------------------------------------------------------------------
# 6. عميل رصيد (بلا أي اشتراك) → 'na'.
# ---------------------------------------------------------------------------


def test_customer_without_subscription_is_na(engine, cleanup):
    created_customers, _ = cleanup
    customer_id = _make_customer(engine, price_sar=None)
    created_customers.append(customer_id)

    result = guarantee.evaluate_period(customer_id, engine=engine)
    assert result["status"] == "na"


# ---------------------------------------------------------------------------
# 7. انقطاع بسبب بريد العميل (mail_links.status != 'ok') → تمديد بدل تعويض،
#    حتى لو كان التمديد العادي (2 يوم) قد استُهلِك أصلًا من قبل.
# ---------------------------------------------------------------------------


def test_customer_caused_mail_outage_extends_instead_of_refund(engine, cleanup):
    created_customers, created_jobs = cleanup
    tag = uuid.uuid4().hex[:10]
    customer_id = _make_customer(engine, price_sar=90.0, tag=tag)
    created_customers.append(customer_id)
    job_id, company_id = _make_job_and_company(engine, tag)
    created_jobs.append((job_id, company_id))

    now = datetime.now(timezone.utc)
    starts = now - timedelta(days=35)
    ends = now - timedelta(hours=1)
    _make_subscription(engine, customer_id, starts_at=starts, ends_at=ends)
    _insert_applications(engine, customer_id, job_id, sent_count=300, bounced_count=0, within=starts + timedelta(days=1))

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO mail_links (customer_id, address, secret_enc, status, last_error, created_at) "
                "VALUES (:cid, :addr, 'enc', 'failed', 'auth failed', now())"
            ),
            {"cid": customer_id, "addr": f"broken-{tag}@masar.invalid"},
        )

    # حتى بعد عدّة تقييمات (تجاوزت مدة التمديد العادي) — يبقى 'extended'،
    # لا يتحوّل أبدًا لـ'refund_pending' طالما البريد معطوب.
    guarantee.evaluate_period(customer_id, engine=engine, now=now)
    result = guarantee.evaluate_period(customer_id, engine=engine, now=now + timedelta(days=10))

    assert result["status"] == "extended"
    assert result["reason"] == "mail_link_broken"
