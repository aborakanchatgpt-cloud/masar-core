"""اختبارات B7: core/app/catalog.py (`GET /catalog`, `POST /admin/orders`,
`GET /admin/orders`) — يبني فوق جداول products/orders/wallets/ledger/
subscriptions الموجودة منذ B3 (migration 0004)، موسّعة بـ0012_b7_catalog.

يحتاج قاعدة بيانات Postgres حقيقية — يُتخطّى تلقائيًا (skip) إن تعذّر الاتصال
(نفس نمط test_customers_api.py).
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import catalog

DEFAULT_TEST_DB_URL = "postgresql+psycopg://masar_test:masar_test@localhost:5432/masar_test"


def _make_engine() -> Engine | None:
    import os

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
        "لا اتصال بقاعدة بيانات Postgres محلية للاختبار — يُتخطّى test_catalog.py كليًا.",
        allow_module_level=True,
    )


@pytest.fixture()
def engine() -> Engine:
    return _ENGINE


@pytest.fixture(autouse=True)
def _patch_engine(monkeypatch, engine):
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)


@pytest.fixture()
def customer_id(engine: Engine):
    tag = uuid.uuid4().hex[:10]
    with engine.begin() as conn:
        cid = conn.execute(
            text(
                "INSERT INTO customers (name, email_service, status, target_daily) "
                "VALUES (:n, :e, 'active', 17) RETURNING id"
            ),
            {"n": f"Catalog Test {tag}", "e": f"catalog-{tag}@masar.invalid"},
        ).scalar_one()
    yield cid
    with engine.begin() as conn:
        # orders/ledger بلا ondelete=CASCADE عمدًا (migration 0004) —
        # تُحذف يدويًا قبل حذف العميل، بعكس wallets/subscriptions/
        # customer_status_audit (CASCADE تلقائي).
        conn.execute(text("DELETE FROM orders WHERE customer_id = :cid"), {"cid": cid})
        conn.execute(text("DELETE FROM ledger WHERE customer_id = :cid"), {"cid": cid})
        conn.execute(text("DELETE FROM customers WHERE id = :cid"), {"cid": cid})


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# GET /catalog
# ---------------------------------------------------------------------------


def test_catalog_lists_active_products_with_null_or_placeholder_prices(engine):
    result = _run(catalog.list_catalog())
    assert result["count"] >= 5
    codes = {item["code"] for item in result["items"]}
    assert {"SUB30", "CR100", "CR300", "CR510", "CV1"} <= codes

    by_code = {item["code"]: item for item in result["items"]}
    sub30 = by_code["SUB30"]
    assert sub30["kind"] == "subscription"
    assert sub30["applications_included"] == 510
    assert sub30["days"] == 30
    # B7: القاعدة غير القابلة للتفاوض — الأسعار مؤقتة حتى يقرر أحمد
    assert sub30["price_sar"] is None
    assert sub30["price_label_ar"] == catalog.PRICE_TBD_LABEL_AR

    cv1 = by_code["CV1"]
    assert cv1["kind"] == "cv_standalone" or cv1["kind"] == "standalone_cv"
    assert cv1["price_sar"] is None


def test_catalog_excludes_inactive_products(engine):
    tag = uuid.uuid4().hex[:8]
    code = f"INACTIVE_{tag}"
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO products (code, name_ar, type, price_sar, active) "
                "VALUES (:c, 'منتج معطّل', 'credits', NULL, false)"
            ),
            {"c": code},
        )
    try:
        result = _run(catalog.list_catalog())
        codes = {item["code"] for item in result["items"]}
        assert code not in codes
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM products WHERE code = :c"), {"c": code})


# ---------------------------------------------------------------------------
# POST /admin/orders — subscription
# ---------------------------------------------------------------------------


def test_create_order_subscription_activates_period_and_grants_credits(engine, customer_id):
    body = catalog.AdminOrderCreateRequest(customer_id=customer_id, product_code="SUB30")
    result = _run(catalog.create_order(body))

    assert result["ok"] is True
    assert result["status"] == "active"
    assert result["kind"] == "subscription"
    assert result["subscription_id"] is not None
    assert result["credits_granted"] == 510
    assert result["wallet_balance"] == 510

    with engine.connect() as conn:
        sub = conn.execute(
            text("SELECT status, daily_target, ends_at, starts_at FROM subscriptions WHERE id = :id"),
            {"id": result["subscription_id"]},
        ).mappings().first()
        wallet = conn.execute(
            text("SELECT balance FROM wallets WHERE customer_id = :id"), {"id": customer_id}
        ).first()
        order = conn.execute(
            text("SELECT status, credits_granted, starts_at, ends_at FROM orders WHERE id = :id"),
            {"id": result["order_id"]},
        ).mappings().first()

    assert sub["status"] == "active"
    assert sub["daily_target"] == 17
    assert (sub["ends_at"] - sub["starts_at"]).days == 30
    assert wallet[0] == 510
    assert order["status"] == "active"
    assert order["credits_granted"] == 510
    assert order["ends_at"] is not None


def test_create_order_subscription_leaves_customer_price_null_when_product_price_tbd(engine, customer_id):
    body = catalog.AdminOrderCreateRequest(customer_id=customer_id, product_code="SUB30")
    _run(catalog.create_order(body))
    with engine.connect() as conn:
        row = conn.execute(text("SELECT price_sar FROM customers WHERE id = :id"), {"id": customer_id}).first()
    assert row[0] is None


# ---------------------------------------------------------------------------
# B11.1: اشتراك جديد وعميل يملك اشتراكًا ساريًا أصلًا → يُمدَّد بلا صفّ جديد.
# ---------------------------------------------------------------------------


def test_create_order_subscription_early_renewal_merges_into_existing(engine, customer_id):
    first = _run(catalog.create_order(catalog.AdminOrderCreateRequest(customer_id=customer_id, product_code="SUB30")))
    assert first["merged_into_subscription_id"] is None
    with engine.connect() as conn:
        first_ends = conn.execute(
            text("SELECT ends_at FROM subscriptions WHERE id = :id"), {"id": first["subscription_id"]}
        ).scalar_one()

    # تجديد مبكر (الفترة لا تزال سارية) — نفس المنتج مرة أخرى.
    second = _run(catalog.create_order(catalog.AdminOrderCreateRequest(customer_id=customer_id, product_code="SUB30")))
    assert second["merged_into_subscription_id"] == first["subscription_id"]
    assert second["subscription_id"] == first["subscription_id"]

    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM subscriptions WHERE customer_id = :cid"), {"cid": customer_id}
        ).scalar_one()
        new_ends = conn.execute(
            text("SELECT ends_at, status FROM subscriptions WHERE id = :id"), {"id": first["subscription_id"]}
        ).mappings().first()
    assert count == 1  # بلا صفّ جديد
    assert new_ends["status"] == "active"
    assert (new_ends["ends_at"] - first_ends).days == 30  # مُدّد من ends_at القديم، لا من الآن


def test_create_order_subscription_renewal_after_end_merges_when_status_still_active(engine, customer_id):
    """اشتراك انتهت فترته زمنيًا (ends_at بالماضي) لكن لم تُشغَّل جولة
    guarantee.py اليومية بعد لإغلاقه (status لا يزال 'active') — تجديد
    العميل بهذه الأثناء يُمدَّد من الآن (starts_at) لا من ends_at الماضي،
    بلا صفّ جديد."""
    from datetime import datetime, timedelta, timezone

    first = _run(catalog.create_order(catalog.AdminOrderCreateRequest(customer_id=customer_id, product_code="SUB30")))
    past_ends = datetime.now(timezone.utc) - timedelta(days=5)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE subscriptions SET ends_at = :ends WHERE id = :id"),
            {"ends": past_ends, "id": first["subscription_id"]},
        )

    second = _run(catalog.create_order(catalog.AdminOrderCreateRequest(customer_id=customer_id, product_code="SUB30")))
    assert second["merged_into_subscription_id"] == first["subscription_id"]

    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM subscriptions WHERE customer_id = :cid"), {"cid": customer_id}
        ).scalar_one()
        row = conn.execute(
            text("SELECT ends_at, status FROM subscriptions WHERE id = :id"), {"id": first["subscription_id"]}
        ).mappings().first()
    assert count == 1
    assert row["status"] == "active"
    assert row["ends_at"] > past_ends + timedelta(days=25)  # مُدّد من starts_at الحالي لا من الماضي


def test_create_order_subscription_for_wallet_customer_switches_billing_mode(engine, customer_id):
    """عميل billing_mode='wallet' يشتري اشتراكًا → يتحوّل لـ'subscription'،
    ويبقى wallet_balance_sar محفوظًا كما هو تمامًا (B11.1)."""
    from decimal import Decimal

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE customers SET billing_mode = 'wallet', wallet_balance_sar = 12.5 WHERE id = :id"),
            {"id": customer_id},
        )

    result = _run(catalog.create_order(catalog.AdminOrderCreateRequest(customer_id=customer_id, product_code="SUB30")))
    assert result["status"] == "active"
    assert result["merged_into_subscription_id"] is None

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT billing_mode, wallet_balance_sar FROM customers WHERE id = :id"), {"id": customer_id}
        ).mappings().first()
    assert row["billing_mode"] == "subscription"
    assert row["wallet_balance_sar"] == Decimal("12.500")  # لم يُمسّ إطلاقًا


# ---------------------------------------------------------------------------
# POST /admin/orders — credits
# ---------------------------------------------------------------------------


def test_create_order_credits_grants_wallet_and_marks_fulfilled(engine, customer_id):
    body = catalog.AdminOrderCreateRequest(customer_id=customer_id, product_code="CR100")
    result = _run(catalog.create_order(body))

    assert result["ok"] is True
    assert result["status"] == "fulfilled"
    assert result["kind"] == "credits"
    assert result["subscription_id"] is None
    assert result["credits_granted"] == 100
    assert result["wallet_balance"] == 100

    with engine.connect() as conn:
        wallet = conn.execute(
            text("SELECT balance FROM wallets WHERE customer_id = :id"), {"id": customer_id}
        ).first()
    assert wallet[0] == 100


def test_create_order_credits_twice_accumulates_wallet_balance(engine, customer_id):
    _run(catalog.create_order(catalog.AdminOrderCreateRequest(customer_id=customer_id, product_code="CR100")))
    result2 = _run(
        catalog.create_order(catalog.AdminOrderCreateRequest(customer_id=customer_id, product_code="CR300"))
    )
    assert result2["wallet_balance"] == 400


# ---------------------------------------------------------------------------
# POST /admin/orders — standalone CV
# ---------------------------------------------------------------------------


def test_create_order_cv_standalone_creates_pending_job_no_credits(engine, customer_id):
    body = catalog.AdminOrderCreateRequest(customer_id=customer_id, product_code="CV1")
    result = _run(catalog.create_order(body))

    assert result["ok"] is True
    assert result["status"] == "pending"
    assert result["kind"] == "standalone_cv"
    assert result["subscription_id"] is None
    assert result["credits_granted"] is None
    assert result["wallet_balance"] is None
    assert result["note"] == "بانتظار توليد السيرة الذاتية"


# ---------------------------------------------------------------------------
# POST /admin/orders — أخطاء
# ---------------------------------------------------------------------------


def test_create_order_unknown_customer_returns_404(engine, customer_id):
    body = catalog.AdminOrderCreateRequest(customer_id=customer_id + 999_999, product_code="SUB30")
    with pytest.raises(HTTPException) as exc_info:
        _run(catalog.create_order(body))
    assert exc_info.value.status_code == 404


def test_create_order_unknown_product_returns_404(engine, customer_id):
    body = catalog.AdminOrderCreateRequest(customer_id=customer_id, product_code="NOPE_XYZ")
    with pytest.raises(HTTPException) as exc_info:
        _run(catalog.create_order(body))
    assert exc_info.value.status_code == 404


def test_create_order_inactive_product_returns_404(engine, customer_id):
    tag = uuid.uuid4().hex[:8]
    code = f"OFF_{tag}"
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO products (code, name_ar, type, price_sar, active) "
                "VALUES (:c, 'منتج معطّل', 'credits', NULL, false)"
            ),
            {"c": code},
        )
    try:
        body = catalog.AdminOrderCreateRequest(customer_id=customer_id, product_code=code)
        with pytest.raises(HTTPException) as exc_info:
            _run(catalog.create_order(body))
        assert exc_info.value.status_code == 404
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM products WHERE code = :c"), {"c": code})


# ---------------------------------------------------------------------------
# GET /admin/orders?customer_id=
# ---------------------------------------------------------------------------


def test_list_orders_returns_customer_orders_newest_first(engine, customer_id):
    _run(catalog.create_order(catalog.AdminOrderCreateRequest(customer_id=customer_id, product_code="CR100")))
    _run(catalog.create_order(catalog.AdminOrderCreateRequest(customer_id=customer_id, product_code="CV1")))

    result = _run(catalog.list_orders(customer_id=customer_id))
    assert result["customer_id"] == customer_id
    assert result["count"] == 2
    assert result["items"][0]["product_code"] == "CV1"
    assert result["items"][1]["product_code"] == "CR100"


def test_list_orders_unknown_customer_returns_404(engine, customer_id):
    with pytest.raises(HTTPException) as exc_info:
        _run(catalog.list_orders(customer_id=customer_id + 999_999))
    assert exc_info.value.status_code == 404


def test_list_orders_empty_for_customer_without_orders(engine, customer_id):
    result = _run(catalog.list_orders(customer_id=customer_id))
    assert result["items"] == []
    assert result["count"] == 0
