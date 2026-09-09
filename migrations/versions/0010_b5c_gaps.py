"""B5c: فجوات ما بعد مراجعة B5a/B2b (docs/reports/B5a-B2b-review.md) —
فصل عدّاد التمديد بـguarantee_ledger، بيانات الاعتماد الإدارية لروابط
تيليجرام/سيرة العملاء، وجعل customers.email_service اختياريًا حتى إنشاء
رابط البريد الفعلي.

هذا الترحيل مملوك بالكامل لمنفّذ B5c — لا يلمس أي جدول من جداول B6 المتوازية
(applications, send_queue, opportunities, company_cooldowns, ...) ولا يعيد
تعريف أي قيد عليها. يضيف/يعدّل فقط:

    1. guarantee_ledger.grace_extension_days / outage_extension_days —
       عمودان منفصلان يحلّان محل الاعتماد الحصري على extension_days الكلي
       لفحص "هل استُهلِكت مهلة الأداء الإلزامية؟" (عيب [major] بالمراجعة، القسم
       3.2) — extension_days يبقى كما هو (المجموع، توافقًا خلفيًا).
    2. customers.email_service يصبح NULLable (تسجيل عميل عبر onboarding قبل
       ربط بريد Gmail فعلي) — القيد الفريد يتحوّل لفهرس فريد جزئي
       (WHERE email_service IS NOT NULL) بدل قيد UNIQUE عادي.
    3. customers.telegram_chat_id: الفهرس العادي الموجود (0004) يتحوّل لفهرس
       فريد جزئي (WHERE telegram_chat_id IS NOT NULL) — يمنع ربط رقم محادثة
       تيليجرام واحد بعميلين مختلفين، ضروري لصحّة
       GET /customers/by-telegram/{chat_id}.
    4. customers.cv_pdf_path / cv_pdf_sha256 / cv_pdf_uploaded_at — مرجع
       سيرة PDF مرفوعة مباشرة (POST /customers/{id}/cv)، منفصل عن
       profiles.cv_pdf_path (B3 — نسخة السيرة **المولَّدة** لكل عائلة مهنية
       عبر cv_builder.py، لا نفس الشيء).
    5. customer_status_audit — سجلّ تدقيق بسيط لكل تغيير حالة عميل
       (POST /customers/{id}/status)، بلا أي تعديل على customers.status
       نفسها (البقاء ضمن قيم CHECK الحالية: active/paused/expired).

ملاحظة ترحيل مهمة (البند 1): صفوف guarantee_ledger القديمة (قبل هذا
الترحيل) تبدأ بـgrace_extension_days=0 افتراضيًا — لا سجلّ تاريخي يميّز مصدر
كل يوم تمديد سابق (نفس القيد الموثَّق أعلى core/app/guarantee.py). الأثر
العملي: عميل قديم قد يحصل على مهلة أداء إضافية مرة واحدة كأثر انتقالي — وهو
الاتجاه الآمن الوحيد المقبول (تأخير تعويض ببضعة أيام لعميل قديم، لا إسقاط
مهلة مستحقة لعميل جديد أبدًا).

Revision ID: 0010_b5c_gaps
Revises: 0008_b5_reports
Create Date: 2026-09-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010_b5c_gaps"
down_revision: Union[str, None] = "0008_b5_reports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # 1. guarantee_ledger — فصل عدّاد التمديد (تصحيح [major] بالمراجعة)
    # -----------------------------------------------------------------
    op.add_column(
        "guarantee_ledger",
        sa.Column("grace_extension_days", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "guarantee_ledger",
        sa.Column("outage_extension_days", sa.Integer(), nullable=False, server_default="0"),
    )

    # -----------------------------------------------------------------
    # 2. customers.email_service → NULLable + فهرس فريد جزئي
    # -----------------------------------------------------------------
    op.drop_constraint("uq_customers_email_service", "customers", type_="unique")
    op.alter_column("customers", "email_service", existing_type=sa.String(length=255), nullable=True)
    op.create_index(
        "uq_customers_email_service_not_null",
        "customers",
        ["email_service"],
        unique=True,
        postgresql_where=sa.text("email_service IS NOT NULL"),
    )

    # -----------------------------------------------------------------
    # 3. customers.telegram_chat_id → فهرس فريد جزئي (كان فهرسًا عاديًا)
    # -----------------------------------------------------------------
    op.drop_index("ix_customers_telegram_chat_id", table_name="customers")
    op.create_index(
        "uq_customers_telegram_chat_id_not_null",
        "customers",
        ["telegram_chat_id"],
        unique=True,
        postgresql_where=sa.text("telegram_chat_id IS NOT NULL"),
    )

    # -----------------------------------------------------------------
    # 4. customers.cv_pdf_* — مرجع سيرة PDF مرفوعة مباشرة (POST /customers/{id}/cv)
    # -----------------------------------------------------------------
    op.add_column("customers", sa.Column("cv_pdf_path", sa.Text(), nullable=True))
    op.add_column("customers", sa.Column("cv_pdf_sha256", sa.String(length=64), nullable=True))
    op.add_column(
        "customers", sa.Column("cv_pdf_uploaded_at", sa.DateTime(timezone=True), nullable=True)
    )

    # -----------------------------------------------------------------
    # 5. customer_status_audit — سجلّ تدقيق POST /customers/{id}/status
    # -----------------------------------------------------------------
    op.create_table(
        "customer_status_audit",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "customer_id",
            sa.BigInteger(),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("old_status", sa.String(length=20), nullable=False),
        sa.Column("new_status", sa.String(length=20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_customer_status_audit_customer_id", "customer_status_audit", ["customer_id"])


def downgrade() -> None:
    op.drop_index("ix_customer_status_audit_customer_id", table_name="customer_status_audit")
    op.drop_table("customer_status_audit")

    op.drop_column("customers", "cv_pdf_uploaded_at")
    op.drop_column("customers", "cv_pdf_sha256")
    op.drop_column("customers", "cv_pdf_path")

    op.drop_index("uq_customers_telegram_chat_id_not_null", table_name="customers")
    op.create_index("ix_customers_telegram_chat_id", "customers", ["telegram_chat_id"])

    op.drop_index("uq_customers_email_service_not_null", table_name="customers")
    op.alter_column("customers", "email_service", existing_type=sa.String(length=255), nullable=False)
    op.create_unique_constraint("uq_customers_email_service", "customers", ["email_service"])

    op.drop_column("guarantee_ledger", "outage_extension_days")
    op.drop_column("guarantee_ledger", "grace_extension_days")
