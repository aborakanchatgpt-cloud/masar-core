"""B4: جداول الإرسال والوارد (المرحلة 4 حسب الدليل §9): mail_links (صناديق
بريد العملاء المخصّصة، بيانات الاتصال + السرّ المشفّر)، send_queue (طابور
الإرسال المُجدوَل زمنيًا)، inbox_events (أحداث الوارد المصنَّفة: ارتداد/رد/
مقابلة/أخرى)، cv_variants (نسخ السيرة الذاتية المولَّدة لكل عميل/عائلة).

هذا الترحيل مملوك بالكامل لمنفّذ B4 — لا يلمس أي جدول من B1-B3 (customers,
profiles, jobs, opportunities, applications, company_cooldowns, ...) ولا
يعيد تعريف أي قيد عليها؛ يضيف فقط جداول جديدة مستقلة، بمفاتيح أجنبية للقراءة
فقط (customer_id → customers.id، opportunity_id → opportunities.id،
job_id → jobs.id).

Revision ID: 0005_b4_mail
Revises: 0004_b3_customers
Create Date: 2026-09-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

JSONB = postgresql.JSONB

revision: str = "0005_b4_mail"
down_revision: Union[str, None] = "0004_b3_customers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # mail_links — صندوق بريد Gmail مخصّص واحد لكل عميل (SMTP+IMAP)
    # -----------------------------------------------------------------
    op.create_table(
        "mail_links",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "customer_id",
            sa.BigInteger(),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("address", sa.String(length=255), nullable=False),
        sa.Column("smtp_host", sa.String(length=255), nullable=False, server_default="smtp.gmail.com"),
        sa.Column("smtp_port", sa.Integer(), nullable=False, server_default="587"),
        sa.Column("imap_host", sa.String(length=255), nullable=False, server_default="imap.gmail.com"),
        sa.Column("imap_port", sa.Integer(), nullable=False, server_default="993"),
        # secret_enc: كلمة مرور تطبيق Gmail مشفّرة (Fernet، core/app/mail_crypto.py)
        # — لا تُخزَّن أو تُسجَّل نصًا صريحًا أبدًا.
        sa.Column("secret_enc", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="unverified"),
        sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        # warmup_day: يُشتق فعليًا من created_at بمنطق pacing.ramp_cap()، هذا
        # العمود احتياطي/تشخيصي فقط (يُحدَّث إعلاميًا، لا يُقرأ من منطق الإحماء).
        sa.Column("warmup_day", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('unverified','ok','failed')", name="ck_mail_links_status"
        ),
    )
    op.create_unique_constraint("uq_mail_links_customer_id", "mail_links", ["customer_id"])
    op.create_index("ix_mail_links_status", "mail_links", ["status"])

    # -----------------------------------------------------------------
    # send_queue — طابور الإرسال المُجدوَل زمنيًا (send_after بتوقيت UTC
    # حقيقي، مُحوَّل من وقت رياض عبر pacing.riyadh_naive_to_utc)
    # -----------------------------------------------------------------
    op.create_table(
        "send_queue",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "customer_id",
            sa.BigInteger(),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "opportunity_id",
            sa.BigInteger(),
            sa.ForeignKey("opportunities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("job_id", sa.BigInteger(), sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("to_email", sa.String(length=255), nullable=False),
        sa.Column("cc_email", sa.String(length=255), nullable=True),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("attachments", JSONB(), nullable=False, server_default="[]"),
        sa.Column("send_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("message_id", sa.String(length=255), nullable=True),
        # synthetic: صفوف اختبار التحميل (load-test) — تُستبعد من أي أثر
        # جانبي حقيقي (applications/company_cooldowns/ledger) رغم مرورها
        # بمسار الإرسال الفعلي بالكامل (تُصرَّف للـsink دومًا).
        sa.Column("synthetic", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','sending','sent','failed','cancelled')", name="ck_send_queue_status"
        ),
    )
    op.create_index(
        "ix_send_queue_status_send_after_locked",
        "send_queue",
        ["status", "send_after", "locked_until"],
    )
    op.create_index("ix_send_queue_customer_send_after", "send_queue", ["customer_id", "send_after"])

    # -----------------------------------------------------------------
    # inbox_events — أحداث الوارد المصنَّفة لكل صندوق بريد
    # -----------------------------------------------------------------
    op.create_table(
        "inbox_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "customer_id",
            sa.BigInteger(),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("message_id", sa.String(length=255), nullable=False),
        sa.Column("in_reply_to", sa.String(length=255), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("from_addr", sa.String(length=255), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("raw_ref", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('bounce','reply','interview','other')", name="ck_inbox_events_kind"
        ),
    )
    op.create_unique_constraint(
        "uq_inbox_events_customer_message", "inbox_events", ["customer_id", "message_id"]
    )
    op.create_index("ix_inbox_events_customer_id", "inbox_events", ["customer_id"])

    # -----------------------------------------------------------------
    # cv_variants — نسخة سيرة ذاتية واحدة (قالب+ملفات) لكل (عميل، عائلة)
    # -----------------------------------------------------------------
    op.create_table(
        "cv_variants",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "customer_id",
            sa.BigInteger(),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("family", sa.String(length=50), nullable=False),
        sa.Column("template", sa.Integer(), nullable=False),
        sa.Column("html_path", sa.Text(), nullable=False),
        sa.Column("pdf_path", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("template >= 1 AND template <= 4", name="ck_cv_variants_template"),
    )
    op.create_unique_constraint("uq_cv_variants_customer_family", "cv_variants", ["customer_id", "family"])


def downgrade() -> None:
    op.drop_constraint("uq_cv_variants_customer_family", "cv_variants", type_="unique")
    op.drop_table("cv_variants")
    op.drop_index("ix_inbox_events_customer_id", table_name="inbox_events")
    op.drop_constraint("uq_inbox_events_customer_message", "inbox_events", type_="unique")
    op.drop_table("inbox_events")
    op.drop_index("ix_send_queue_customer_send_after", table_name="send_queue")
    op.drop_index("ix_send_queue_status_send_after_locked", table_name="send_queue")
    op.drop_table("send_queue")
    op.drop_index("ix_mail_links_status", table_name="mail_links")
    op.drop_constraint("uq_mail_links_customer_id", "mail_links", type_="unique")
    op.drop_table("mail_links")
