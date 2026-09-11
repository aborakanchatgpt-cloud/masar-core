"""B9/B0: قاعدة بيانات دفعة B9 — التسجيل الذاتي قبل تأكيد الدفع (`pending`)،
المفوّضون، طلبات الدفع، إعدادات التطبيق، ورسائل العملاء.

**customers.status='pending' (جديد + الافتراضي الجديد):** العميل الجديد
يسجّل نفسه عبر بوت العملاء بلا أي تدخّل مسبق من أحمد (قرار المنتج 11
سبتمبر: "تسجيل ذاتي") — صفّه يُنشأ فورًا بحالة `pending` حتى يؤكد أحمد
استلام الحوالة (B3) أو يُفعّله يدويًا من بطاقته. راجعت شخصيًا (قبل كتابة
هذا الترحيل) كل موضع يقرأ `customers.status` بالمستودع (send_builder.py،
planner.py، reports.py، retention.py، skill_gap.py، guarantee.py،
inbox.py): جميعها إما تُصفّي صراحة `status = 'active'` على مستوى نقطة
الدخول الرئيسية، أو لا تلمس `customers.status` إطلاقًا (تعتمد على حالة
جدول آخر مستقل مثل `subscriptions.status`/`mail_links.status`) — أي مكان
منها كان سيُعامل "غير active" (بما فيها `pending` الجديدة) كمرشّح خطأً لو
افترض "غير paused = نشط"، لكن لا يوجد أي موضع كهذا فعليًا؛ فلا تغيير مطلوب
بأي من تلك الملفات، فقط بالترحيل وبـ`customers_api.CUSTOMER_STATUS_ALLOWED_VALUES`
(تعديل منفصل بنفس الدفعة يسمح بالتفعيل اليدوي `pending`→`active`).

**الجداول الأربعة الجديدة** (تُبنى فوقها لاحقًا: B2 المفوّضون، B3 الدفع،
B4/B5 رسائل العميل والإعدادات — هذا الترحيل بنية فقط، بلا أي كود يقرأ/يكتب
إليها بعد):
    admin_delegates    — مفوّضو أحمد على بوت الأدمن (username أو جوال).
    payment_requests   — طلب دفع لكل محاولة اشتراك/شراء (إيصال + مبلغ معلَن
                          + قرار أحمد ✅/❌).
    app_settings       — مفتاح/قيمة نصّي بسيط: بيانات التحويل البنكي ورقم
                          واتساب الدعم (يُدخلها أحمد من ⚙️ الإعدادات — لا
                          قيمة مكتوبة بالكود هنا سوى support_whatsapp
                          الحالية فعليًا بعدة ملفات، تُبذر كبداية فقط).
    customer_messages  — سجلّ "تواصل معنا" (in) وردود الأدمن (out).

**products**: لا تغيير — `active`/`price_sar` (NULLable)/`days`/
`applications_included` موجودة بالفعل منذ 0012_b7_catalog.

Revision ID: 0017_b9_payments_delegates
Revises: 0016_terms_accepted
Create Date: 2026-09-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0017_b9_payments_delegates"
down_revision: Union[str, None] = "0016_terms_accepted"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # customers.status: 'pending' جديدة + الافتراضي الجديد
    # -----------------------------------------------------------------
    op.drop_constraint("ck_customers_status", "customers", type_="check")
    op.create_check_constraint(
        "ck_customers_status",
        "customers",
        "status IN ('pending','active','paused','expired')",
    )
    op.alter_column("customers", "status", server_default="pending")

    # -----------------------------------------------------------------
    # admin_delegates
    # -----------------------------------------------------------------
    op.create_table(
        "admin_delegates",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        # username بلا "@" وlower-case — اتفاقية تطبيقية تُطبَّق بكود B2
        # القادم عند الكتابة، لا قيد قاعدة بيانات هنا.
        sa.Column("telegram_username", sa.String(length=64), nullable=True),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint(
        "uq_admin_delegates_telegram_chat_id", "admin_delegates", ["telegram_chat_id"]
    )

    # -----------------------------------------------------------------
    # payment_requests
    # -----------------------------------------------------------------
    op.create_table(
        "payment_requests",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "customer_id", sa.BigInteger(), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("product_code", sa.String(length=30), sa.ForeignKey("products.code"), nullable=False),
        sa.Column("expected_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("declared_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("sender_name", sa.Text(), nullable=True),
        sa.Column("receipt_path", sa.Text(), nullable=True),
        sa.Column("receipt_kind", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("admin_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_chat_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','confirmed','rejected')", name="ck_payment_requests_status"
        ),
        sa.CheckConstraint(
            "receipt_kind IS NULL OR receipt_kind IN ('photo','document')",
            name="ck_payment_requests_receipt_kind",
        ),
    )
    op.create_index("ix_payment_requests_customer_id", "payment_requests", ["customer_id"])
    op.create_index("ix_payment_requests_status", "payment_requests", ["status"])

    # -----------------------------------------------------------------
    # app_settings — مفتاح/قيمة، بذر بيانات التحويل فارغة + واتساب الحالي
    # -----------------------------------------------------------------
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=60), primary_key=True),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute(
        """
        INSERT INTO app_settings (key, value) VALUES
            ('bank_name', NULL),
            ('account_holder', NULL),
            ('iban', NULL),
            ('support_whatsapp', '+966544161255')
        ON CONFLICT (key) DO NOTHING
        """
    )

    # -----------------------------------------------------------------
    # customer_messages
    # -----------------------------------------------------------------
    op.create_table(
        "customer_messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "customer_id", sa.BigInteger(), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("direction", sa.String(length=3), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("sent_by_chat_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint("direction IN ('in','out')", name="ck_customer_messages_direction"),
    )
    op.create_index("ix_customer_messages_customer_id", "customer_messages", ["customer_id"])


def downgrade() -> None:
    op.drop_index("ix_customer_messages_customer_id", table_name="customer_messages")
    op.drop_table("customer_messages")

    op.execute(
        "DELETE FROM app_settings WHERE key IN ('bank_name','account_holder','iban','support_whatsapp')"
    )
    op.drop_table("app_settings")

    op.drop_index("ix_payment_requests_status", table_name="payment_requests")
    op.drop_index("ix_payment_requests_customer_id", table_name="payment_requests")
    op.drop_table("payment_requests")

    op.drop_constraint("uq_admin_delegates_telegram_chat_id", "admin_delegates", type_="unique")
    op.drop_table("admin_delegates")

    # أي عميل ما زال 'pending' وقت التراجع يُعاد لـ'active' أولًا حتى لا
    # يخالف القيد القديم بعد إعادته (نفس أسلوب 0006_b4_fixes.downgrade).
    op.execute("UPDATE customers SET status = 'active' WHERE status = 'pending'")
    op.alter_column("customers", "status", server_default="active")
    op.drop_constraint("ck_customers_status", "customers", type_="check")
    op.create_check_constraint(
        "ck_customers_status",
        "customers",
        "status IN ('active','paused','expired')",
    )
