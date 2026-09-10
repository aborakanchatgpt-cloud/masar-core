"""B8: إزالة n8n نهائيًا من مسار الدخول الوارد (تيليجرام) — كل تفاعل
تيليجرام (بوت العملاء "مسار" + بوت الأدمن الخاص) يُدار الآن داخل Core
مباشرة عبر app.telegram_api/telegram_onboarding/telegram_admin، بلا أي
اعتماد على n8n. راجع docstring core/app/telegram_onboarding.py لتفصيل
كامل لقرارات التصميم (خصوصًا لماذا لا يوجد جدول "Pending Approvals" هنا،
بعكس n8n Data Tables القديمة).

`telegram_sessions`: جدول حالة محادثة واحد بسيط، صفّ واحد لكل chat_id،
يُستخدم من كلا البوتين معًا (لا تصادم عمليًا — chat_id بمحادثة خاصة هو
معرّف حساب Telegram الشخصي، لا يتكرر بين مستخدم عادي وأحمد صاحب البوت).
`step`: أين نحن بتدفّق المحادثة الحالي (فارغ = لا حالة معلّقة). `data`:
JSONB لأي بيانات مؤقتة قبل أن تُكتب لجداول customers/profiles الحقيقية
عند إنهاء كل خطوة (مثال: قائمة مدن/مجالات قيد الاختيار بعد onboarding).

Revision ID: 0013_telegram_sessions
Revises: 0012_b7_catalog
Create Date: 2026-09-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0013_telegram_sessions"
down_revision: Union[str, None] = "0012_b7_catalog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_sessions",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("step", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("data", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("telegram_sessions")
