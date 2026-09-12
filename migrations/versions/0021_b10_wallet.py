"""B10: نموذج المحفظة/الدفع حسب الاستخدام (رصيد ريالي، لا اشتراك) — راجع
claude/masar_build_brief_v3_2026-09-11.md قسم B10 لتفاصيل القرار الكامل
(ثمانية توضيحات من أحمد، كلها نهائية بلا نقاش إضافي).

الفكرة: باقة جديدة **إضافية** (لا تستبدل الاشتراك/الرصيد الحاليين) —
العميل يدفع فقط على التقديمات الفعلية الناجحة، بسعر ريالي لكل تقديم
(0.235 ريال، قابل للتعديل من app_settings لا مكتوبًا بالكود — راجع
core/app/wallet.py). الرصيد يُخزَّن بالريال (Decimal) لا كعدد تقديمات —
"الباقي الذي لا يكفي لإرسال تقديم يبقى بالمحفظة يُستخدَم لاحقًا" (نص أحمد
الحرفي) — عدد التقديمات المتاح يُحسَب ديناميكيًا floor(balance/rate) دومًا،
لا عمود منفصل يُخزَّن ويُزامَن.

    1. customers.billing_mode (نص، CHECK IN 'subscription'/'wallet'،
       NOT NULL، افتراضي 'subscription') — يحدّد أي نظام خصم يُستخدَم عند
       الإرسال الفعلي (core/app/sender.py:_mark_success) وأي سقف يومي
       يُطبَّق عند بناء الطابور (core/app/send_builder.py) لهذا العميل.
       الافتراضي 'subscription' لكل الصفوف الحالية يعني **صفر تغيير سلوك**
       لأي عميل حالي (تعليمات هذه الدفعة الحرفية: zero behavior change
       for existing subscription customers).
    2. customers.wallet_balance_sar (Numeric(12,3)، NOT NULL، افتراضي 0) —
       **دقّة 3 خانات عشرية عمدًا لا 2** (خلاف orders.amount_sar وكل أعمدة
       الريال الأخرى بالمستودع): سعر التقديم الواحد الذي حدّده أحمد (0.235
       ريال) يحمل ثلاث خانات عشرية — تخزينه بعمود Numeric(*, 2) كان سيُقرِّب
       كل عملية خصم فرديًا (انجراف تراكمي فعلي عبر آلاف التقديمات، لا مجرد
       نظري) بعكس مبدأ "الريال هو مصدر الحقيقة الوحيد، بلا تقريب" الذي نصّ
       عليه أحمد صراحة لمسار الشحن بمبلغ حر. المبالغ نفسها (شحن/تعديل يدوي)
       تبقى بهللات كاملة كالمعتاد؛ العمود الأدق ببساطة *يتّسع* لها بلا خسارة،
       فلا أثر عملي على أي مسار آخر — قرار تصميم مُتّخَذ هنا لحلّ غموض تقني
       صغير لم يحسمه أحمد صراحة (لم يُسأل عن دقّة العمود)، يُوثَّق هنا بدل
       الرجوع إليه (تعليمات الدفعة: حلّ الغموض الصغير ذاتيًا وتوثيقه).
    3. wallet_transactions — سجلّ تدقيق كل حركة محفظة (شحن/استهلاك/تعديل
       يدوي من أحمد) — id, customer_id, amount (موقّع: + إضافة/- خصم، نفس
       دقّة wallet_balance_sar أعلاه)، type (CHECK)، reason (نصّي، اختياري
       بقيد قاعدة البيانات — يُلزَم به لتعديل الأدمن اليدوي من واجهة
       تيليجرام نفسها لا من قيد قاعدة بيانات، نفس أسلوب حقول اختيارية أخرى
       بالمستودع تُفرَض إلزاميتها بمنطق التطبيق)، created_at، created_by
       (معرّف/اسم أدمن تيليجرام، اختياري).
    4. products.type CHECK يتوسّع ليشمل 'wallet_topup' الجديد (فوق الثلاث
       القديمة subscription/credits/standalone_cv، تبقى كما هي) — باقة
       "شحن محفظة" الجديدة تُدار كصفّ products عادي (price_sar=NULL لأن
       المبلغ متغيّر يحدّده العميل وقت الطلب، لا سعر ثابت) حتى تتكامل بلا
       أي تعديل بنيوي إضافي مع payment_requests/orders الموجودتين (نفس
       مسار الدفع بالضبط: بنك → إيصال → موافقة أحمد ✅ — إجابة أحمد
       الحرفية: "نفس الطريقة الحالية").
    5. بذر صفّ منتج WALLET (وسم عرض عربي مطابق لاقتراح أحمد حرفيًا: "💰
       ادفع حسب الاستخدام (محفظة)") + مفتاح app_settings الجديد
       wallet_rate_per_application_sar='0.235' (القيمة الافتراضية القابلة
       للتعديل لاحقًا — لا واجهة تعديل مخصّصة بهذه الدفعة بعد، نفس حالة أي
       مفتاح app_settings آخر لم تُبنَ له بعد شاشة تعديل، يُعدَّل بـUPDATE
       مباشر مؤقتًا).

Revision ID: 0021_b10_wallet
Revises: 0020_b4_msg_category
Create Date: 2026-09-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0021_b10_wallet"
down_revision: Union[str, None] = "0020_b4_msg_category"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # 1+2. customers — billing_mode + wallet_balance_sar
    # -----------------------------------------------------------------
    op.add_column(
        "customers",
        sa.Column("billing_mode", sa.Text(), nullable=False, server_default="subscription"),
    )
    op.create_check_constraint(
        "ck_customers_billing_mode",
        "customers",
        "billing_mode IN ('subscription','wallet')",
    )
    op.add_column(
        "customers",
        sa.Column("wallet_balance_sar", sa.Numeric(12, 3), nullable=False, server_default="0"),
    )

    # -----------------------------------------------------------------
    # 3. wallet_transactions
    # -----------------------------------------------------------------
    op.create_table(
        "wallet_transactions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "customer_id", sa.BigInteger(), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("amount", sa.Numeric(12, 3), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "type IN ('topup','consumption','admin_adjustment')", name="ck_wallet_transactions_type"
        ),
    )
    op.create_index("ix_wallet_transactions_customer_id", "wallet_transactions", ["customer_id"])

    # -----------------------------------------------------------------
    # 4. products.type — إضافة 'wallet_topup' لقيد CHECK الموجود
    # -----------------------------------------------------------------
    op.drop_constraint("ck_products_type", "products", type_="check")
    op.create_check_constraint(
        "ck_products_type",
        "products",
        "type IN ('subscription','credits','standalone_cv','wallet_topup')",
    )

    # -----------------------------------------------------------------
    # 5. بذر منتج المحفظة + سعر التقديم الافتراضي بـapp_settings
    # -----------------------------------------------------------------
    op.execute(
        """
        INSERT INTO products (code, name_ar, type, price_sar, days, credits, applications_included, active)
        VALUES ('WALLET', '💰 ادفع حسب الاستخدام (محفظة)', 'wallet_topup', NULL, NULL, NULL, NULL, true)
        ON CONFLICT (code) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO app_settings (key, value) VALUES ('wallet_rate_per_application_sar', '0.235')
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM app_settings WHERE key = 'wallet_rate_per_application_sar'")
    op.execute("DELETE FROM products WHERE code = 'WALLET'")

    op.drop_constraint("ck_products_type", "products", type_="check")
    op.create_check_constraint(
        "ck_products_type",
        "products",
        "type IN ('subscription','credits','standalone_cv')",
    )

    op.drop_index("ix_wallet_transactions_customer_id", table_name="wallet_transactions")
    op.drop_table("wallet_transactions")

    op.drop_column("customers", "wallet_balance_sar")
    op.drop_constraint("ck_customers_billing_mode", "customers", type_="check")
    op.drop_column("customers", "billing_mode")
