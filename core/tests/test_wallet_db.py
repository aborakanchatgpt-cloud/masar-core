"""اختبارات B10 (نموذج المحفظة/الدفع حسب الاستخدام) التي تحتاج قاعدة
بيانات Postgres حقيقية — تُتخطّى تلقائيًا (skip، لا فشل) إن تعذّر الاتصال،
نفس نمط test_catalog.py/test_sender_idempotency.py.

يغطي:
    1. app.wallet: apply_wallet_delta[_conn] (ائتمان/خصم + سجلّ تدقيق).
    2. app.catalog: create_order لمنتج wallet_topup (شحن كامل من الطلب
       الإداري حتى wallet_balance_sar + billing_mode='wallet').
    3. app.sender: الخصم الفعلي عند نجاح الإرسال (_process_row/_mark_success)
       لعميل billing_mode='wallet' — ونقيضه: عميل billing_mode='subscription'
       (الافتراضي) يمرّ بنفس مسار الإرسال بلا أي أثر على wallet_transactions/
       wallet_balance_sar (اختبار الانحدار الإلزامي المطلوب بهذه الدفعة).
    4. app.send_builder: سقف الطابور اليومي لعميل محفظة محكوم بـ
       floor(wallet_balance_sar / rate) لا wallets.balance القديم.
    5. app.telegram_admin_commands.reply_wallet_adjust: تعديل يدوي (ائتمان
       ومدين) مع سبب إلزامي وسجلّ تدقيق type='admin_adjustment'.

تشغيل: cd core && TEST_DATABASE_URL=postgresql+psycopg://... python -m pytest tests/test_wallet_db.py -v
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import catalog, sender, send_builder, telegram_admin_commands as commands, wallet

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
        "لا اتصال بقاعدة بيانات Postgres محلية للاختبار (TEST_DATABASE_URL/"
        f"{DEFAULT_TEST_DB_URL}) — يُتخطّى test_wallet_db.py كليًا.",
        allow_module_level=True,
    )


@pytest.fixture()
def engine() -> Engine:
    return _ENGINE


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def customer_id(engine: Engine):
    tag = uuid.uuid4().hex[:10]
    with engine.begin() as conn:
        cid = conn.execute(
            text(
                "INSERT INTO customers (name, email_service, status, target_daily) "
                "VALUES (:n, :e, 'active', 17) RETURNING id"
            ),
            {"n": f"Wallet Test {tag}", "e": f"wallet-{tag}@masar.invalid"},
        ).scalar_one()
    yield cid
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM wallet_transactions WHERE customer_id = :cid"), {"cid": cid})
        conn.execute(text("DELETE FROM orders WHERE customer_id = :cid"), {"cid": cid})
        conn.execute(text("DELETE FROM ledger WHERE customer_id = :cid"), {"cid": cid})
        conn.execute(text("DELETE FROM customers WHERE id = :cid"), {"cid": cid})


def _get_customer(engine: Engine, customer_id: int) -> dict:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT billing_mode, wallet_balance_sar FROM customers WHERE id = :id"), {"id": customer_id}
        ).mappings().first()
    return dict(row)


def _wallet_tx_rows(engine: Engine, customer_id: int) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT * FROM wallet_transactions WHERE customer_id = :id ORDER BY id"), {"id": customer_id}
        ).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 1. app.wallet — apply_wallet_delta[_conn]
# ---------------------------------------------------------------------------


def test_apply_wallet_delta_credit_then_debit_with_audit_rows(engine, customer_id):
    new_balance = wallet.apply_wallet_delta(engine, customer_id, Decimal("10.00"), "topup", reason="test topup")
    assert new_balance == Decimal("10.00")

    new_balance = wallet.apply_wallet_delta(
        engine, customer_id, Decimal("-0.235"), "consumption"
    )
    assert new_balance == Decimal("9.765")

    rows = _wallet_tx_rows(engine, customer_id)
    assert len(rows) == 2
    assert rows[0]["type"] == "topup" and rows[0]["amount"] == Decimal("10.000")
    assert rows[0]["reason"] == "test topup"
    assert rows[1]["type"] == "consumption" and rows[1]["amount"] == Decimal("-0.235")

    assert _get_customer(engine, customer_id)["wallet_balance_sar"] == Decimal("9.765")


def test_apply_wallet_delta_unknown_type_raises_and_writes_nothing(engine, customer_id):
    with pytest.raises(ValueError):
        wallet.apply_wallet_delta(engine, customer_id, Decimal("5"), "bogus_type")
    assert _wallet_tx_rows(engine, customer_id) == []
    assert _get_customer(engine, customer_id)["wallet_balance_sar"] == Decimal("0.000")


def test_apply_wallet_delta_unknown_customer_raises(engine):
    with pytest.raises(ValueError):
        wallet.apply_wallet_delta(engine, 999_999_999, Decimal("5"), "topup")


def test_get_wallet_balance_and_per_application_rate_read_seeded_defaults(engine, customer_id):
    assert wallet.get_wallet_balance(customer_id, engine=engine) == Decimal("0.000")
    # مبذورة بترحيلة 0021 — 0.235 هو الافتراضي المُعتمَد حاليًا.
    assert wallet.per_application_rate(engine) == Decimal("0.235")


# ---------------------------------------------------------------------------
# 2. app.catalog.create_order — منتج wallet_topup
# ---------------------------------------------------------------------------


def test_catalog_create_order_wallet_topup_sets_balance_and_billing_mode(engine, customer_id):
    result = _run(
        catalog.create_order(
            catalog.AdminOrderCreateRequest(customer_id=customer_id, product_code="WALLET", amount_sar=23.5)
        )
    )
    assert result["ok"] is True
    assert result["kind"] == "wallet_topup"
    assert result["status"] == "fulfilled"
    assert result["wallet_balance_sar"] == pytest.approx(23.5)

    customer = _get_customer(engine, customer_id)
    assert customer["billing_mode"] == "wallet"
    assert customer["wallet_balance_sar"] == Decimal("23.500")

    rows = _wallet_tx_rows(engine, customer_id)
    assert len(rows) == 1
    assert rows[0]["type"] == "topup"
    assert rows[0]["amount"] == Decimal("23.500")

    with engine.connect() as conn:
        order_amount = conn.execute(
            text("SELECT amount_sar FROM orders WHERE customer_id = :cid AND product_code = 'WALLET'"),
            {"cid": customer_id},
        ).scalar_one()
    assert order_amount == Decimal("23.50")


def test_catalog_create_order_wallet_topup_requires_positive_amount_sar(engine, customer_id):
    with pytest.raises(Exception):  # HTTPException — راجع catalog.create_order
        _run(
            catalog.create_order(
                catalog.AdminOrderCreateRequest(customer_id=customer_id, product_code="WALLET", amount_sar=None)
            )
        )
    # لا أثر جانبي — لا رصيد ولا معاملة أُدرجت رغم فشل الطلب.
    assert _get_customer(engine, customer_id)["wallet_balance_sar"] == Decimal("0.000")
    assert _wallet_tx_rows(engine, customer_id) == []


def test_catalog_create_order_subscription_still_unaffected_by_wallet_code(engine, customer_id):
    """انحدار: منتج subscription العادي (SUB30) يبقى بسلوكه القديم تمامًا —
    billing_mode يبقى 'subscription' (الافتراضي)، ولا صفّ wallet_transactions
    يُنشأ إطلاقًا."""
    result = _run(
        catalog.create_order(catalog.AdminOrderCreateRequest(customer_id=customer_id, product_code="SUB30"))
    )
    assert result["kind"] == "subscription"
    customer = _get_customer(engine, customer_id)
    assert customer["billing_mode"] == "subscription"
    assert _wallet_tx_rows(engine, customer_id) == []
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM subscriptions WHERE customer_id = :cid"), {"cid": customer_id})


# ---------------------------------------------------------------------------
# 3. app.sender — الخصم الفعلي عند نجاح الإرسال + انحدار عميل subscription
# ---------------------------------------------------------------------------


@pytest.fixture()
def send_ctx(engine: Engine):
    """عميل + شركة + وظيفة + فرصة + صفّ send_queue جاهز للإرسال — نفس نمط
    test_sender_idempotency.py's ctx، بمعرّفات فريدة (uuid)."""
    tag = uuid.uuid4().hex[:10]
    with engine.begin() as conn:
        company_id = conn.execute(
            text("INSERT INTO companies (name, status) VALUES (:n, 'active') RETURNING id"),
            {"n": f"Wallet Sender Co {tag}"},
        ).scalar_one()
        source_id = conn.execute(
            text(
                "INSERT INTO sources (company_id, source_type, source_url) "
                "VALUES (:cid, 'greenhouse', :url) RETURNING id"
            ),
            {"cid": company_id, "url": f"https://example.invalid/{tag}"},
        ).scalar_one()
        job_id = conn.execute(
            text(
                "INSERT INTO jobs (source_id, company_id, title, dedup_key, company_name) "
                "VALUES (:sid, :cid, 'Wallet Test Job', :dk, 'Wallet Sender Co') RETURNING id"
            ),
            {"sid": source_id, "cid": company_id, "dk": f"dedup-{tag}"},
        ).scalar_one()

        def _make_customer(billing_mode: str) -> int:
            cid = conn.execute(
                text(
                    "INSERT INTO customers (name, email_service, status, target_daily, billing_mode, wallet_balance_sar) "
                    "VALUES (:n, :e, 'active', 17, :bm, :bal) RETURNING id"
                ),
                {
                    "n": f"Wallet Sender Customer {tag} {billing_mode}",
                    "e": f"wallet-sender-{billing_mode}-{tag}@masar.invalid",
                    "bm": billing_mode,
                    "bal": Decimal("1.000") if billing_mode == "wallet" else Decimal("0"),
                },
            ).scalar_one()
            conn.execute(
                text("INSERT INTO profiles (customer_id, cv_text, years_exp) VALUES (:cid, 'CV text', 5)"),
                {"cid": cid},
            )
            conn.execute(text("INSERT INTO wallets (customer_id, balance) VALUES (:cid, 20)"), {"cid": cid})
            return cid

        wallet_customer_id = _make_customer("wallet")
        sub_customer_id = _make_customer("subscription")

        def _make_opportunity(cid: int) -> int:
            return conn.execute(
                text(
                    "INSERT INTO opportunities (customer_id, job_id, score, tier, planned_for, status) "
                    "VALUES (:cid, :jid, 0.9, 'A', CURRENT_DATE, 'queued') RETURNING id"
                ),
                {"cid": cid, "jid": job_id},
            ).scalar_one()

        wallet_opportunity_id = _make_opportunity(wallet_customer_id)
        sub_opportunity_id = _make_opportunity(sub_customer_id)

    ids = {
        "tag": tag,
        "company_id": company_id,
        "source_id": source_id,
        "job_id": job_id,
        "wallet_customer_id": wallet_customer_id,
        "sub_customer_id": sub_customer_id,
        "wallet_opportunity_id": wallet_opportunity_id,
        "sub_opportunity_id": sub_opportunity_id,
    }
    try:
        yield ids
    finally:
        with engine.begin() as conn:
            for cid in (wallet_customer_id, sub_customer_id):
                conn.execute(text("DELETE FROM applications WHERE customer_id = :cid"), {"cid": cid})
                conn.execute(text("DELETE FROM company_cooldowns WHERE customer_id = :cid"), {"cid": cid})
                conn.execute(text("DELETE FROM ledger WHERE customer_id = :cid"), {"cid": cid})
                conn.execute(text("DELETE FROM wallet_transactions WHERE customer_id = :cid"), {"cid": cid})
                conn.execute(text("DELETE FROM customers WHERE id = :cid"), {"cid": cid})
            conn.execute(text("DELETE FROM jobs WHERE id = :jid"), {"jid": job_id})
            conn.execute(text("DELETE FROM sources WHERE id = :sid"), {"sid": source_id})
            conn.execute(text("DELETE FROM companies WHERE id = :cid2"), {"cid2": company_id})


