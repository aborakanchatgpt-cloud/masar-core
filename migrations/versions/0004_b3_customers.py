"""B3: جداول المطابقة/الملف الشخصي/المحفظة (المرحلة 3 حسب الدليل §3.3، §3.6،
§3.7، §3.12): customers، profiles، products، orders، ledger، wallets،
subscriptions، opportunities، applications، feedback، company_cooldowns —
وفهرس أداء إضافي على jobs (family, out_of_region, first_seen_at) يخدم
مرشِّح المخطِّط (core/app/planner.py) بالمرحلة نفسها.

هذا الترحيل لا يلمس عمود jobs.locations (أُضيف مؤقتًا وقت الإقلاع بتعديل
مخطّط idempotent بـcore/app/discovery.py — راجع تعليق أعلى ذلك الملف) —
سيُضاف رسميًا بترحيل 0005 منفصل من فريق B2/discovery بعد استقرار هذا
الترحيل على main، تفاديًا لأي تصادم مراجعة بين المنفّذين المتوازيين.

الأسعار بجدول products مؤقتة (placeholder) — يحددها أحمد لاحقًا (راجع
تعليق seed أدناه وdocs/reports/B3-executor.md).

Revision ID: 0004_b3_customers
Revises: 0003_b2_region_and_dedup_fixes
Create Date: 2026-09-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# JSONB لا sa.JSON() العامة عمدًا لهذه الأعمدة تحديدًا: planner.py يستعلم
# customers.cities/families مباشرة بعوامل/دوال jsonb (`?`، jsonb_typeof،
# jsonb_array_elements_text) لتصفية المرشّحين بمجموعات SQL دون نقل بيانات
# غير ضرورية لبايثون — sa.JSON() العامة تُترجم لنوع json العادي بلا هذه
# العوامل (نفس ما تستخدمه بقية جداول jobs/companies بالمستودع لأعمدة لا
# تُستعلم بعوامل jsonb، مثل jobs.skills/raw_json — تلك تبقى json عادي عمدًا،
# فرق الاستخدام هو المبرر لا خطأ اتساق).
JSONB = postgresql.JSONB

revision: str = "0004_b3_customers"
down_revision: Union[str, None] = "0003_b2_region_and_dedup_fixes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # identity — customers + profiles
    # -----------------------------------------------------------------
    op.create_table(
        "customers",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column("email_service", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("cities", JSONB(), nullable=False, server_default="[]"),
        sa.Column("families", JSONB(), nullable=False, server_default="[]"),
        sa.Column("target_daily", sa.Integer(), nullable=False, server_default="17"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active','paused','expired')", name="ck_customers_status"
        ),
    )
    op.create_index("ix_customers_telegram_chat_id", "customers", ["telegram_chat_id"])
    op.create_index("ix_customers_status", "customers", ["status"])
    op.create_unique_constraint("uq_customers_email_service", "customers", ["email_service"])

    op.create_table(
        "profiles",
        sa.Column(
            "customer_id",
            sa.BigInteger(),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("cv_text", sa.Text(), nullable=True),
        sa.Column("cv_pdf_path", sa.Text(), nullable=True),
        sa.Column("years_exp", sa.Numeric(4, 1), nullable=True),
        sa.Column("seniority", sa.String(length=30), nullable=True),
        sa.Column("degree", sa.String(length=255), nullable=True),
        sa.Column("certs", JSONB(), nullable=False, server_default="[]"),
        sa.Column("skills", JSONB(), nullable=False, server_default="[]"),
        sa.Column("titles", JSONB(), nullable=False, server_default="[]"),
        sa.Column("nationality_saudi", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("languages", JSONB(), nullable=False, server_default="[]"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("extracted_by", sa.String(length=20), nullable=True),
        sa.CheckConstraint(
            "extracted_by IS NULL OR extracted_by IN ('rules','claude')", name="ck_profiles_extracted_by"
        ),
    )

    # -----------------------------------------------------------------
    # billing — products, orders, ledger, wallets, subscriptions
    # -----------------------------------------------------------------
    op.create_table(
        "products",
        sa.Column("code", sa.String(length=30), primary_key=True),
        sa.Column("name_ar", sa.String(length=120), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("price_sar", sa.Numeric(10, 2), nullable=False),
        sa.Column("days", sa.Integer(), nullable=True),
        sa.Column("credits", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint(
            "type IN ('subscription','credits','standalone_cv')", name="ck_products_type"
        ),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("customer_id", sa.BigInteger(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("product_code", sa.String(length=30), sa.ForeignKey("products.code"), nullable=False),
        sa.Column("amount_sar", sa.Numeric(10, 2), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="paid"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','paid','cancelled')", name="ck_orders_status"
        ),
    )
    op.create_index("ix_orders_customer_id", "orders", ["customer_id"])

    op.create_table(
        "ledger",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("customer_id", sa.BigInteger(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=30), nullable=False),
        sa.Column("ref_id", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "reason IN ('purchase','application_sent','bounce_refund','guarantee_refund','adjustment')",
            name="ck_ledger_reason",
        ),
    )
    op.create_index("ix_ledger_customer_id", "ledger", ["customer_id"])
    op.create_index("ix_ledger_created_at", "ledger", ["created_at"])

    # wallets: رصيد محسوب مباشرة (بدل view مادي) — يُحدَّث بنفس معاملة إدراج
    # ledger دائمًا (core/app/customers_api.py) حتى يبقى balance = SUM(ledger.delta)
    # لهذا العميل بلا إعادة حساب. القيد balance>=0 شبكة أمان على مستوى القاعدة
    # فوق التحقق التطبيقي (معيار قبول B3: "الرصيد لا يصبح سالبًا أبدًا").
    op.create_table(
        "wallets",
        sa.Column(
            "customer_id",
            sa.BigInteger(),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("balance", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("balance >= 0", name="ck_wallets_balance_non_negative"),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("customer_id", sa.BigInteger(), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_code", sa.String(length=30), sa.ForeignKey("products.code"), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("daily_target", sa.Integer(), nullable=False, server_default="17"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.CheckConstraint(
            "status IN ('active','extended','closed')", name="ck_subscriptions_status"
        ),
    )
    op.create_index("ix_subscriptions_customer_id", "subscriptions", ["customer_id"])

    # -----------------------------------------------------------------
    # matching/planning — opportunities, applications, feedback, cooldowns
    # -----------------------------------------------------------------
    op.create_table(
        "opportunities",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("customer_id", sa.BigInteger(), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.BigInteger(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("score", sa.Numeric(5, 4), nullable=False),
        sa.Column("tier", sa.String(length=2), nullable=False),
        sa.Column("reasons", JSONB(), nullable=False, server_default="{}"),
        sa.Column("planned_for", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="planned"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("tier IN ('A','B','C','C2','D')", name="ck_opportunities_tier"),
        sa.CheckConstraint(
            "status IN ('planned','sent','skipped','expired')", name="ck_opportunities_status"
        ),
    )
    op.create_unique_constraint("uq_opportunities_customer_job", "opportunities", ["customer_id", "job_id"])
    op.create_index("ix_opportunities_customer_planned_for", "opportunities", ["customer_id", "planned_for"])

    op.create_table(
        "applications",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("customer_id", sa.BigInteger(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("job_id", sa.BigInteger(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("opportunity_id", sa.BigInteger(), sa.ForeignKey("opportunities.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("companies.id"), nullable=True),
        # company_key: نسخة مطبّعة من اسم الشركة (نفس دالة
        # core.app.collectors.normalizer.company_key) — مُقحَمة (denormalized)
        # عمدًا حتى يفهرس/يستعلم قيد التبريد 60 يومًا وسقف 3/أسبوع بلا JOIN
        # على companies في كل استعلام مخطِّط يومي (§3.7، §3.9).
        sa.Column("company_key", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','sent','bounced','replied','interview')", name="ck_applications_status"
        ),
    )
    op.create_index("ix_applications_customer_id", "applications", ["customer_id"])
    op.create_index("ix_applications_company_key_sent_at", "applications", ["company_key", "sent_at"])

    op.create_table(
        "feedback",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("customer_id", sa.BigInteger(), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("application_id", sa.BigInteger(), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        # kind: 'down' (👎)، 'up' (🎉)، 'note' (ملاحظة نصية حرة) — رموز مقروءة
        # بدل تخزين الإيموجي حرفيًا بقيد CHECK؛ واجهة تيليجرام (B5) تُترجم
        # الأزرار لهذه القيم.
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('down','up','note')", name="ck_feedback_kind"),
    )
    op.create_index("ix_feedback_customer_id", "feedback", ["customer_id"])

    op.create_table(
        "company_cooldowns",
        sa.Column("company_key", sa.String(length=255), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_primary_key("pk_company_cooldowns", "company_cooldowns", ["company_key", "customer_id"])

    # -----------------------------------------------------------------
    # فهرس أداء على jobs لصالح مرشِّح المخطِّط SQL (planner.py) — البادئة
    # (family, out_of_region) تخدم شرط WHERE الأساسي، وfirst_seen_at اللاحق
    # يخدم فرز/تصفية الحداثة بلا فرز إضافي منفصل.
    # -----------------------------------------------------------------
    op.create_index(
        "ix_jobs_family_out_of_region_first_seen",
        "jobs",
        ["family", "out_of_region", "first_seen_at"],
    )

    # -----------------------------------------------------------------
    # بذر products بأسعار مؤقتة (placeholder) — أحمد يحدد الأسعار النهائية
    # لاحقًا (الدليل §10: "أسعار مبدئية للمنتجات"). القيم هنا فقط لتفعيل
    # اختبار /subscriptions ودفتر الرصيد؛ credits لمنتج الاشتراك = منحة
    # 510 تقديمًا شهريًا كما ينص الدليل §3.12، لا 17 (target_daily منفصل —
    # وتيرة الإرسال اليومية، لا رصيد المحفظة).
    # -----------------------------------------------------------------
    op.execute(
        """
        INSERT INTO products (code, name_ar, type, price_sar, days, credits, active) VALUES
            ('SUB30', 'اشتراك شهري', 'subscription', 90.00, 30, 510, true),
            ('CR100', 'رصيد 100 تقديم', 'credits', 60.00, NULL, 100, true),
            ('CR300', 'رصيد 300 تقديم', 'credits', 165.00, NULL, 300, true),
            ('CR510', 'رصيد 510 تقديم', 'credits', 240.00, NULL, 510, true),
            ('CV1', 'سيرة ذاتية مستقلة', 'standalone_cv', 15.00, NULL, NULL, true)
        ON CONFLICT (code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_family_out_of_region_first_seen", table_name="jobs")
    op.drop_table("company_cooldowns")
    op.drop_table("feedback")
    op.drop_index("ix_applications_company_key_sent_at", table_name="applications")
    op.drop_index("ix_applications_customer_id", table_name="applications")
    op.drop_table("applications")
    op.drop_index("ix_opportunities_customer_planned_for", table_name="opportunities")
    op.drop_constraint("uq_opportunities_customer_job", "opportunities", type_="unique")
    op.drop_table("opportunities")
    op.drop_index("ix_subscriptions_customer_id", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_table("wallets")
    op.drop_index("ix_ledger_created_at", table_name="ledger")
    op.drop_index("ix_ledger_customer_id", table_name="ledger")
    op.drop_table("ledger")
    op.drop_index("ix_orders_customer_id", table_name="orders")
    op.drop_table("orders")
    op.drop_table("products")
    op.drop_table("profiles")
    op.drop_constraint("uq_customers_email_service", "customers", type_="unique")
    op.drop_index("ix_customers_status", table_name="customers")
    op.drop_index("ix_customers_telegram_chat_id", table_name="customers")
    op.drop_table("customers")
