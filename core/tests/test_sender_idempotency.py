"""اختبار idempotency لـcore/app/sender.py ضد ازدواج الإرسال الفعلي (B4،
الدليل §"idempotent sends (no double-send on retry)") — تصحيح Critical/High
1 (F1) بمراجعة B4 الأوفلاين (docs/reports/B4-offline-review.md):

    "انهيار العملية بين نجاح SMTP فعليًا وكتابة _mark_success بقاعدة
    البيانات يترك الصفّ بحالة 'sending' حتى ينتهي locked_until (5 دقائق)
    ثم يُعاد التقاطه وإرساله مجددًا فعليًا لنفس الشركة."

يحتاج هذا الاختبار قاعدة بيانات Postgres حقيقية (نفس نمط سلسلة الترحيل
الكاملة التي استخدمها المراجع أوفلاين — CTE بـ`FOR UPDATE SKIP LOCKED` لا
يمكن محاكاته بصدق بـSQLite). يُتخطّى تلقائيًا (skip، لا فشل) إن تعذّر
الاتصال — لا اختبار وهمي (fake) يُظهر نجاحًا بلا دليل فعلي.

الإعداد الافتراضي: `postgresql+psycopg://masar_test:masar_test@localhost:5432/masar_test`
(نفس قاعدة الاختبار المحلية المستخدمة لتشغيل سلسلة الترحيل الكاملة أثناء
تنفيذ هذه التصحيحات). يمكن تجاوزها بمتغيّر بيئة `TEST_DATABASE_URL`.

تشغيل: cd core && TEST_DATABASE_URL=postgresql+psycopg://... python -m pytest tests/test_sender_idempotency.py -v
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import sender

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
        f"{DEFAULT_TEST_DB_URL}) — يُتخطّى test_sender_idempotency.py كليًا "
        "(لا اختبار وهمي بلا قاعدة بيانات حقيقية لمنطق FOR UPDATE SKIP LOCKED).",
        allow_module_level=True,
    )


@pytest.fixture()
def engine() -> Engine:
    return _ENGINE


@pytest.fixture()
def ctx(engine: Engine):
    """يبني عميلًا + شركة + مصدر + وظيفة + فرصة كاملة صالحة لاختبار واحد،
    بمعرّفات فريدة (uuid) لتجنّب أي تصادم بين تشغيلات متتالية، وينظّف كل
    شيء (بترتيب يحترم قيود FK بلا ON DELETE CASCADE على applications) عند
    الانتهاء بصرف النظر عن نجاح الاختبار أو فشله."""
    tag = uuid.uuid4().hex[:10]
    with engine.begin() as conn:
        company_id = conn.execute(
            text("INSERT INTO companies (name, status) VALUES (:n, 'active') RETURNING id"),
            {"n": f"Idem Test Co {tag}"},
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
                "VALUES (:sid, :cid, 'Process Engineer', :dk, 'Idem Test Co') RETURNING id"
            ),
            {"sid": source_id, "cid": company_id, "dk": f"dedup-{tag}"},
        ).scalar_one()
        customer_id = conn.execute(
            text(
                "INSERT INTO customers (name, email_service, status, target_daily) "
                "VALUES (:n, :e, 'active', 17) RETURNING id"
            ),
            {"n": f"Idem Test Customer {tag}", "e": f"idem-{tag}@masar.invalid"},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO profiles (customer_id, cv_text, years_exp) VALUES (:cid, 'CV text', 5)"
            ),
            {"cid": customer_id},
        )
        conn.execute(
            text("INSERT INTO wallets (customer_id, balance) VALUES (:cid, 20)"),
            {"cid": customer_id},
        )
        opportunity_id = conn.execute(
            text(
                "INSERT INTO opportunities (customer_id, job_id, score, tier, planned_for, status) "
                "VALUES (:cid, :jid, 0.9, 'A', CURRENT_DATE, 'queued') RETURNING id"
            ),
            {"cid": customer_id, "jid": job_id},
        ).scalar_one()

    ids = {
        "tag": tag,
        "company_id": company_id,
        "source_id": source_id,
        "job_id": job_id,
        "customer_id": customer_id,
        "opportunity_id": opportunity_id,
    }
    try:
        yield ids
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM applications WHERE customer_id = :cid"), {"cid": customer_id})
            conn.execute(text("DELETE FROM company_cooldowns WHERE customer_id = :cid"), {"cid": customer_id})
            conn.execute(text("DELETE FROM ledger WHERE customer_id = :cid"), {"cid": customer_id})
            # حذف العميل يُسقط (CASCADE) profiles/wallets/mail_links/send_queue/opportunities تلقائيًا.
            conn.execute(text("DELETE FROM customers WHERE id = :cid"), {"cid": customer_id})
            conn.execute(text("DELETE FROM jobs WHERE id = :jid"), {"jid": job_id})
            conn.execute(text("DELETE FROM sources WHERE id = :sid"), {"sid": source_id})
            conn.execute(text("DELETE FROM companies WHERE id = :cid2"), {"cid2": company_id})


def _insert_send_queue_row(engine: Engine, ctx: dict, *, status: str, locked_until=None, attempts: int = 0) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO send_queue (
                    customer_id, opportunity_id, job_id, to_email, cc_email, subject,
                    body_text, attachments, send_after, attempts, status, locked_until, synthetic, created_at
                ) VALUES (
                    :cid, :oid, :jid, 'hr@example.invalid', NULL, 'Application',
                    'Body', '[]', now(), :attempts, :status, :locked_until, false, now()
                ) RETURNING id
                """
            ),
            {
                "cid": ctx["customer_id"],
                "oid": ctx["opportunity_id"],
                "jid": ctx["job_id"],
                "attempts": attempts,
                "status": status,
                "locked_until": locked_until,
            },
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


