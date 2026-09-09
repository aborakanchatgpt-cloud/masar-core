"""B5a: تقارير العميل اليومية، التغذية الراجعة (استبعاد شركات)، دفتر
الضمان/التعويض، وربط بريد بذاتي الخدمة (link_tokens) — المرحلة 5 حسب
docs/EXECUTION_GUIDE.md §3.12/§3.13 وPLAN.md بند B5.

هذا الترحيل مملوك بالكامل لمنفّذ B5a — لا يلمس أي جدول من B1-B4 (jobs,
companies, sources, customers[عدا إضافة عمود واحد أدناه], profiles, mail_links,
send_queue, applications, opportunities, ...) ولا يعيد تعريف أي قيد عليها.
يضيف فقط:
    1. daily_reports — صف تقرير واحد لكل (عميل، تاريخ).
    2. application_feedback — 👎/🎉 على تقديم محدَّد (send_queue_id).
    3. customer_company_exclusions — شركة استبعدها العميل (من 👎)، يقرأها
       send_builder.py قبل بناء الطابور.
    4. guarantee_ledger — تقييم دوري لكل (عميل، بداية فترة الاشتراك):
       الهدف/المُحتسَب/المرتد/العجز/أيام التمديد/مبلغ التعويض.
    5. link_tokens — رمز مرّة واحدة (48 ساعة) لصفحة ربط البريد الذاتي
       (GET/POST /link/{token}) — لا كلمة مرور ولا أي سرّ عميل يُخزَّن هنا،
       فقط hash للرمز نفسه (sha256) وحالة الاستخدام/المحاولات.
    6. customers.price_sar — عمود اختياري (nullable) لسعر الاشتراك الشهري
       بالريال؛ يُستخدَم بحساب التعويض التناسبي (سعر الاشتراك ÷ 510 لكل
       تقديم ناقص). NULL يعني لم يُحدَّد السعر بعد — الأدمن (أحمد) يملؤه
       يدويًا لاحقًا؛ guarantee.py يتعامل معه كـ"يحتاج قرار المالك" (لا يخمّن
       رقمًا أبدًا).
    7. فهارس أداء إضافية على applications (status, sent_at) و
       (customer_id, status, sent_at) — تخدم استعلامات lوحة الأدمن
       (/admin/overview: "المرسَل اليوم/آخر 7 أيام"، "المرتد اليوم") ومنطق
       الضمان (guarantee.evaluate_period: عدّاد المُحتسَب/المرتد لكل عميل
       بفترته) بلا مسح كامل للجدول — ضروري لمعيار التوسّع (1,500 عميل).

Revision ID: 0008_b5_reports
Revises: 0007_mail_link_skip_verify
Create Date: 2026-09-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

JSONB = postgresql.JSONB

revision: str = "0008_b5_reports"
down_revision: Union[str, None] = "0007_mail_link_skip_verify"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # customers.price_sar — سعر الاشتراك الشهري (اختياري، يملؤه الأدمن)
    # -----------------------------------------------------------------
    op.add_column(
        "customers",
        sa.Column("price_sar", sa.Numeric(10, 2), nullable=True),
    )

    # -----------------------------------------------------------------
    # daily_reports
    # -----------------------------------------------------------------
    op.create_table(
        "daily_reports",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "customer_id",
            sa.BigInteger(),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("channel", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','delivered','failed')", name="ck_daily_reports_status"
        ),
    )
    op.create_unique_constraint(
        "uq_daily_reports_customer_date", "daily_reports", ["customer_id", "report_date"]
    )
    op.create_index("ix_daily_reports_status", "daily_reports", ["status"])

    # -----------------------------------------------------------------
    # application_feedback — 👎/🎉 على send_queue_id محدَّد
    # -----------------------------------------------------------------
    op.create_table(
        "application_feedback",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "customer_id",
            sa.BigInteger(),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "send_queue_id",
            sa.BigInteger(),
            sa.ForeignKey("send_queue.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('thumbs_down','celebrate')", name="ck_application_feedback_kind"),
    )
    op.create_unique_constraint(
        "uq_application_feedback_customer_queue_kind",
        "application_feedback",
        ["customer_id", "send_queue_id", "kind"],
    )

    # -----------------------------------------------------------------
    # customer_company_exclusions — يقرأها send_builder._fetch_candidate_opportunities
    # -----------------------------------------------------------------
    op.create_table(
        "customer_company_exclusions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "customer_id",
            sa.BigInteger(),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            sa.BigInteger(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_customer_company_exclusions_customer_company",
        "customer_company_exclusions",
        ["customer_id", "company_id"],
    )

    # -----------------------------------------------------------------
    # guarantee_ledger
    # -----------------------------------------------------------------
    op.create_table(
        "guarantee_ledger",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "customer_id",
            sa.BigInteger(),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("target", sa.Integer(), nullable=False, server_default="510"),
        sa.Column("counted_sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bounced", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shortfall", sa.Integer(), nullable=True),
        sa.Column("extension_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("refund_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="computed"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('computed','extended','refund_pending','settled','na')",
            name="ck_guarantee_ledger_status",
        ),
    )
    op.create_unique_constraint(
        "uq_guarantee_ledger_customer_period", "guarantee_ledger", ["customer_id", "period_start"]
    )
    op.create_index("ix_guarantee_ledger_status", "guarantee_ledger", ["status"])

    # -----------------------------------------------------------------
    # link_tokens — رمز مرّة واحدة لصفحة ربط البريد الذاتي (48 ساعة)؛ لا
    # يُخزَّن الرمز الخام أبدًا، فقط sha256(token) — نفس فلسفة mail_links.secret_enc
    # (لا سرّ عميل نصًا صريحًا)، وإن كان هذا "سرًّا مؤقتًا للرابط" لا كلمة
    # مرور بريد فعلية.
    # -----------------------------------------------------------------
    op.create_table(
        "link_tokens",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "customer_id",
            sa.BigInteger(),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_link_tokens_token_hash", "link_tokens", ["token_hash"])
    op.create_index("ix_link_tokens_customer_id", "link_tokens", ["customer_id"])

    # -----------------------------------------------------------------
    # فهارس أداء على applications — تخدم /admin/overview وguarantee.py
    # (راجع تعليق الرأس، البند 7) — لا تعديل على أي عمود موجود.
    # -----------------------------------------------------------------
    op.create_index("ix_applications_status_sent_at", "applications", ["status", "sent_at"])
    op.create_index(
        "ix_applications_customer_status_sent_at", "applications", ["customer_id", "status", "sent_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_applications_customer_status_sent_at", table_name="applications")
    op.drop_index("ix_applications_status_sent_at", table_name="applications")

    op.drop_index("ix_link_tokens_customer_id", table_name="link_tokens")
    op.drop_constraint("uq_link_tokens_token_hash", "link_tokens", type_="unique")
    op.drop_table("link_tokens")

    op.drop_index("ix_guarantee_ledger_status", table_name="guarantee_ledger")
    op.drop_constraint("uq_guarantee_ledger_customer_period", "guarantee_ledger", type_="unique")
    op.drop_table("guarantee_ledger")

    op.drop_constraint(
        "uq_customer_company_exclusions_customer_company", "customer_company_exclusions", type_="unique"
    )
    op.drop_table("customer_company_exclusions")

    op.drop_constraint(
        "uq_application_feedback_customer_queue_kind", "application_feedback", type_="unique"
    )
    op.drop_table("application_feedback")

    op.drop_index("ix_daily_reports_status", table_name="daily_reports")
    op.drop_constraint("uq_daily_reports_customer_date", "daily_reports", type_="unique")
    op.drop_table("daily_reports")

    op.drop_column("customers", "price_sar")
