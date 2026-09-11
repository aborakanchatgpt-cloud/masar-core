"""B3 متابعة: دعم أكثر من بنك واحد لبيانات التحويل — جدول bank_accounts
يستبدل مفاتيح app_settings الثلاثة الأحادية (bank_name/account_holder/iban)
بقائمة حسابات بنكية يديرها أحمد من ⚙️ الإعدادات → 🏦 بيانات التحويل.

**لا حذف**: مفاتيح app_settings الثلاثة القديمة تبقى بجدولها (غير مستخدَمة
بالكود بعد هذا الترحيل) — لا شيء يُحذف، فقط يُهجَر. إن كانت الثلاثة معبّأة
فعليًا وقت الترحيل (نادر — آخر مراجعة كانت الثلاثة NULL)، تُنسَخ تلقائيًا
كحساب بنكي أول نشط حتى لا يضيع أي إدخال سابق لأحمد.

soft-delete فقط (`active=false`) — نفس نمط `admin_delegates` بترحيلة 0017،
لا صف يُحذف فعليًا عبر واجهة الأدمن.

Revision ID: 0018_b3_bank_accounts
Revises: 0017_b9_payments_delegates
Create Date: 2026-09-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0018_b3_bank_accounts"
down_revision: Union[str, None] = "0017_b9_payments_delegates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bank_accounts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("bank_name", sa.Text(), nullable=False),
        sa.Column("account_holder", sa.Text(), nullable=False),
        sa.Column("iban", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ترحيل بيانات app_settings الأحادية القديمة (إن كانت مُعبّأة فعلًا فقط)
    # — صف نشط واحد، حتى لا يضيع أي إدخال سابق. الجُداء الديكارتي هنا آمن
    # لأن كل تصفية key= تُبقي صفًا واحدًا كحد أقصى من app_settings.
    op.execute(
        """
        INSERT INTO bank_accounts (bank_name, account_holder, iban)
        SELECT b.value, h.value, i.value
        FROM app_settings b, app_settings h, app_settings i
        WHERE b.key = 'bank_name' AND h.key = 'account_holder' AND i.key = 'iban'
          AND b.value IS NOT NULL AND h.value IS NOT NULL AND i.value IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_table("bank_accounts")
