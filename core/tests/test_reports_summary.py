"""اختبارات core/app/reports_summary.py (B6/v3) — منطق التجميع/التنسيق
البحت لـ"📊 تقرير شامل": حساب مدى الفترة السريعة، تنسيق النص العربي بلا
اتصال قاعدة بيانات، ثم التجميع الفعلي (مالي/رسائل/إحصاءات إرسال) بقاعدة
بيانات Postgres حقيقية — يُتخطّى تلقائيًا (skip، لا فشل) إن تعذّر الاتصال،
نفس نمط test_reports.py.
"""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import reports_summary

DEFAULT_TEST_DB_URL = "postgresql+psycopg://masar_test:masar_test@localhost:5432/masar_test"


# =========================================================================
# اختبارات بحتة — بلا قاعدة بيانات إطلاقًا
# =========================================================================


def test_compute_period_range_day():
    today = date(2026, 9, 12)
    assert reports_summary.compute_period_range("day", today=today) == (today, today)


def test_compute_period_range_week():
    today = date(2026, 9, 12)
    start, end = reports_summary.compute_period_range("week", today=today)
    assert end == today
    assert start == date(2026, 9, 6)
    assert (end - start).days == 6


def test_compute_period_range_month():
    today = date(2026, 9, 12)
    start, end = reports_summary.compute_period_range("month", today=today)
    assert end == today
    assert start == date(2026, 8, 14)
    assert (end - start).days == 29


def test_compute_period_range_unknown_raises():
    with pytest.raises(ValueError):
        reports_summary.compute_period_range("year", today=date(2026, 9, 12))


def _empty_payload(start="2026-09-01", end="2026-09-11") -> dict:
    return {
        "start_date": start,
        "end_date": end,
        "financial": {"rows": [], "grand_total": 0.0},
        "messages": {"complaint": [], "heart_to_heart": [], "note": []},
        "stats": {"sends": 0, "bounces": 0},
    }


def test_build_summary_text_single_day_header():
    payload = _empty_payload(start="2026-09-12", end="2026-09-12")
    text_body = reports_summary.build_summary_text(payload)
    assert "🗓️ 2026-09-12" in text_body
    assert "من" not in text_body.splitlines()[1]


def test_build_summary_text_range_header():
    payload = _empty_payload()
    text_body = reports_summary.build_summary_text(payload)
    assert "🗓️ من 2026-09-01 إلى 2026-09-11" in text_body


def test_build_summary_text_empty_sections_say_no_data():
    payload = _empty_payload()
    text_body = reports_summary.build_summary_text(payload)
    assert "لا طلبات دفع مؤكَّدة بهذه الفترة." in text_body
    assert "لا رسائل جديدة بهذه الفترة." in text_body


def test_build_summary_text_financial_rows_and_grand_total():
    payload = _empty_payload()
    payload["financial"] = {
        "rows": [
            {"product_code": "SUB30", "name_ar": "اشتراك شهري", "count": 3, "total": 900.0},
            {"product_code": "CR100", "name_ar": "رصيد 100 تقديم", "count": 1, "total": 60.0},
        ],
        "grand_total": 960.0,
    }
    text_body = reports_summary.build_summary_text(payload)
    assert "اشتراك شهري: 3 — 900 ريال" in text_body
    assert "رصيد 100 تقديم: 1 — 60 ريال" in text_body
    assert "الإجمالي: 960 ريال" in text_body


def test_build_summary_text_messages_grouped_and_ordered_by_category():
    payload = _empty_payload()
    payload["messages"] = {
        "complaint": [{"customer_id": 1, "customer_name": "سارة", "text": "تأخّر الردّ", "created_at": None}],
        "heart_to_heart": [{"customer_id": 2, "customer_name": "خالد", "text": "شكرًا لكم", "created_at": None}],
        "note": [],
    }
    text_body = reports_summary.build_summary_text(payload)
    complaint_idx = text_body.index("😔 شكاوى")
    heart_idx = text_body.index("💬 من قلب لقلب")
    assert complaint_idx < heart_idx  # ترتيب الفئات ثابت: شكوى ثم قلب لقلب ثم ملاحظة
    assert "سارة (#1) — تأخّر الردّ" in text_body
    assert "خالد (#2) — شكرًا لكم" in text_body
    assert "📝 ملاحظات" not in text_body  # فئة فارغة لا تُطبع عنوانها


def test_build_summary_text_truncates_long_message_snippet():
    long_text = "أ" * 500
    payload = _empty_payload()
    payload["messages"]["note"] = [
        {"customer_id": 9, "customer_name": "عميل", "text": long_text, "created_at": None}
    ]
    text_body = reports_summary.build_summary_text(payload)
    assert "…" in text_body
    assert long_text not in text_body


