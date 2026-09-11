"""B4 — عمود category بجدول customer_messages: يصنّف الرسائل الواردة من
العميل للأدمن (direction='in') — شكوى/قلب لقلب/ملاحظة، القناة الجديدة
"📞 تواصل معنا" بـ`app.telegram_onboarding`. NULLable عمدًا: رسائل الأدمن
الصادرة (direction='out') لا تحمل فئة إطلاقًا، وتبقى NULL دومًا — القيد
أدناه يمنع فقط قيمة خاطئة، لا يُلزم بوجود فئة.

Revision ID: 0020_b4_msg_category
Revises: 0019_b3_bank_account_fields
Create Date: 2026-09-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0020_b4_msg_category"
down_revision: Union[str, None] = "0019_b3_bank_account_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("customer_messages", sa.Column("category", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_customer_messages_category",
        "customer_messages",
        "category IS NULL OR category IN ('complaint','heart_to_heart','note')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_customer_messages_category", "customer_messages", type_="check")
    op.drop_column("customer_messages", "category")