def _count_applications(engine: Engine, customer_id: int) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT count(*) FROM applications WHERE customer_id = :cid"), {"cid": customer_id}
        ).scalar_one()


# ---------------------------------------------------------------------------
# 1. المسار الأساسي: صفّ جديد بلا تطبيق سابق → _process_row يرسل فعليًا
#    (SMTP مُموَّه) ويُنشئ applications واحدة، ويضبط opportunities='sent'
#    (تصحيح Critical/High 2 — لم يعد send_builder يضبطها وقت البناء).
# ---------------------------------------------------------------------------


def test_process_row_success_creates_single_application_and_marks_opportunity_sent(engine, ctx, monkeypatch):
    monkeypatch.setattr(sender, "_smtp_send", lambda transport, msg, recipients: None)
    monkeypatch.setattr(
        sender,
        "resolve_transport",
        lambda customer_id, mail_link: {
            "mode": "sink",
            "host": "mailpit",
            "port": 1025,
            "use_tls": False,
            "username": None,
            "password": None,
            "from_addr": "customer@masar.local",
        },
    )

    sq_id = _insert_send_queue_row(engine, ctx, status="sending", locked_until=None)
    row = _fetch_row_dict(engine, sq_id)

    ok = sender._process_row(engine, row)
    assert ok is True

    assert _count_applications(engine, ctx["customer_id"]) == 1
    with engine.connect() as conn:
        sq_status = conn.execute(
            text("SELECT status FROM send_queue WHERE id = :id"), {"id": sq_id}
        ).scalar_one()
        opp_status = conn.execute(
            text("SELECT status FROM opportunities WHERE id = :id"), {"id": ctx["opportunity_id"]}
        ).scalar_one()
    assert sq_status == "sent"
    assert opp_status == "sent"


# ---------------------------------------------------------------------------
# 2. F1 — صفّ استُعيد بعد انتهاء قفل، وتطبيق ناجح مسجّل أصلًا لنفس الفرصة
#    (يحاكي انهيار العملية بين نجاح SMTP الفعلي و_mark_success): يجب ألا
#    يُستدعى SMTP مجددًا إطلاقًا، ولا يُنشأ صفّ applications ثانٍ.
# ---------------------------------------------------------------------------