# =========================================================================
# اختبارات مُتّصلة بقاعدة بيانات حقيقية
# =========================================================================


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
        "لا اتصال بقاعدة بيانات Postgres محلية للاختبار — يُتخطّى test_reports_summary.py (قسم DB) كليًا.",
        allow_module_level=True,
    )


@pytest.fixture()
def engine() -> Engine:
    return _ENGINE


@pytest.fixture()
def ctx(engine: Engine):
    tag = uuid.uuid4().hex[:10]
    with engine.begin() as conn:
        product_code = f"SUMTEST{tag[:6]}".upper()
        conn.execute(
            text(
                "INSERT INTO products (code, name_ar, type, price_sar, days, credits, active) "
                "VALUES (:c, :n, 'subscription', 300.00, 30, NULL, true)"
            ),
            {"c": product_code, "n": f"باقة اختبار {tag}"},
        )
        customer_id = conn.execute(
            text(
                "INSERT INTO customers (name, email_service, status, target_daily) "
                "VALUES (:n, :e, 'active', 17) RETURNING id"
            ),
            {"n": f"Summary Test {tag}", "e": f"summary-{tag}@masar.invalid"},
        ).scalar_one()
        company_id = conn.execute(
            text("INSERT INTO companies (name, status) VALUES (:n, 'active') RETURNING id"),
            {"n": f"Summary Co {tag}"},
        ).scalar_one()
        source_id = conn.execute(
            text(
                "INSERT INTO sources (company_id, source_type, source_url) "
                "VALUES (:cid, 'greenhouse', :url) RETURNING id"
            ),
            {"cid": company_id, "url": f"https://example.invalid/summary/{tag}"},
        ).scalar_one()
        job_id = conn.execute(
            text(
                "INSERT INTO jobs (source_id, company_id, title, dedup_key, company_name, city) "
                "VALUES (:sid, :cid, 'Summary Job', :dk, 'Summary Co', 'Riyadh') RETURNING id"
            ),
            {"sid": source_id, "cid": company_id, "dk": f"dedup-summary-{tag}"},
        ).scalar_one()

    yield {
        "tag": tag,
        "product_code": product_code,
        "customer_id": customer_id,
        "company_id": company_id,
        "job_id": job_id,
    }

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM payment_requests WHERE customer_id = :cid"), {"cid": customer_id})
        conn.execute(text("DELETE FROM customer_messages WHERE customer_id = :cid"), {"cid": customer_id})
        conn.execute(text("DELETE FROM applications WHERE customer_id = :cid"), {"cid": customer_id})
        conn.execute(text("DELETE FROM customers WHERE id = :cid"), {"cid": customer_id})
        conn.execute(text("DELETE FROM jobs WHERE id = :jid"), {"jid": job_id})
        conn.execute(text("DELETE FROM sources WHERE id = :sid"), {"sid": source_id})
        conn.execute(text("DELETE FROM companies WHERE id = :cid"), {"cid": company_id})
        conn.execute(text("DELETE FROM products WHERE code = :c"), {"c": product_code})


def _insert_payment_request(
    engine: Engine, *, customer_id: int, product_code: str, status: str, decided_at: datetime, expected_amount: float = 300.0
) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO payment_requests "
                "(customer_id, product_code, expected_amount, declared_amount, status, decided_at) "
                "VALUES (:cid, :code, :amt, :amt, :status, :decided) RETURNING id"
            ),
            {"cid": customer_id, "code": product_code, "amt": expected_amount, "status": status, "decided": decided_at},
        ).scalar_one()


def _insert_customer_message(
    engine: Engine, *, customer_id: int, category: str | None, msg_text: str, created_at: datetime
) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO customer_messages (customer_id, direction, text, category, created_at) "
                "VALUES (:cid, 'in', :txt, :cat, :created) RETURNING id"
            ),
            {"cid": customer_id, "txt": msg_text, "cat": category, "created": created_at},
        ).scalar_one()


def _insert_application(engine: Engine, *, customer_id: int, job_id: int, status: str, sent_at: datetime) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO applications (customer_id, job_id, status, sent_at) "
                "VALUES (:cid, :jid, :status, :sent) RETURNING id"
            ),
            {"cid": customer_id, "jid": job_id, "status": status, "sent": sent_at},
        ).scalar_one()


