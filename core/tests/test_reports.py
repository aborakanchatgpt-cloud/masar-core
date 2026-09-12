"""اختبارات core/app/reports.py (B5a البند 1) — التقرير اليومي: تقديمات
اليوم، استبعاد المرتد من التقدّم (bounce exclusion)، نص بشري بلا كسور/سقوف،
idempotency per (customer_id, report_date)، وأن الجولة تغطي العملاء
**النشطين فقط**.

يحتاج قاعدة بيانات Postgres حقيقية (نفس نمط test_sender_idempotency.py) —
يُتخطّى تلقائيًا (skip، لا فشل) إن تعذّر الاتصال.
"""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import reports

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
        "لا اتصال بقاعدة بيانات Postgres محلية للاختبار — يُتخطّى test_reports.py كليًا.",
        allow_module_level=True,
    )


@pytest.fixture()
def engine() -> Engine:
    return _ENGINE


@pytest.fixture()
def ctx(engine: Engine):
    tag = uuid.uuid4().hex[:10]
    with engine.begin() as conn:
        company_id = conn.execute(
            text("INSERT INTO companies (name, status) VALUES (:n, 'active') RETURNING id"),
            {"n": f"Reports Test Co {tag}"},
        ).scalar_one()
        source_id = conn.execute(
            text(
                "INSERT INTO sources (company_id, source_type, source_url) "
                "VALUES (:cid, 'greenhouse', :url) RETURNING id"
            ),
            {"cid": company_id, "url": f"https://example.invalid/reports/{tag}"},
        ).scalar_one()
        job_id = conn.execute(
            text(
                "INSERT INTO jobs (source_id, company_id, title, dedup_key, company_name, city) "
                "VALUES (:sid, :cid, 'Quality Engineer', :dk, 'Reports Test Co', 'Jeddah') RETURNING id"
            ),
            {"sid": source_id, "cid": company_id, "dk": f"dedup-reports-{tag}"},
        ).scalar_one()
        active_customer_id = conn.execute(
            text(
                "INSERT INTO customers (name, email_service, status, target_daily) "
                "VALUES (:n, :e, 'active', 17) RETURNING id"
            ),
            {"n": f"Reports Active {tag}", "e": f"reports-active-{tag}@masar.invalid"},
        ).scalar_one()
        paused_customer_id = conn.execute(
            text(
                "INSERT INTO customers (name, email_service, status, target_daily) "
                "VALUES (:n, :e, 'paused', 17) RETURNING id"
            ),
            {"n": f"Reports Paused {tag}", "e": f"reports-paused-{tag}@masar.invalid"},
        ).scalar_one()

    ids = {
        "tag": tag,
        "company_id": company_id,
        "source_id": source_id,
        "job_id": job_id,
        "active_customer_id": active_customer_id,
        "paused_customer_id": paused_customer_id,
    }
    try:
        yield ids
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM daily_reports WHERE customer_id = ANY(:ids)"),
                          {"ids": [active_customer_id, paused_customer_id]})
            conn.execute(text("DELETE FROM applications WHERE customer_id = ANY(:ids)"),
                          {"ids": [active_customer_id, paused_customer_id]})
            conn.execute(text("DELETE FROM customers WHERE id = ANY(:ids)"),
                          {"ids": [active_customer_id, paused_customer_id]})
            conn.execute(text("DELETE FROM jobs WHERE id = :jid"), {"jid": job_id})
            conn.execute(text("DELETE FROM sources WHERE id = :sid"), {"sid": source_id})
            conn.execute(text("DELETE FROM companies WHERE id = :cid"), {"cid": company_id})


