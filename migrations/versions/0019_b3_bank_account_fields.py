"""B3 متابعة٢: حقلان إضافيان للحساب البنكي — رقم الحساب account_number،
ولغة اسم البنك name_language (ar/en، لعرض داخلي فقط للأدمن — لا يُعرض
للعميل بلغتين معًا). الآيبان iban يصبح اختياريًا (nullable) بدل إلزامي،
مع قيد CHECK يضمن تعبئة واحد على الأقل من account_number/iban.

جاء هذا بناءً على طلب أحمد: "رقم الحساب" حقل منفصل بجانب الآيبان،
كلاهما اختياري (يكفي واحد منهما)، واسم البنك لا يُعرض للعميل بالعربي
والإنجليزي معًا — فقط علامة لغة داخلية اختيارية لأحمد.

Revision ID: 0019_b3_bank_account_fields
Revises: 0018_b3_bank_accounts
Create Date: 2026-09-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0019_b3_bank_account_fields"
down_revision: Union[str, None] = "0018_b3_bank_accounts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("bank_accounts", sa.Column("account_number", sa.Text(), nullable=True))
    op.add_column("bank_accounts", sa.Column("name_language", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_bank_accounts_name_language",
        "bank_accounts",
        "name_language IS NULL OR name_language IN ('ar', 'en')",
    )

    op.alter_column("bank_accounts", "iban", existing_type=sa.Text(), nullable=True)

    op.create_check_constraint(
        "ck_bank_accounts_number_or_iban",
        "bank_accounts",
        "account_number IS NOT NULL OR iban IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_bank_accounts_number_or_iban", "bank_accounts", type_="check")
    op.alter_column("bank_accounts", "iban", existing_type=sa.Text(), nullable=False)
    op.drop_constraint("ck_bank_accounts_name_language", "bank_accounts", type_="check")
    op.drop_column("bank_accounts", "name_language")
    op.drop_column("bank_accounts", "account_number")
