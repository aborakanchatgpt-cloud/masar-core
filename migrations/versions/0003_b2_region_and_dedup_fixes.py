"""B2 مراجعة (R3/R4/R5): أعمدة تحديد المنطقة الجغرافية (خليجي/غير
خليجي) على jobs، وأعمدة تتبّع مطابقة الخليج لكل مصدر (saudi_hits، عدّاد
الجولات الصفرية المتتالية، ونوع فلترة المنطقة) على sources — تُستخدم لاستبعاد
الوظائف غير الخليجية من الإحصاءات/العيّنات، وتعطيل تلقائي للمصادر التي لا
تُنتج أي وظيفة خليجية عبر جولتين متتاليتين.

Revision ID: 0003_b2_region_and_dedup_fixes
Revises: 0002_b2_discovery_extend
Create Date: 2026-09-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_b2_region_and_dedup_fixes"
down_revision: Union[str, None] = "0002_b2_discovery_extend"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # jobs: رمز الدولة المستنتج (ISO حرفين، قد يكون أي دولة لا الخليج فقط)
    # وعلم "خارج النطاق" (ليست SA/AE/QA/KW/BH/OM ولا عن بُعد بالخليج صراحة).
    # القيمة الافتراضية true عمدًا (وليس false) للأعمدة الموجودة مسبقًا: كل
    # الوظائف الحالية (6568) لم تُصنّف بعد فعليًا، والمراجعة أثبتت أن الأغلبية الساحقة
    # منها خارج نطاق السعودية/الخليج — الافتراض الآمن هو "خارج النطاق" حتى يُعاد حسابها
    # فعليًا عبر POST /admin/discovery/backfill-locations (بدل افتراض false الذي كان سيدخل
    # كل الصفوف القديمة زورًا كـ"داخل النطاق").
    op.add_column("jobs", sa.Column("country_code", sa.String(length=4), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("out_of_region", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_jobs_out_of_region", "jobs", ["out_of_region"])
    op.create_index("ix_jobs_country_code", "jobs", ["country_code"])

    # sources: نوع فلترة المنطقة لكل مصدر (افتراضيًا "gcc" — يُستبعد لاحقًا أي
    # مصدر بلا وظائف خليجية عبر جولتين)، وعدد الوظائف الخليجية بآخر جلب،
    # وعدّاد الجولات المتتالية بلا أي وظيفة خليجية (لتعطيل تلقائي بـ'no_gcc_jobs').
    op.add_column(
        "sources",
        sa.Column("region_filter", sa.String(length=20), nullable=False, server_default="gcc"),
    )
    op.add_column("sources", sa.Column("saudi_hits", sa.Integer(), nullable=False, server_default="0"))
    op.add_column(
        "sources",
        sa.Column("consecutive_zero_gcc_rounds", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("sources", "consecutive_zero_gcc_rounds")
    op.drop_column("sources", "saudi_hits")
    op.drop_column("sources", "region_filter")
    op.drop_index("ix_jobs_country_code", table_name="jobs")
    op.drop_index("ix_jobs_out_of_region", table_name="jobs")
    op.drop_column("jobs", "out_of_region")
    op.drop_column("jobs", "country_code")