def test_reclaimed_sending_row_with_existing_application_does_not_resend(engine, ctx, monkeypatch):
    # 1) محاكاة إرسال ناجح سابق: صفّ applications موجود أصلًا لنفس
    #    (customer_id, job_id, opportunity_id) — كما يُنشئه _mark_success
    #    الحقيقي بعد نجاح SMTP، لكن هنا نُدخله مباشرة لمحاكاة "نجح الإرسال
    #    فعليًا، ثم انهارت العملية قبل تحديث send_queue".
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO applications (customer_id, job_id, opportunity_id, sent_at, message_id, status, created_at)
                VALUES (:cid, :jid, :oid, now(), :mid, 'sent', now())
                """
            ),
            {
                "cid": ctx["customer_id"],
                "jid": ctx["job_id"],
                "oid": ctx["opportunity_id"],
                "mid": "<already-sent@masar.local>",
            },
        )

    # 2) صفّ send_queue عالق بحالة 'sending' (القفل انتهى أصلًا) — هذا هو
    #    الصفّ الذي تُعيد _claim_due_batch التقاطه بعد التصحيح.
    sq_id = _insert_send_queue_row(
        engine, ctx, status="sending", locked_until=datetime.now(timezone.utc) - timedelta(minutes=1), attempts=0
    )
    row = _fetch_row_dict(engine, sq_id)

    def _boom(*args, **kwargs):
        raise AssertionError("لا يجب استدعاء _smtp_send إطلاقًا — يوجد تطبيق ناجح مسجّل أصلًا (idempotency)")

    monkeypatch.setattr(sender, "_smtp_send", _boom)

    ok = sender._process_row(engine, row)
    assert ok is True

    # لا يزال هناك صفّ applications واحد فقط (لم يُضَف ثانٍ، ولا SMTP جديد).
    assert _count_applications(engine, ctx["customer_id"]) == 1

    with engine.connect() as conn:
        sq_status = conn.execute(
            text("SELECT status, message_id FROM send_queue WHERE id = :id"), {"id": sq_id}
        ).mappings().first()
        opp_status = conn.execute(
            text("SELECT status FROM opportunities WHERE id = :id"), {"id": ctx["opportunity_id"]}
        ).scalar_one()
    assert sq_status["status"] == "sent"
    assert sq_status["message_id"] == "<already-sent@masar.local>"
    assert opp_status == "sent"


# ---------------------------------------------------------------------------
# 3. _claim_due_batch يستعيد فعليًا صفًّا عالقًا بحالة 'sending' بعد انتهاء
#    القفل (الشبكة الآمنة الموثَّقة أصلًا بالتعليق، كانت معطَّلة فعليًا قبل
#    التصحيح لأن الشرط كان status='queued' فقط).
# ---------------------------------------------------------------------------


def test_claim_due_batch_reclaims_expired_sending_row(engine, ctx):
    sq_id = _insert_send_queue_row(
        engine, ctx, status="sending", locked_until=datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE send_queue SET send_after = now() - interval '1 minute' WHERE id = :id"),
            {"id": sq_id},
        )

    batch = sender._claim_due_batch(engine, limit=10)
    claimed_ids = {r["id"] for r in batch}
    assert sq_id in claimed_ids

    with engine.connect() as conn:
        status = conn.execute(text("SELECT status FROM send_queue WHERE id = :id"), {"id": sq_id}).scalar_one()
    assert status == "sending"  # أُعيد قفله بنجاح لدورة معالجة جديدة


def test_claim_due_batch_does_not_reclaim_row_with_active_lock(engine, ctx):
    sq_id = _insert_send_queue_row(
        engine, ctx, status="sending", locked_until=datetime.now(timezone.utc) + timedelta(minutes=5)
    )
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE send_queue SET send_after = now() - interval '1 minute' WHERE id = :id"),
            {"id": sq_id},
        )

    batch = sender._claim_due_batch(engine, limit=10)
    claimed_ids = {r["id"] for r in batch}
    assert sq_id not in claimed_ids


# ---------------------------------------------------------------------------
# 4. فشل نهائي (MAX_ATTEMPTS) يجب ألا يُبقي opportunities.status='queued'
#    للأبد (تصحيح Critical/High 2 بالاتجاه المعاكس لنجاح الإرسال).
# ---------------------------------------------------------------------------


def test_permanent_failure_marks_opportunity_skipped(engine, ctx):
    sq_id = _insert_send_queue_row(engine, ctx, status="sending", attempts=sender.MAX_ATTEMPTS - 1)
    row = _fetch_row_dict(engine, sq_id)
    row["attempts"] = sender.MAX_ATTEMPTS - 1

    with engine.begin() as conn:
        sender._mark_failure(conn, row, "smtp error: connection refused")

    with engine.connect() as conn:
        sq_status = conn.execute(text("SELECT status FROM send_queue WHERE id = :id"), {"id": sq_id}).scalar_one()
        opp_status = conn.execute(
            text("SELECT status FROM opportunities WHERE id = :id"), {"id": ctx["opportunity_id"]}
        ).scalar_one()
    assert sq_status == "failed"
    assert opp_status == "skipped"
