"""B7: كتالوج المنتجات وطلبات الأدمن (المرحلة 6 حسب الدليل §9، §3.12، §4.10)
— core/app/catalog.py (`GET /catalog`, `POST /admin/orders`, `GET /admin/orders`).

الجدولان `products`/`orders` **موجودان فعلًا منذ 0004_b3_customers** (B3،
تُستخدمان حاليًا من `POST /subscriptions` بـcustomers_api.py) — هذا الترحيل
لا يُنشئهما من جديد (كان سيتصادم مع PK/الجدول الموجود) بل **يوسّعهما**
بالأعمدة التي يحتاجها B7 فقط، ويُبقي كل عمود/قيد قديم كما هو (لا كسر
لـ`POST /subscriptions` القائم — صفر اختبارات تغطّيه حاليًا لكنه كود حيّ):

    1. products.price_sar يصبح NULLable (كان NOT NULL) — قاعدة B7 غير
       القابلة للتفاوض: "الأسعار مؤقتة يحددها أحمد لاحقًا" (`PRICE_TBD` في
       core/app/catalog.py). الأسعار الخمسة المزروعة بـ0004 (90/60/165/240/15
       ريال) كانت أرقامًا تجريبية لتفعيل اختبار الرصيد وقتها لا أسعارًا
       معتمدة من أحمد — تُحوَّل هنا لـNULL (TBD) طبقًا لنفس القاعدة، تمامًا
       كما customers.price_sar NULLable أصلًا منذ 0008 ويتعامل معه
       guarantee.py بأمان (`if price_sar is not None`).
    2. products.applications_included (عدد صحيح، NULLable) — عدد
       التقديمات التي يمنحها المنتج (يوازي `credits` دلاليًا لمنتجات
       subscription/credits؛ NULL لـstandalone_cv). يُبذَر من `credits`
       الحالي فور الإضافة فلا يُفقَد أي معنى.
    3. orders: أعمدة جديدة NULLable (`starts_at`, `ends_at`,
       `credits_granted`, `note`) يحتاجها `POST /admin/orders` لتسجيل بداية/
       نهاية فترة الاشتراك المُفعَّلة والرصيد الممنوح وملاحظة حرة (مثال: "بانتظار
       توليد السيرة الذاتية" لطلبات CV1) — لا قيمة افتراضية فتبقى صفوف
       `POST /subscriptions` القديمة NULL بهذه الأعمدة بلا أثر.
    4. orders.amount_sar يصبح NULLable (يوافق تحويل price_sar لـNULL أعلاه —
       `POST /subscriptions` يُدرج `product["price_sar"]` مباشرة، فلو بقي
       العمود NOT NULL وأصبح المصدر NULL لانكسر ذلك المسار الحيّ).
    5. orders.status: يوسَّع قيد CHECK ليشمل 'active'/'fulfilled' الجديدتين
       (`POST /admin/orders`) فوق القيم الثلاث القديمة 'pending'/'paid'/
       'cancelled' (تبقى كما هي لأي قارئ/كاتب حالي) — القيمة الافتراضية
       الحالية ('paid') لا تتغيّر.

Revision ID: 0012_b7_catalog
Revises: 0011_b6_load
Create Date: 2026-09-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0012_b7_catalog"
down_revision: Union[str, None] = "0011_b6_load"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # 1+2. products — price_sar NULLable + applications_included جديد
    # -----------------------------------------------------------------
    op.alter_column("products", "price_sar", existing_type=sa.Numeric(10, 2), nullable=True)
    op.add_column("products", sa.Column("applications_included", sa.Integer(), nullable=True))
    op.execute("UPDATE products SET applications_included = credits")
    # PRICE_TBD حقيقي: الأسعار الخمسة المزروعة بـ0004 لم تُعتمد من أحمد بعد
    # (راجع docstring أعلاه) — تُحوَّل لـNULL هنا فتصبح "قيد الانتظار" فعليًا
    # لا رقمًا تجريبيًا يُقرأ خطأً كسعر نهائي بأي واجهة إدارية مستقبلية.
    op.execute("UPDATE products SET price_sar = NULL")

    # -----------------------------------------------------------------
    # 3+4. orders — أعمدة B7 الجديدة + amount_sar NULLable
    # -----------------------------------------------------------------
    op.add_column("orders", sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("credits_granted", sa.Integer(), nullable=True))
    op.add_column("orders", sa.Column("note", sa.Text(), nullable=True))
    op.alter_column("orders", "amount_sar", existing_type=sa.Numeric(10, 2), nullable=True)

    # -----------------------------------------------------------------
    # 5. orders.status — قيد CHECK موسَّع (union قديم+جديد)
    # -----------------------------------------------------------------
    op.drop_constraint("ck_orders_status", "orders", type_="check")
    op.create_check_constraint(
        "ck_orders_status",
        "orders",
        "status IN ('pending','paid','active','fulfilled','cancelled')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_orders_status", "orders", type_="check")
    op.create_check_constraint(
        "ck_orders_status",
        "orders",
        "status IN ('pending','paid','cancelled')",
    )

    op.drop_column("orders", "note")
    op.drop_column("orders", "credits_granted")
    op.drop_column("orders", "ends_at")
    op.drop_column("orders", "starts_at")

    # استرجاع NOT NULL يتطلب صفوفًا بلا NULL أولًا — نعيد نفس القيم الأصلية
    # المزروعة بـ0004 لصفوف التسع (5 منتجات + أي طلب أدرجته اختبارات B7
    # المحلية عبر amount_sar=NULL) حتى ينجح downgrade بلا فقد بيانات وهمي.
    op.execute(
        """
        UPDATE products SET price_sar = CASE code
            WHEN 'SUB30' THEN 90.00
            WHEN 'CR100' THEN 60.00
            WHEN 'CR300' THEN 165.00
            WHEN 'CR510' THEN 240.00
            WHEN 'CV1' THEN 15.00
            ELSE 0.00
        END
        WHERE price_sar IS NULL
        """
    )
    op.execute("UPDATE orders SET amount_sar = 0.00 WHERE amount_sar IS NULL")
    op.alter_column("orders", "amount_sar", existing_type=sa.Numeric(10, 2), nullable=False)

    op.drop_column("products", "applications_included")
    op.alter_column("products", "price_sar", existing_type=sa.Numeric(10, 2), nullable=False)
