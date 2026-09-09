"""B6: تصليب الحمل لـ1,500 عميل (25,500 صفّ send_queue = 1,500×17) — فهارس
أداء + أعمدة مراقبة/تقسيم لا تغيّر أي سلوك قائم (كل عمود جديد nullable أو
بقيمة افتراضية آمنة، وكل فهرس إضافي بحت).

هذا الترحيل مملوك بالكامل لمنفّذ B6 — لا يلمس أي جدول من B1-B5 (customers,
profiles, jobs, opportunities, applications[عدا فهرس جديد], company_cooldowns,
mail_links[إضافة أعمدة فقط], send_queue[إضافة عمود + فهرس فقط], daily_reports,
guarantee_ledger, ...) ولا يعيد تعريف أي قيد عليها.

المحتوى (كل بند مُبرَّر باستعلام ساخن فعلي — راجع docs/reports/B6-executor.md
لدليل EXPLAIN قبل/بعد):

1. send_queue.completed_at (timestamptz, nullable) — يُضبط في
   sender._mark_success/_mark_already_sent/_mark_failure (نهائي فقط) — يمكّن
   مقاييس "أُرسل/دقيقة" و"عمر أقدم صفّ بالطابور" بدقة، بما فيها صفوف
   synthetic (اختبار التحميل) التي لا تُنشئ applications إطلاقًا فيستحيل
   قياسها من هناك.
2. send_queue: فهرس جزئي `ix_send_queue_claim_partial` على (send_after,
   locked_until) بشرط `status IN ('queued','sending')` — يطابق تمامًا شرط
   WHERE الحقيقي لـsender._claim_due_batch (نفس القيمتين معًا)، بدل الفهرس
   المركّب القائم (status, send_after, locked_until) الذي يضع status عمودًا
   أول رغم أنه IN بقيمتين لا مساواة واحدة.
3. send_queue: فهرس جزئي `ix_send_queue_sent_completed_at` على (completed_at)
   بشرط `status = 'sent'` — يخدم "أُرسل بآخر دقيقة/5 دقائق" بمسح صغير محصور
   بدل مسح كل send_queue أو الفهرس الحالي (status, send_after, locked_until)
   غير المفيد لفرز completed_at.
4. applications: فهرس `ix_applications_sent_at_company_key_customer` على
   (sent_at, company_key, customer_id) بشرط `company_key IS NOT NULL` — يخدم
   send_builder.fetch_cooldown_pairs وfetch_weekly_cap_companies وplanner
   بنفس الاسم (كلاهما: `WHERE sent_at >= :cutoff AND company_key IS NOT NULL`،
   الثاني يُجمِّع GROUP BY company_key HAVING count(DISTINCT customer_id)`) —
   لا فهرس حالي يقود بـsent_at (الفهارس القائمة تقود بـcompany_key أو status).
5. company_cooldowns: فهرس `ix_company_cooldowns_last_sent_at` على
   (last_sent_at) — نفس دوري fetch_cooldown_pairs أعلاه؛ الجدول كان بلا أي
   فهرس غير مفتاحه الأساسي (company_key, customer_id) الذي لا يخدم فلترة
   last_sent_at.
6. opportunities: فهرس جزئي `ix_opportunities_customer_planned_score` على
   (customer_id, planned_for, score DESC) بشرط `status = 'planned'` — يخدم
   send_builder._fetch_candidate_opportunities (customer_id=..., status=
   'planned', planned_for<=:today, ORDER BY score DESC) بمسح فهرس واحد بدل
   فهرس (customer_id, planned_for) القائم + فرز score خارجي.
7. mail_links: ثلاثة أعمدة لقارئ الوارد (B6 البند 5، الدليل §3.11):
   - `last_uid` (integer, nullable): أعلى UID IMAP عولج فعليًا لهذا الصندوق
     — يُستبدَل بحث "UNSEEN" (لا يتقدّم أبدًا لأن القراءة readonly=True لا
     تُعلّم أي رسالة كمقروءة — كل تكة كانت ستُعيد جلب كل الوارد التاريخي غير
     المقروء من جديد) ببحث "UID <last_uid+1>:*" التزايدي الحقيقي.
   - `error_count` (integer, not null, default 0): عدّاد أخطاء IMAP متتالية
     لهذا الصندوق (يُصفَّر عند أي نجاح).
   - `next_check_at` (timestamptz, nullable): تراجع (backoff) — صندوق أخطأ
     N مرة متتالية يُؤجَّل حتى هذا الوقت، فلا يُعاد الاتصال به كل تكة (15
     دقيقة) بلا جدوى (الدليل، بند B6 5: "skip links in error state with
     backoff").
   فهرس جزئي `ix_mail_links_status_next_check` على (next_check_at) بشرط
   `status = 'ok'` — يخدم استعلام دورة الوارد الجديد (partition + backoff
   معًا، القسم `run_inbox_round`).

كل الفهارس أُنشئت بصيغة `CREATE INDEX` عادية (لا CONCURRENTLY): بيانات
الإنتاج الحالية صغيرة جدًا (send_queue/applications/opportunities كلها
بعشرات الصفوف فقط وقت كتابة هذا الترحيل — راجع pg_stat_user_tables
بالتقرير)، وتدفق النشر يوقف core-scheduler أثناء alembic upgrade head
(autodeploy.sh/run_queue.sh migrate) فلا تنافس على القفل مع دورة إرسال حيّة
فعلية إطلاقًا.

Revision ID: 0011_b6_load
Revises: 0010_b5c_gaps
Create Date: 2026-09-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011_b6_load"
down_revision: Union[str, None] = "0010_b5c_gaps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # 1. send_queue.completed_at + فهارس أداء الطابور
    # -----------------------------------------------------------------
    op.add_column("send_queue", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))

    op.create_index(
        "ix_send_queue_claim_partial",
        "send_queue",
        ["send_after", "locked_until"],
        postgresql_where=sa.text("status IN ('queued', 'sending')"),
    )
    op.create_index(
        "ix_send_queue_sent_completed_at",
        "send_queue",
        ["completed_at"],
        postgresql_where=sa.text("status = 'sent'"),
    )

    # -----------------------------------------------------------------
    # 4-5. تبريد الشركة 60 يومًا + سقف 3 عملاء/أسبوع — applications + company_cooldowns
    # -----------------------------------------------------------------
    op.create_index(
        "ix_applications_sent_at_company_key_customer",
        "applications",
        ["sent_at", "company_key", "customer_id"],
        postgresql_where=sa.text("company_key IS NOT NULL"),
    )
    op.create_index(
        "ix_company_cooldowns_last_sent_at",
        "company_cooldowns",
        ["last_sent_at"],
    )

    # -----------------------------------------------------------------
    # 6. مرشِّح send_builder لفرص اليوم المخطَّطة
    # -----------------------------------------------------------------
    op.create_index(
        "ix_opportunities_customer_planned_score",
        "opportunities",
        ["customer_id", "planned_for", sa.text("score DESC")],
        postgresql_where=sa.text("status = 'planned'"),
    )

    # -----------------------------------------------------------------
    # 7. mail_links — تقسيم/تراجع قارئ الوارد + متابعة UID تزايدية
    # -----------------------------------------------------------------
    op.add_column("mail_links", sa.Column("last_uid", sa.Integer(), nullable=True))
    op.add_column("mail_links", sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("mail_links", sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_mail_links_status_next_check",
        "mail_links",
        ["next_check_at"],
        postgresql_where=sa.text("status = 'ok'"),
    )


def downgrade() -> None:
    op.drop_index("ix_mail_links_status_next_check", table_name="mail_links")
    op.drop_column("mail_links", "next_check_at")
    op.drop_column("mail_links", "error_count")
    op.drop_column("mail_links", "last_uid")

    op.drop_index("ix_opportunities_customer_planned_score", table_name="opportunities")

    op.drop_index("ix_company_cooldowns_last_sent_at", table_name="company_cooldowns")
    op.drop_index("ix_applications_sent_at_company_key_customer", table_name="applications")

    op.drop_index("ix_send_queue_sent_completed_at", table_name="send_queue")
    op.drop_index("ix_send_queue_claim_partial", table_name="send_queue")
    op.drop_column("send_queue", "completed_at")
