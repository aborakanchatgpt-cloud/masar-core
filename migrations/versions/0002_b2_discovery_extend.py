"""B2: تمديد جداول الاكتشاف — أعمدة صحة المصدر، حقول المطبّع الكاملة على jobs،
جدول مقاييس العائلات لكل ساعة، وجدول سجلّ جولات الجامع.

Revision ID: 0002_b2_discovery_extend
Revises: 0001_initial_discovery_schema
Create Date: 2026-09-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_b2_discovery_extend"
down_revision: Union[str, None] = "0001_initial_discovery_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # companies: بلد الشركة (يُملأ من data/sources_seed.csv عند البذر)
    op.add_column("companies", sa.Column("country", sa.String(length=80), nullable=True))
    op.create_unique_constraint("uq_companies_name", "companies", ["name"])

    # sources: صحة المصدر (الدليل: last_ok_at/last_error/disabled_reason)
    # + terms_note (ملاحظة الامتثال المسجّلة عند إضافة كل مصدر) + فرادة الرابط
    # (يسمح بالبذر المتكرر idempotent عبر ON CONFLICT (source_url)).
    op.add_column("sources", sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sources", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("sources", sa.Column("disabled_reason", sa.Text(), nullable=True))
    op.add_column("sources", sa.Column("terms_note", sa.Text(), nullable=True))
    op.create_unique_constraint("uq_sources_source_url", "sources", ["source_url"])

    # jobs: الحقول المستخرجة كاملة (سنوات/أقدمية/جنسية/مدينة/مهارات) حتى
    # يستطيع المخطّط (المرحلة 3) وعينة الجودة (/admin/quality-sample) قراءتها
    # مباشرة بدل إعادة استخراجها من raw_json في كل مرة.
    op.add_column("jobs", sa.Column("company_name", sa.String(length=255), nullable=True))
    op.add_column("jobs", sa.Column("city", sa.String(length=120), nullable=True))
    op.add_column("jobs", sa.Column("years_min", sa.Integer(), nullable=True))
    op.add_column("jobs", sa.Column("seniority", sa.String(length=30), nullable=True))
    op.add_column("jobs", sa.Column("saudi_only", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("jobs", sa.Column("skills", sa.JSON(), nullable=True))
    op.add_column("jobs", sa.Column("apply_mode", sa.String(length=20), nullable=True))
    op.add_column("jobs", sa.Column("description_snippet", sa.Text(), nullable=True))
    op.create_index("ix_jobs_city", "jobs", ["city"])

    # مقاييس الوظائف الجديدة لكل عائلة مهنية لكل ساعة (يُستخدم في
    # /admin/stats → jobs_new_24h_by_family وفي لوحة أرقام الأدمن لاحقًا).
    op.create_table(
        "metrics_family_hourly",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("family", sa.String(length=50), nullable=False),
        sa.Column("hour_bucket", sa.DateTime(timezone=True), nullable=False),
        sa.Column("jobs_new", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_metrics_family_hourly", "metrics_family_hourly", ["family", "hour_bucket"]
    )

    # سجلّ جولات الجامع — يقرأه /admin/stats لعرض last_round_at/last_round_seconds
    # بلا اعتماد على حالة داخل عملية واحدة (قد تعمل عدة نسخ uvicorn).
    op.create_table(
        "discovery_rounds",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sources_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sources_ok", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sources_error", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jobs_fetched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jobs_new", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_discovery_rounds_started_at", "discovery_rounds", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_discovery_rounds_started_at", table_name="discovery_rounds")
    op.drop_table("discovery_rounds")
    op.drop_constraint("uq_metrics_family_hourly", "metrics_family_hourly", type_="unique")
    op.drop_table("metrics_family_hourly")
    op.drop_index("ix_jobs_city", table_name="jobs")
    op.drop_column("jobs", "description_snippet")
    op.drop_column("jobs", "apply_mode")
    op.drop_column("jobs", "skills")
    op.drop_column("jobs", "saudi_only")
    op.drop_column("jobs", "seniority")
    op.drop_column("jobs", "years_min")
    op.drop_column("jobs", "city")
    op.drop_column("jobs", "company_name")
    op.drop_constraint("uq_sources_source_url", "sources", type_="unique")
    op.drop_column("sources", "terms_note")
    op.drop_column("sources", "disabled_reason")
    op.drop_column("sources", "last_error")
    op.drop_column("sources", "last_ok_at")
    op.drop_constraint("uq_companies_name", "companies", type_="unique")
    op.drop_column("companies", "country")
