"""B4 F1 (مراجعة حيّة docs/reports/B3B4-live-review.md، استنتاج عالٍ 1):
يضيف عمودًا اختياريًا `verified_via` إلى `mail_links` لتسجيل صراحةً أن حالة
'ok' جاءت من تجاوز إداري صريح (skip_verify=true) بوضع DRY_RUN، لا من اختبار
SMTP/IMAP حقيقي ناجح — يمنع الالتباس لاحقًا عند تشخيص أي صندوق بريد لم
يُختبَر فعليًا رغم أن status='ok' بقاعدة البيانات.

القيمة NULL تعني (كالسابق) مسار التحقق الحقيقي الطبيعي (SMTP+IMAP فعليان)؛
القيمة 'skipped-dry-run' تعني أن `POST /mail-link` استُدعي بـskip_verify=true
بينما DRY_RUN فعّال (MAIL_LIVE != true) — مرفوضة كليًا خارج DRY_RUN (400).

Revision ID: 0007_mail_link_skip_verify
Revises: 0006_b4_fixes
Create Date: 2026-09-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_mail_link_skip_verify"
down_revision: Union[str, None] = "0006_b4_fixes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "mail_links",
        sa.Column("verified_via", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mail_links", "verified_via")
