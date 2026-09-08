"""B4 executor fixes (offline review docs/reports/B4-offline-review.md):

1. يضيف حالة 'queued' إلى قيد opportunities.status — send_builder.py لم يعد
   يضبط status='sent' وقت بناء الطابور (Critical/High 2 بالتقرير: كان
   يُسجَّل 'sent' فور الإدراج بـsend_queue، قبل أي إرسال فعلي)، بل 'queued'
   الآن، ولا تصبح 'sent' إلا عند نجاح الإرسال الفعلي (sender._mark_success)
   أو 'skipped' عند فشل نهائي (sender._mark_failure، MAX_ATTEMPTS).
2. فهرسان جديدان على applications (Low 2 بالتقرير + استعلام idempotency
   الجديد بـsender._existing_application_message_id الذي أضافته تصحيحات
   Critical/High 1/F1 نفسها — استعلام ساخن على كل صفّ send_queue مُستعاد):
   - ix_applications_customer_message (customer_id, message_id): استعلام
     ارتداد inbox.py._process_mail_link الحالي (WHERE customer_id=... AND
     message_id=...).
   - ix_applications_customer_job_opportunity (customer_id, job_id,
     opportunity_id): استعلام idempotency الجديد بـsender.py.

لا يغيّر أي بيانات قائمة (فيما عدا downgrade، الذي يُعيد صفوف 'queued'
لـ'planned' مؤقتًا حتى لا يخالف القيد القديم بعد إعادته).

Revision ID: 0006_b4_fixes
Revises: 0005_b4_mail
Create Date: 2026-09-08

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0006_b4_fixes"
down_revision: Union[str, None] = "0005_b4_mail"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_opportunities_status", "opportunities", type_="check")
    op.create_check_constraint(
        "ck_opportunities_status",
        "opportunities",
        "status IN ('planned','queued','sent','skipped','expired')",
    )
    op.create_index("ix_applications_customer_message", "applications", ["customer_id", "message_id"])
    op.create_index(
        "ix_applications_customer_job_opportunity", "applications", ["customer_id", "job_id", "opportunity_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_applications_customer_job_opportunity", table_name="applications")
    op.drop_index("ix_applications_customer_message", table_name="applications")
    # أي صفّ بحالة 'queued' وقت التراجع يُعاد لـ'planned' أولًا حتى لا يخالف
    # القيد القديم بعد إعادته (نفس المعنى العملي: لم يُرسَل بعد فعليًا).
    op.execute("UPDATE opportunities SET status = 'planned' WHERE status = 'queued'")
    op.drop_constraint("ck_opportunities_status", "opportunities", type_="check")
    op.create_check_constraint(
        "ck_opportunities_status",
        "opportunities",
        "status IN ('planned','sent','skipped','expired')",
    )