def _insert_send_queue_row(engine: Engine, customer_id: int, job_id: int, opportunity_id: int) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO send_queue (
                    customer_id, opportunity_id, job_id, to_email, cc_email, subject,
                    body_text, attachments, send_after, attempts, status, locked_until, synthetic, created_at
                ) VALUES (
                    :cid, :oid, :jid, 'hr@example.invalid', NULL, 'Application',
                    'Body', '[]', now(), 0, 'sending', NULL, false, now()
                ) RETURNING id
                """
            ),
            {"cid": customer_id, "oid": opportunity_id, "jid": job_id},
        ).scalar_one()


def _fetch_row_dict(engine: Engine, send_queue_id: int) -> dict:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, customer_id, opportunity_id, job_id, to_email, cc_email, subject, "
                "body_text, attachments, attempts, synthetic FROM send_queue WHERE id = :id"
            ),
            {"id": send_queue_id},
        ).mappings().first()
    return dict(row)


def _patch_sender_for_fake_smtp(monkeypatch):
    monkeypatch.setattr(sender, "_smtp_send", lambda transport, msg, recipients: None)
    monkeypatch.setattr(
        sender,
        "resolve_transport",
        lambda customer_id, mail_link: {
            "mode": "sink", "host": "mailpit", "port": 1025, "use_tls": False,
            "username": None, "password": None, "from_addr": "customer@masar.local",
        },
    )


def test_sender_mark_success_consumes_wallet_balance_for_wallet_customer(engine, send_ctx, monkeypatch):
    _patch_sender_for_fake_smtp(monkeypatch)
    cid = send_ctx["wallet_customer_id"]
    sq_id = _insert_send_queue_row(engine, cid, send_ctx["job_id"], send_ctx["wallet_opportunity_id"])
    row = _fetch_row_dict(engine, sq_id)

    ok = sender._process_row(engine, row)
    assert ok is True

    # الرصيد كان 1.000، السعر الافتراضي 0.235 → 0.765 بعد الخصم بالضبط.
    customer = _get_customer(engine, cid)
    assert customer["wallet_balance_sar"] == Decimal("0.765")

    rows = _wallet_tx_rows(engine, cid)
    assert len(rows) == 1
    assert rows[0]["type"] == "consumption"
    assert rows[0]["amount"] == Decimal("-0.235")

    # applications/opportunities تُسجّل بالضبط كأي عميل آخر — لا فرق بمنطق
    # الإرسال نفسه، فقط الخصم المالي إضافي.
    with engine.connect() as conn:
        app_count = conn.execute(
            text("SELECT count(*) FROM applications WHERE customer_id = :cid"), {"cid": cid}
        ).scalar_one()
    assert app_count == 1


def test_sender_mark_success_subscription_customer_regression_untouched(engine, send_ctx, monkeypatch):
    """اختبار الانحدار الإلزامي (تعليمات هذه الدفعة): عميل billing_mode='subscription'
    (الافتراضي، وكل عميل قبل B10) يمرّ بنفس نقطة الإرسال الحقيقية بلا أي
    أثر جانبي متعلّق بالمحفظة إطلاقًا — لا صفّ wallet_transactions، ولا
    تغيير على wallet_balance_sar (يبقى 0)."""
    _patch_sender_for_fake_smtp(monkeypatch)
    cid = send_ctx["sub_customer_id"]
    sq_id = _insert_send_queue_row(engine, cid, send_ctx["job_id"], send_ctx["sub_opportunity_id"])
    row = _fetch_row_dict(engine, sq_id)

    before = _get_customer(engine, cid)
    assert before["billing_mode"] == "subscription"
    assert before["wallet_balance_sar"] == Decimal("0.000")

    ok = sender._process_row(engine, row)
    assert ok is True

    after = _get_customer(engine, cid)
    assert after["wallet_balance_sar"] == Decimal("0.000")  # صفر تغيير سلوك
    assert _wallet_tx_rows(engine, cid) == []

    with engine.connect() as conn:
        app_count = conn.execute(
            text("SELECT count(*) FROM applications WHERE customer_id = :cid"), {"cid": cid}
        ).scalar_one()
    assert app_count == 1  # الإرسال نفسه نجح بلا أي تغيير سلوكي


def test_sender_mark_failure_does_not_touch_legacy_wallet_for_wallet_customer(engine, send_ctx, monkeypatch):
    """عميل billing_mode='wallet' لم يُخصَم منه شيء وقت البناء (send_builder
    لا يستدعي _debit_one_credit له) — فشل نهائي بـ_mark_failure يجب ألا
    يلمس wallets/ledger القديمين إطلاقًا له (لا استرداد لشيء لم يُخصَم)."""
    cid = send_ctx["wallet_customer_id"]
    sq_id = _insert_send_queue_row(engine, cid, send_ctx["job_id"], send_ctx["wallet_opportunity_id"])
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE send_queue SET attempts = :a WHERE id = :id"),
            {"a": sender.MAX_ATTEMPTS - 1, "id": sq_id},
        )
        wallets_balance_before = conn.execute(
            text("SELECT balance FROM wallets WHERE customer_id = :cid"), {"cid": cid}
        ).scalar_one()

    row = _fetch_row_dict(engine, sq_id)
    with engine.begin() as conn:
        sender._mark_failure(conn, row, "SMTP down (test)")

    with engine.connect() as conn:
        wallets_balance_after = conn.execute(
            text("SELECT balance FROM wallets WHERE customer_id = :cid"), {"cid": cid}
        ).scalar_one()
        ledger_count = conn.execute(
            text("SELECT count(*) FROM ledger WHERE customer_id = :cid"), {"cid": cid}
        ).scalar_one()
    assert wallets_balance_after == wallets_balance_before  # لا استرداد وهمي
    assert ledger_count == 0
    # ولا خصم من wallet_balance_sar أيضًا (لم يُخصَم شيء أصلًا عند الفشل).
    assert _get_customer(engine, cid)["wallet_balance_sar"] == Decimal("1.000")


# ---------------------------------------------------------------------------
# 4. app.send_builder — سقف الطابور اليومي محكوم بـfloor(balance/rate)
# ---------------------------------------------------------------------------


def test_build_queue_for_customer_wallet_mode_capped_by_balance(engine, send_ctx, monkeypatch, tmp_path):
    """رصيد 1.000 ريال بسعر افتراضي 0.235 → floor(1.000/0.235) = 4 تقديمات
    كحد أقصى — حتى لو توفّرت أكثر من 4 فرصة مؤهّلة، ولا يُخصَم شيء من
    wallets/ledger القديمين (billing_mode='wallet' يتجاوزهما بالكامل).

    يُموّه cv_builder.ensure_cv_variant (يحتاج Gotenberg حيًّا لتحويل HTML→PDF
    فعليًا — غير متاح ببيئة الاختبار المحلية هنا) حتى يبقى الاختبار مركّزًا
    على منطق سقف رصيد المحفظة وحده، لا على بنية تحتية خارجية غير ذات صلة —
    نفس مبرّر تمويه _smtp_send/resolve_transport بـtest_sender_idempotency.py."""
    import random

    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(
        send_builder.cv_builder,
        "ensure_cv_variant",
        lambda conn, customer_row, profile_row, family: {"pdf_path": str(fake_pdf), "template": 1},
    )

    cid = send_ctx["wallet_customer_id"]
    job_id = send_ctx["job_id"]
    company_id = send_ctx["company_id"]

    # فرص إضافية (فوق الفرصة الأصلية بـsend_ctx) — حتى نملك أكثر من 4
    # مرشّحين مؤهّلين فعليًا لهذا العميل بنفس اليوم.
    extra_job_ids = []
    with engine.begin() as conn:
        for i in range(6):
            jid = conn.execute(
                text(
                    "INSERT INTO jobs (source_id, company_id, title, dedup_key, company_name, description_snippet) "
                    "VALUES (:sid, :cid, :title, :dk, :cname, :snip) RETURNING id"
                ),
                {
                    "sid": send_ctx["source_id"], "cid": company_id, "title": f"Wallet Cap Job {i}",
                    "dk": f"wallet-cap-{send_ctx['tag']}-{i}", "cname": f"Wallet Cap Co {i} {send_ctx['tag']}",
                    # بريد "posted" صريح بالإعلان — يتجنّب مسار no_apply_email
                    # (خارج نطاق هذا الاختبار: سقف الرصيد اليومي فقط).
                    "snip": f"للتقديم أرسل سيرتك على jobs{i}-{send_ctx['tag']}@example.invalid",
                },
            ).scalar_one()
            extra_job_ids.append(jid)
            conn.execute(
                text(
                    "INSERT INTO opportunities (customer_id, job_id, score, tier, planned_for, status) "
                    "VALUES (:cid, :jid, 0.9, 'A', CURRENT_DATE, 'planned')"
                ),
                {"cid": cid, "jid": jid},
            )
        customer_row = conn.execute(
            text(
                "SELECT c.id, c.name, c.phone, c.cities, c.target_daily, c.billing_mode, c.wallet_balance_sar, "
                "p.years_exp, p.skills, p.cv_text, p.seniority, p.degree, p.certs, p.titles, p.languages, "
                "0 AS wallet_balance, NULL::bigint AS mail_link_id, NULL AS mail_address, "
                "NULL::timestamptz AS mail_created_at, NULL AS mail_status "
                "FROM customers c JOIN profiles p ON p.customer_id = c.id WHERE c.id = :cid"
            ),
            {"cid": cid},
        ).mappings().first()

    try:
        with engine.begin() as conn:
            result = send_builder.build_queue_for_customer(conn, dict(customer_row), date.today(), random.Random(1))
        assert result["queued"] == 4

        with engine.connect() as conn:
            queued_count = conn.execute(
                text("SELECT count(*) FROM send_queue WHERE customer_id = :cid AND status != 'cancelled'"),
                {"cid": cid},
            ).scalar_one()
            ledger_count = conn.execute(text("SELECT count(*) FROM ledger WHERE customer_id = :cid"), {"cid": cid}).scalar_one()
            wallets_balance = conn.execute(
                text("SELECT balance FROM wallets WHERE customer_id = :cid"), {"cid": cid}
            ).scalar_one()
        assert queued_count == 4
        assert ledger_count == 0  # billing_mode='wallet' لا يلمس ledger القديم إطلاقًا
        assert wallets_balance == 20  # ولا wallets.balance القديم (يبقى كما بُذر)
        # الخصم الفعلي بالريال لم يحدث بعد (يقع فقط عند الإرسال الفعلي الناجح).
        assert _get_customer(engine, cid)["wallet_balance_sar"] == Decimal("1.000")
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM send_queue WHERE customer_id = :cid"), {"cid": cid})
            conn.execute(text("DELETE FROM opportunities WHERE customer_id = :cid AND job_id = ANY(:jids)"), {"cid": cid, "jids": extra_job_ids})
            for jid in extra_job_ids:
                conn.execute(text("DELETE FROM jobs WHERE id = :jid"), {"jid": jid})


# ---------------------------------------------------------------------------
# 5. app.telegram_admin_commands.reply_wallet_adjust — تعديل يدوي بسبب إلزامي
# ---------------------------------------------------------------------------


class _RecordingClient:
    def __init__(self):
        self.sent: list[dict[str, Any]] = []

    async def send_message(self, chat_id, text_, *, buttons=None, disable_web_page_preview=True):
        self.sent.append({"chat_id": chat_id, "text": text_, "buttons": buttons})
        return {"message_id": len(self.sent)}


def test_admin_wallet_adjust_credit_then_debit_with_audit_rows(engine, customer_id):
    client = _RecordingClient()
    _run(commands.reply_wallet_adjust(client, 677475661, customer_id, Decimal("25.00"), "هدية ترحيبية"))
    assert _get_customer(engine, customer_id)["wallet_balance_sar"] == Decimal("25.000")

    _run(commands.reply_wallet_adjust(client, 677475661, customer_id, Decimal("-5.50"), "تصحيح خطأ شحن"))
    assert _get_customer(engine, customer_id)["wallet_balance_sar"] == Decimal("19.500")

    rows = _wallet_tx_rows(engine, customer_id)
    assert len(rows) == 2
    assert all(r["type"] == "admin_adjustment" for r in rows)
    assert rows[0]["reason"] == "هدية ترحيبية"
    assert rows[1]["reason"] == "تصحيح خطأ شحن"
    assert rows[0]["created_by"] == "677475661"

    assert any("25.00" in m["text"] for m in client.sent)
    assert any("19.50" in m["text"] for m in client.sent)
