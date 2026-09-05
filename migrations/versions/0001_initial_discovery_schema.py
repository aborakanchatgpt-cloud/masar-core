"""جداول المرحلة 2: الاكتشاف والمعجم (companies, sources, jobs, taxonomy_terms, metrics_hourly)

Revision ID: 0001_initial_discovery_schema
Revises:
Create Date: 2026-09-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial_discovery_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("career_page_url", sa.Text(), nullable=True),
        sa.Column("ats_system", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_companies_name", "companies", ["name"])

    op.create_table(
        "sources",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", sa.String(length=30), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("avg_per_day", sa.Float(), nullable=False, server_default="0"),
        sa.Column("last_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_zero_rounds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sources_company_id", "sources", ["company_id"])
    op.create_index("ix_sources_enabled", "sources", ["enabled"])

    op.create_table(
        "taxonomy_terms",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("family", sa.String(length=50), nullable=False),
        sa.Column("term", sa.String(length=255), nullable=False),
        sa.Column("lang", sa.String(length=5), nullable=False),
        sa.Column("excluded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_taxonomy_terms_family", "taxonomy_terms", ["family"])
    op.create_unique_constraint("uq_taxonomy_term_family_lang", "taxonomy_terms", ["family", "term", "lang"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.BigInteger(), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("family", sa.String(length=50), nullable=True),
        sa.Column("dedup_key", sa.String(length=600), nullable=False),
        sa.Column("raw_json", sa.JSON(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_jobs_dedup_key", "jobs", ["dedup_key"])
    op.create_index("ix_jobs_company_id", "jobs", ["company_id"])
    op.create_index("ix_jobs_family", "jobs", ["family"])
    op.create_index("ix_jobs_first_seen_at", "jobs", ["first_seen_at"])

    op.create_table(
        "metrics_hourly",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.BigInteger(), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("hour_bucket", sa.DateTime(timezone=True), nullable=False),
        sa.Column("jobs_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_metrics_hourly_source_bucket", "metrics_hourly", ["source_id", "hour_bucket"])


def downgrade() -> None:
    op.drop_table("metrics_hourly")
    op.drop_table("jobs")
    op.drop_table("taxonomy_terms")
    op.drop_table("sources")
    op.drop_table("companies")