def _insert_application(engine: Engine, *, customer_id: int, job_id: int, status: str, sent_at: datetime) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO applications (customer_id, job_id, sent_at, message_id, status, created_at)
                VALUES (:cid, :jid, :sent_at, :mid, :status, now()) RETURNING id
                """
            ),
            {"cid": customer_id, "jid": job_id, "sent_at": sent_at, "mid": f"<{uuid.uuid4().hex}@masar.local>", "status": status},
        ).scalar_one()


# ---------------------------------------------------------------------------
# 1. build_customer_report: تقديمات اليوم تُدرَج، والمرتدة لا تُحسب بالتقدّم
#    (bounce exclusion — القاعدة الثابتة: "المرتد لا يُحتسب أبدًا").
# ---------------------------------------------------------------------------


def test_build_customer_report_lists_today_apps_and_excludes_bounced_from_progress(engine, ctx):
    today = date.today()
    day_start_utc = datetime.now(timezone.utc).replace(hour=6, minute=0, second=0, microsecond=0)

    _insert_application(engine, customer_id=ctx["active_customer_id"], job_id=ctx["job_id"], status="sent", sent_at=day_start_utc)
    _insert_application(engine, customer_id=ctx["active_customer_id"], job_id=ctx["job_id"], status="bounced", sent_at=day_start_utc)

    payload = reports.build_customer_report(ctx["active_customer_id"], today, engine=engine)

    assert payload is not None
    # كلا الصفّين ظهرا بقائمة "اليوم" (أُرسلا فعلاً) — لكن المرتد لا يُحسب
    # بالتقدّم (all_time_count بلا اشتراك نشط هنا).
    assert payload["today_count"] == 2
    assert payload["all_time_count"] == 1  # فقط الصفّ status='sent' غير المرتد
    assert "قدّمنا لك اليوم 2 فرصة" in payload["text"]
    assert "/" not in payload["text"]  # لا صياغة كسرية (رقم/رقم) بأي مكان بالنص


def test_build_customer_report_unknown_customer_returns_none(engine, ctx):
    assert reports.build_customer_report(999_999_999, date.today(), engine=engine) is None


# ---------------------------------------------------------------------------
# 1b. تصحيح B5c (NEEDS-CORE #1): كل عنصر بـtoday_applications يحمل
#     send_queue_id (+company_id، job_id) — يُشتق من send_queue عبر
#     opportunity_id، لا NULL حين توجد صفّ send_queue فعلي بحالة 'sent'
#     لنفس الفرصة، وNULL حين لا opportunity_id على التطبيق (تطبيقات قديمة).
# ---------------------------------------------------------------------------


def test_today_applications_carry_send_queue_id_when_available(engine, ctx):
    today = date.today()
    day_start_utc = datetime.now(timezone.utc).replace(hour=6, minute=0, second=0, microsecond=0)

    with engine.begin() as conn:
        opportunity_id = conn.execute(
            text(
                """
                INSERT INTO opportunities (customer_id, job_id, score, tier, planned_for, status)
                VALUES (:cid, :jid, 0.9, 'A', :d, 'sent') RETURNING id
                """
            ),
            {"cid": ctx["active_customer_id"], "jid": ctx["job_id"], "d": today},
        ).scalar_one()
        send_queue_id = conn.execute(
            text(
                """
                INSERT INTO send_queue (customer_id, opportunity_id, job_id, to_email, subject, body_text,
                                         send_after, status, synthetic, created_at)
                VALUES (:cid, :oid, :jid, 'hr@example.invalid', 'Application', 'Body', now(), 'sent', false, now())
                RETURNING id
                """
            ),
            {"cid": ctx["active_customer_id"], "oid": opportunity_id, "jid": ctx["job_id"]},
        ).scalar_one()
        conn.execute(
            text(
                """
                INSERT INTO applications (customer_id, job_id, opportunity_id, sent_at, message_id, status, created_at)
                VALUES (:cid, :jid, :oid, :sent_at, :mid, 'sent', now())
                """
            ),
            {
                "cid": ctx["active_customer_id"], "jid": ctx["job_id"], "oid": opportunity_id,
                "sent_at": day_start_utc, "mid": f"<{uuid.uuid4().hex}@masar.local>",
            },
        )

    # طبيق آخر بلا opportunity_id (نمط قديم قبل B3) — send_queue_id يجب أن
    # يبقى None بلا أي خطأ (LEFT JOIN حقيقي، لا استبعاد للصف).
    _insert_application(engine, customer_id=ctx["active_customer_id"], job_id=ctx["job_id"], status="sent", sent_at=day_start_utc)

    payload = reports.build_customer_report(ctx["active_customer_id"], today, engine=engine)
    apps = payload["today_applications"]
    assert len(apps) == 2

    with_sq = [a for a in apps if a["send_queue_id"] is not None]
    without_sq = [a for a in apps if a["send_queue_id"] is None]
    assert len(with_sq) == 1
    assert len(without_sq) == 1
    assert with_sq[0]["send_queue_id"] == send_queue_id
    assert with_sq[0]["job_id"] == ctx["job_id"]
    assert with_sq[0]["company_id"] == ctx["company_id"]

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM send_queue WHERE id = :id"), {"id": send_queue_id})
        conn.execute(text("DELETE FROM opportunities WHERE id = :id"), {"id": opportunity_id})


def test_build_customer_report_no_apps_today_uses_encouraging_line(engine, ctx):
    payload = reports.build_customer_report(ctx["active_customer_id"], date.today(), engine=engine)
    assert payload["today_count"] == 0
    assert "قدّمنا لك اليوم" not in payload["text"]


# ---------------------------------------------------------------------------
# 2. _progress_phrase (وحدة نقية بلا DB): بلا كسور/حدود أبدًا.
# ---------------------------------------------------------------------------


def test_progress_phrase_never_shows_fraction_or_cap_wording():
    text_with_period = reports._progress_phrase({"start": "x", "end": "y"}, 42, 0)
    text_without_period = reports._progress_phrase(None, 0, 17)

    for phrase in (text_with_period, text_without_period):
        assert "/" not in phrase
        assert "حد" not in phrase
        assert "510" not in phrase


# ---------------------------------------------------------------------------
# 3. run_reports_round: عميل نشط فقط، idempotent (لا تكرار لنفس اليوم).
# ---------------------------------------------------------------------------


def test_run_reports_round_covers_active_only_and_is_idempotent(engine, ctx):
    today = date.today()
    day_start_utc, _ = reports._riyadh_day_bounds_utc(today)
    # تطبيق فعلي مضمون بغضّ النظر عن يوم الأسبوع الفعلي وقت التشغيل (تفاديًا
    # لتخطي B4/v2-B6 الجمعة/السبت — raجع الاختبارات المخصصة أدناه لهذا
    # السلوك تحديدًا — إذا صادف تشغيل هذا الاختبار بيوم عطلة).
    _insert_application(
        engine, customer_id=ctx["active_customer_id"], job_id=ctx["job_id"],
        status="sent", sent_at=day_start_utc + timedelta(hours=6),
    )

    result1 = reports.run_reports_round(report_date=today, engine=engine)
    assert result1["ok"] is True

    with engine.connect() as conn:
        active_row = conn.execute(
            text("SELECT id FROM daily_reports WHERE customer_id = :cid AND report_date = :d"),
            {"cid": ctx["active_customer_id"], "d": today},
        ).first()
        paused_row = conn.execute(
            text("SELECT id FROM daily_reports WHERE customer_id = :cid AND report_date = :d"),
            {"cid": ctx["paused_customer_id"], "d": today},
        ).first()
    assert active_row is not None
    assert paused_row is None  # العميل الموقوف لا يحصل على تقرير

    # تشغيلة ثانية لنفس اليوم — idempotent (لا صفّ إضافي لنفس customer+date).
    result2 = reports.run_reports_round(report_date=today, engine=engine)
    assert result2["ok"] is True

    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM daily_reports WHERE customer_id = :cid AND report_date = :d"),
            {"cid": ctx["active_customer_id"], "d": today},
        ).scalar_one()
    assert count == 1


# ---------------------------------------------------------------------------
# 4. B4/v2-B6 (12 سبتمبر): تخطي الجمعة/السبت لعميل بلا أي تقديم/ردّ فعلي ذلك
#    اليوم — لا معنى لرسالة "لم نجد فرصًا" بيوم عطلة لا عمل فيه أصلًا. عميل
#    حصل فعليًا على تقديم أو ردّ بيوم عطلة (نادر لكن ممكن تقنيًا) يحصل على
#    تقريره كالمعتاد. نستخدم تاريخين ثابتين معروفَي يوم الأسبوع (بدل
#    date.today()) حتى لا يعتمد الاختبار على يوم تشغيله الفعلي.
# ---------------------------------------------------------------------------


def test_run_reports_round_skips_weekend_customer_with_no_activity(engine, ctx):
    friday = date(2026, 9, 11)  # جمعة معروفة — weekday() == 4
    result = reports.run_reports_round(report_date=friday, engine=engine)
    assert result["ok"] is True
    assert result["skipped_weekend_empty"] >= 1

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id FROM daily_reports WHERE customer_id = :cid AND report_date = :d"),
            {"cid": ctx["active_customer_id"], "d": friday},
        ).first()
    assert row is None  # لا تقرير — عطلة بلا أي نشاط فعلي


def test_run_reports_round_still_reports_weekend_customer_with_real_application(engine, ctx):
    saturday = date(2026, 9, 12)  # سبت معروف — weekday() == 5
    day_start_utc, _ = reports._riyadh_day_bounds_utc(saturday)
    _insert_application(
        engine, customer_id=ctx["active_customer_id"], job_id=ctx["job_id"],
        status="sent", sent_at=day_start_utc + timedelta(hours=6),
    )

    result = reports.run_reports_round(report_date=saturday, engine=engine)
    assert result["ok"] is True

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id FROM daily_reports WHERE customer_id = :cid AND report_date = :d"),
            {"cid": ctx["active_customer_id"], "d": saturday},
        ).first()
    assert row is not None  # نشاط فعلي بيوم عطلة → تقرير كالمعتاد رغم أنه عطلة


# ---------------------------------------------------------------------------
# 5. _build_text (وحدة نقية بلا DB): سطر شرح أزرار 👎/🎉 يظهر فقط إذا وُجدت
#    تقديمات فعلية اليوم (B4/v2-B6 — كانت الأزرار تصل بلا أي شرح لمعناها).
# ---------------------------------------------------------------------------


def test_build_text_includes_feedback_buttons_explanation_when_apps_exist():
    rng = reports._seed_rng(1, date(2026, 9, 10))
    text_body = reports._build_text(
        customer_name="أحمد",
        report_date=date(2026, 9, 10),
        today_apps=[{"title": "مهندس جودة", "company": "شركة تجريبية", "city": "جدة"}],
        period=None,
        period_count=0,
        all_time_count=1,
        exclusions=[],
        replies=[],
        rng=rng,
    )
    assert "👎" in text_body
    assert "🎉" in text_body
    assert "اضغط 👎" in text_body


def test_build_text_omits_feedback_buttons_explanation_when_no_apps_today():
    rng = reports._seed_rng(1, date(2026, 9, 10))
    text_body = reports._build_text(
        customer_name="أحمد",
        report_date=date(2026, 9, 10),
        today_apps=[],
        period=None,
        period_count=0,
        all_time_count=0,
        exclusions=[],
        replies=[],
        rng=rng,
    )
    assert "👎" not in text_body
    assert "🎉" not in text_body
