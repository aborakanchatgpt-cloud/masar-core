"""
Masar Core — لوحة أرقام الأدمن المجمّعة (B5a البند 4).

    GET /admin/overview  → استعلام واحد لكل قسم، كلها مفهرسة (تعليق كل قسم
                            يذكر الفهرس المستخدَم — معيار التوسّع 1,500 عميل).

B9/B5: أضيفت 4 حقول لخدمة "📊 نظرة عامة" المعاد تنسيقها ببوت الأدمن (الدليل
§B5): `sends_failed_today` (send_queue.status='failed' اليوم)،
`pending_payment_requests` (جدول B0 — يبقى 0 حتى يبدأ B3 الكتابة إليه)،
`top_families`/`top_cities` (أعلى 5 — نفس استعلام `_reply_segments` القديم
ببوت الأدمن لكن Limit 5 لا 10، مُوحَّد هنا الآن كمصدر واحد بدل تكراره
بملفين). كل الحقول القديمة بلا تغيير — إضافة بحتة، لا كسر لأي مستهلك حالي.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text

from app import sender
from app.auth import require_admin_token
from app.discovery import get_engine

router = APIRouter(prefix="/admin", tags=["overview"], dependencies=[Depends(require_admin_token)])


@router.get("/overview")
async def overview() -> dict:
    engine = get_engine()
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)

    with engine.connect() as conn:
        # ix_customers_status
        customers_by_status = dict(
            conn.execute(text("SELECT status, count(*) FROM customers GROUP BY status")).all()
        )

        # ix_applications_status_sent_at (ترحيل 0008)
        sends_today = conn.execute(
            text("SELECT count(*) FROM applications WHERE sent_at >= :start"), {"start": today_start}
        ).scalar()
        sends_last_7d = conn.execute(
            text("SELECT count(*) FROM applications WHERE sent_at >= :start"), {"start": week_ago}
        ).scalar()
        bounces_today = conn.execute(
            text("SELECT count(*) FROM applications WHERE status = 'bounced' AND sent_at >= :start"),
            {"start": today_start},
        ).scalar()

        # ix_send_queue_status_send_after_locked (العمود الأول status)
        queue_by_status = dict(
            conn.execute(
                text("SELECT status, count(*) FROM send_queue WHERE synthetic = false GROUP BY status")
            ).all()
        )

        # ix_mail_links_status
        mail_links_by_status = dict(
            conn.execute(text("SELECT status, count(*) FROM mail_links GROUP BY status")).all()
        )

        # ix_daily_reports_status (ترحيل 0008)
        pending_reports = conn.execute(
            text("SELECT count(*) FROM daily_reports WHERE status = 'queued'")
        ).scalar()

        # ix_guarantee_ledger_status (ترحيل 0008)
        pending_guarantees = conn.execute(
            text("SELECT count(*) FROM guarantee_ledger WHERE status = 'refund_pending'")
        ).scalar()

        # ix_send_queue_status_send_after_locked (B9/B5) — فشل اليوم تحديدًا
        # (لا كل الفشل التاريخي بـsend_queue_by_status أعلاه).
        sends_failed_today = conn.execute(
            text("SELECT count(*) FROM send_queue WHERE status = 'failed' AND created_at >= :start"),
            {"start": today_start},
        ).scalar()

        # ix_payment_requests_status (ترحيل 0017) — يبقى 0 حتى B3.
        pending_payment_requests = conn.execute(
            text("SELECT count(*) FROM payment_requests WHERE status = 'pending'")
        ).scalar()

        # أعلى 5 مجالات/مدن — نفس منطق segments القديم، Limit 5 بدل 10.
        top_families = conn.execute(
            text(
                """
                SELECT elem AS family, count(*) AS n
                FROM customers, LATERAL jsonb_array_elements_text(families) AS elem
                GROUP BY elem ORDER BY n DESC LIMIT 5
                """
            )
        ).all()
        top_cities = conn.execute(
            text(
                """
                SELECT elem AS city, count(*) AS n
                FROM customers, LATERAL jsonb_array_elements_text(cities) AS elem
                GROUP BY elem ORDER BY n DESC LIMIT 5
                """
            )
        ).all()

        # ix_jobs_first_seen_at (ترحيل 0001) — MAX عبر فحص فهرس فقط، بلا
        # مسح جدول jobs الكامل (قد يبلغ عشرات الآلاف من الصفوف).
        latest_job_discovered_at = conn.execute(text("SELECT max(first_seen_at) FROM jobs")).scalar()

    return {
        "generated_at": now.isoformat(),
        "customers_by_status": customers_by_status,
        "sends_today": int(sends_today or 0),
        "sends_last_7_days": int(sends_last_7d or 0),
        "bounces_today": int(bounces_today or 0),
        "send_queue_by_status": queue_by_status,
        "mail_links_by_status": mail_links_by_status,
        "pending_reports": int(pending_reports or 0),
        "pending_guarantees": int(pending_guarantees or 0),
        "sends_failed_today": int(sends_failed_today or 0),
        "pending_payment_requests": int(pending_payment_requests or 0),
        "top_families": [[row[0], row[1]] for row in top_families],
        "top_cities": [[row[0], row[1]] for row in top_cities],
        "discovery_freshness": {
            "latest_job_discovered_at": latest_job_discovered_at.isoformat() if latest_job_discovered_at else None,
        },
        "dry_run": sender.is_dry_run(),
    }
