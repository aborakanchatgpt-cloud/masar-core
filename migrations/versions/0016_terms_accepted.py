"""B9/A5: عمود موافقة العميل على الشروط (بنية فقط بهذه الدفعة).

customers.terms_accepted_at TIMESTAMPTZ NULL — يُسجَّل وقت موافقة العميل
على شروط الخدمة (docs/TERMS_AR.md، صفحة عرضها GET /terms بـapp/link_api.py
بلا مصادقة). NULL افتراضيًا لكل الصفوف الحالية والجديدة.

قرار المنتج (11 سبتمبر 2026، نهائي): خطوة الموافقة الفعلية *لا* تُضاف
لتدفّق onboarding الحالي بهذه الدفعة — موضعها الصحيح بعد اختيار الباقة
وقبل الدفع، تُنفَّذ بـB3 (تدفّق الباقات والدفع) لاحقًا. هذه الترحيلة بنية
تحتية فقط: العمود موجود وجاهز، بلا أي منطق يكتب إليه بعد.

Revision ID: 0016_terms_accepted
Revises: 0015_canonical_phone
Create Date: 2026-09-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0016_terms_accepted"
down_revision: Union[str, None] = "0015_canonical_phone"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "customers",
        sa.Column("terms_accepted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("customers", "terms_accepted_at")
