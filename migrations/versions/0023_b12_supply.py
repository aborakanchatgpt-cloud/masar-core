"""B12: العرض — مصادر وظائف إضافية + دليل شركات + التقديم المبادر. راجع
claude/masar_build_brief_v4_2026-09-12.md قسم 2 (الوكيل B: B12.1-B12.8)
لتفصيل القرارات الكاملة.

    1. company_directory — دليل شركات سعودية حقيقية ببريد توظيف متحقَّق
       يدويًا (WebFetch لصفحة المصدر الرسمية، موثّق بـemail_source_url —
       لا اختلاق). يغذّي مسارين: (أ) التقديم المبادر بـplanner.py (B12.3)
       حين لا تكفي الوظائف المعلنة لبلوغ الهدف اليومي، (ب) عمود
       companies.careers_email أدناه (نفس الصفّ يُنسخ لصفّ companies مطابق
       عبر core/app/company_directory.py:ensure_company_and_job، idempotent).
       فهرس فريد على lower(hr_email) يمنع تكرار نفس بريد التوظيف بأكثر من
       صفّ (قد تتكرر الشركة باسمين مختلفين قليلًا لكن بريدها التوظيفي واحد
       عمليًّا في كل الحالات المتحقَّق منها لهذه الدفعة).
    2. companies.careers_email — عمود كان *مذكورًا كـplaceholder جاهز* أصلًا
       بـcore/app/apply_email.py:company_directory_email (غير مُعدّل هنا —
       ملف غير مملوك لهذه الدفعة، راجع القسم 3 بالدليل) الذي يقرأ
       `company_row.get("careers_email")` منذ B4 لكنه كان يرجع None دومًا
       لغياب هذا العمود بالضبط. إضافته هنا (لا تعديل أي سطر بـapply_email.py)
       تُفعّل تلك الأولوية الثالثة تلقائيًا لكل صفّ jobs (بما فيها الوظائف
       المعلنة العادية) يحمل company_id مطابقًا لشركة بدليلنا — لا فقط
       التقديم المبادر.
    3. opportunities.speculative (BOOLEAN NOT NULL DEFAULT false) — يُميّز
       الفرص المُنشأة من التقديم المبادر (B12.3) عن الفرص العادية المبنية
       على وظيفة معلنة فعليًا. الفرصة المبادرة لا تزال تُشير لصفّ jobs حقيقي
       (job_id يبقى NOT NULL بلا تغيير بنيوي — راجع
       core/app/company_directory.py:ensure_company_and_job) وهو صفّ اصطناعي
       واحد لكل شركة بالدليل (لا لكل عميل)، لا صفّ لكل عميل — فقط عمود
       `speculative` بجدول opportunities (لا jobs) يحمل علامة العميل لهذه
       الفرصة تحديدًا لأن نفس صفّ jobs الاصطناعي قد يُخطَّط لعدة عملاء.
    4. customers.speculative_enabled (BOOLEAN NOT NULL DEFAULT true) — تبديل
       عميل فردي لإيقاف التقديم المبادر (B12.6، بوت العملاء). الافتراضي true
       يطابق نص الشروط الجديد بالدليل P0.4 ("قد نقدّم لك مبادرةً... تستطيع
       إيقاف هذا الأسلوب في أي وقت").

Revision ID: 0023_b12_supply
Revises: 0022_b11_money
Create Date: 2026-09-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0023_b12_supply"
down_revision: Union[str, None] = "0022_b11_money"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # 1. company_directory
    # -----------------------------------------------------------------
    op.create_table(
        "company_directory",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("name_ar", sa.String(length=255), nullable=True),
        sa.Column("sector_family", sa.String(length=50), nullable=False),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("hr_email", sa.String(length=255), nullable=False),
        sa.Column("email_source_url", sa.Text(), nullable=False),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("verified_at", sa.Date(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("companies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("job_id", sa.BigInteger(), sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "uq_company_directory_hr_email_lower",
        "company_directory",
        [sa.text("lower(hr_email)")],
        unique=True,
    )
    op.create_index(
        "ix_company_directory_family_city_active",
        "company_directory",
        ["sector_family", "city", "active"],
    )

    # -----------------------------------------------------------------
    # 2. companies.careers_email — يُفعّل placeholder موجود أصلًا بـ
    #    app.apply_email.company_directory_email بلا تعديل ذلك الملف.
    # -----------------------------------------------------------------
    op.add_column("companies", sa.Column("careers_email", sa.String(length=255), nullable=True))

    # -----------------------------------------------------------------
    # 3+4. أعلام التقديم المبادر
    # -----------------------------------------------------------------
    op.add_column(
        "opportunities",
        sa.Column("speculative", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_opportunities_speculative", "opportunities", ["speculative"])
    op.add_column(
        "customers",
        sa.Column("speculative_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("customers", "speculative_enabled")
    op.drop_index("ix_opportunities_speculative", table_name="opportunities")
    op.drop_column("opportunities", "speculative")
    op.drop_column("companies", "careers_email")
    op.drop_index("ix_company_directory_family_city_active", table_name="company_directory")
    op.drop_index("uq_company_directory_hr_email_lower", table_name="company_directory")
    op.drop_table("company_directory")