def test_build_summary_counts_only_confirmed_payment_requests_in_range(engine, ctx):
    in_range = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    _insert_payment_request(
        engine, customer_id=ctx["customer_id"], product_code=ctx["product_code"], status="confirmed", decided_at=in_range
    )
    # معلّق (لا يُحتسَب) — نفس الفترة بالضبط.
    _insert_payment_request(
        engine, customer_id=ctx["customer_id"], product_code=ctx["product_code"], status="pending", decided_at=in_range
    )
    payload = reports_summary.build_summary(engine, date(2026, 9, 1), date(2026, 9, 11))
    rows = payload["financial"]["rows"]
    assert len(rows) == 1
    assert rows[0]["product_code"] == ctx["product_code"]
    assert rows[0]["count"] == 1
    assert rows[0]["total"] == 300.0
    assert payload["financial"]["grand_total"] == 300.0


def test_build_summary_excludes_confirmed_payment_outside_range(engine, ctx):
    outside = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    _insert_payment_request(
        engine, customer_id=ctx["customer_id"], product_code=ctx["product_code"], status="confirmed", decided_at=outside
    )
    payload = reports_summary.build_summary(engine, date(2026, 9, 1), date(2026, 9, 11))
    assert payload["financial"]["rows"] == []
    assert payload["financial"]["grand_total"] == 0.0


def test_build_summary_uses_expected_amount_not_declared_amount(engine, ctx):
    """قرار تصميم موثّق بـdocstring reports_summary.py: المبلغ المعتمَد
    بالتقرير هو expected_amount (السعر الرسمي) حتى لو declared_amount مختلفًا
    (مثلاً خطأ كتابة من العميل عند تقديم الإيصال)."""
    in_range = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO payment_requests "
                "(customer_id, product_code, expected_amount, declared_amount, status, decided_at) "
                "VALUES (:cid, :code, 300.00, 250.00, 'confirmed', :decided)"
            ),
            {"cid": ctx["customer_id"], "code": ctx["product_code"], "decided": in_range},
        )
    payload = reports_summary.build_summary(engine, date(2026, 9, 1), date(2026, 9, 11))
    assert payload["financial"]["rows"][0]["total"] == 300.0


def test_build_summary_groups_categorized_inbound_messages_and_excludes_uncategorized(engine, ctx):
    in_range = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    _insert_customer_message(
        engine, customer_id=ctx["customer_id"], category="complaint", msg_text="تأخّر التقديم", created_at=in_range
    )
    _insert_customer_message(
        engine, customer_id=ctx["customer_id"], category="note", msg_text="ملاحظة عادية", created_at=in_range
    )
    # رسالة واردة بلا تصنيف (رد عادي، ليست من قناة "تواصل معنا") — لا تظهر بالتقرير.
    _insert_customer_message(
        engine, customer_id=ctx["customer_id"], category=None, msg_text="رد عادي", created_at=in_range
    )
    payload = reports_summary.build_summary(engine, date(2026, 9, 1), date(2026, 9, 11))
    messages = payload["messages"]
    assert len(messages["complaint"]) == 1
    assert messages["complaint"][0]["text"] == "تأخّر التقديم"
    assert len(messages["note"]) == 1
    assert messages["heart_to_heart"] == []
    all_texts = [m["text"] for cat in messages.values() for m in cat]
    assert "رد عادي" not in all_texts


def test_build_summary_send_and_bounce_stats_for_period(engine, ctx):
    in_range = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    outside = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    _insert_application(engine, customer_id=ctx["customer_id"], job_id=ctx["job_id"], status="sent", sent_at=in_range)
    _insert_application(engine, customer_id=ctx["customer_id"], job_id=ctx["job_id"], status="bounced", sent_at=in_range)
    _insert_application(engine, customer_id=ctx["customer_id"], job_id=ctx["job_id"], status="sent", sent_at=outside)
    payload = reports_summary.build_summary(engine, date(2026, 9, 1), date(2026, 9, 11))
    assert payload["stats"]["sends"] == 2  # كلا الصفّين بالفترة (sent + bounced يُحتسَبان معًا هنا)
    assert payload["stats"]["bounces"] == 1


def test_build_summary_swaps_reversed_date_range_defensively(engine, ctx):
    in_range = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    _insert_payment_request(
        engine, customer_id=ctx["customer_id"], product_code=ctx["product_code"], status="confirmed", decided_at=in_range
    )
    payload = reports_summary.build_summary(engine, date(2026, 9, 11), date(2026, 9, 1))
    assert payload["start_date"] == "2026-09-01"
    assert payload["end_date"] == "2026-09-11"
    assert payload["financial"]["grand_total"] == 300.0
