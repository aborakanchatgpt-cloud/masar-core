"""B11: المال والمتانة — راجع claude/masar_build_brief_v4_2026-09-12.md
قسم P0.5/P0.6/B11.3 لتفصيل القرارات الكاملة.

    1. wallet_transactions.application_id (BIGINT NULL) + فهرس عادي — يربط
       حركة 'consumption' بتطبيق مُرسَل محدّد (sender._mark_success يمرّره
       الآن) حتى يستطيع inbox.py إيجاد الخصم الأصلي عند ارتداد لاحق ويردّه
       عبر wallet.refund_bounce_conn. فهرس فريد **جزئي** إضافي على
       `(application_id) WHERE type='bounce_refund'` يضمن idempotency: لا
       استرداد مضاعف لنفس application_id حتى لو أُعيدت معالجة نفس ارتداد.
    2. ck_wallet_transactions_type يتوسّع ليشمل 'bounce_refund' الجديد (فوق
       الثلاث القديمة topup/consumption/admin_adjustment).
    3. CHECK (wallet_balance_sar >= 0) على customers — دفاع بمستوى قاعدة
       البيانات فوق فحص apply_wallet_delta_conn بالتطبيق (P0.6). backfill
       أي صف سالب (غير متوقّع عمليًا — لا مسار كتابة قديم يُنتج رصيدًا سالبًا
       حتى الآن، لكن الاحتياط أرخص من قيد يفشل عند الترقية) إلى 0 أولًا.
    4. guarantee_ledger.extension_days_total (INT NOT NULL DEFAULT 0) —
       يتتبّع **مجموع كل التمديدات التلقائية** (مهلة أداء + انقطاع بريد +
       تمديد عجز بعد استهلاك المهلة) لفترة واحدة، بحيث لا يتجاوز 30 يومًا
       إجمالًا (P0.5: الضمان أصبح تمديدًا بحتًا، لا تعويضًا نقديًا بعد الآن).
       صفوف قديمة تبدأ من 0 — القيمة الآمنة الوحيدة الممكنة بلا سجلّ تاريخي
       (نفس فلسفة grace_extension_days/outage_extension_days بترحيلة 0010).
    5. ck_guarantee_ledger_status يتوسّع ليشمل 'extended_final' الجديد (حالة
       فترة بلغت سقف 30 يوم تمديد تلقائي ولا تزال قاصرة — تُغلَق بلا تعويض
       نقدي، لا مسار تحويل مالي متبقٍّ بعد P0.5). 'refund_pending'/'settled'
       يبقيان بالقيد للتوافق الخلفي مع أي صفّ تاريخي سابق لهذه الدفعة، وإن
       كان الكود الجديد لن يُنتج 'refund_pending' بعد الآن.
    6. admin_delegates.pending_chat_id (BIGINT NULL) — B11.3: تفعيل مفوّض
       لم يعد فوريًا عند أول رسالة مطابقة؛ يُخزَّن هذا العمود بانتظار تأكيد
       صريح من المالك (زرّي ✅/❌ ببوت الأدمن) قبل أن يصبح telegram_chat_id
       فعليًا.

Revision ID: 0022_b11_money
Revises: 0021_b10_wallet
Create Date: 2026-09-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0022_b11_money"
down_revision: Union[str, None] = "0021_b10_wallet"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # 1+2. wallet_transactions — application_id + توسيع ck النوع
    # -----------------------------------------------------------------
    op.add_column("wallet_transactions", sa.Column("application_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_wallet_transactions_application_id", "wallet_transactions", ["application_id"])
    op.create_index(
        "uq_wallet_transactions_bounce_refund_application",
        "wallet_transactions",
        ["application_id"],
        unique=True,
        postgresql_where=sa.text("type = 'bounce_refund'"),
    )
    op.drop_constraint("ck_wallet_transactions_type", "wallet_transactions", type_="check")
    op.create_check_constraint(
        "ck_wallet_transactions_type",
        "wallet_transactions",
        "type IN ('topup','consumption','admin_adjustment','bounce_refund')",
    )

    # -----------------------------------------------------------------
    # 3. customers.wallet_balance_sar — CHECK >= 0 (بعد backfill احترازي)
    # -----------------------------------------------------------------
    op.execute("UPDATE customers SET wallet_balance_sar = 0 WHERE wallet_balance_sar < 0")
    op.create_check_constraint(
        "ck_customers_wallet_balance_nonneg", "customers", "wallet_balance_sar >= 0"
    )

    # -----------------------------------------------------------------
    # 4+5. guarantee_ledger — extension_days_total + حالة extended_final
    # -----------------------------------------------------------------
    op.add_column(
        "guarantee_ledger",
        sa.Column("extension_days_total", sa.Integer(), nullable=False, server_default="0"),
    )
    op.drop_constraint("ck_guarantee_ledger_status", "guarantee_ledger", type_="check")
    op.create_check_constraint(
        "ck_guarantee_ledger_status",
        "guarantee_ledger",
        "status IN ('computed','extended','extended_final','refund_pending','settled','na')",
    )

    # -----------------------------------------------------------------
    # 6. admin_delegates.pending_chat_id (B11.3)
    # -----------------------------------------------------------------
    op.add_column("admin_delegates", sa.Column("pending_chat_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("admin_delegates", "pending_chat_id")

    op.drop_constraint("ck_guarantee_ledger_status", "guarantee_ledger", type_="check")
    op.create_check_constraint(
        "ck_guarantee_ledger_status",
        "guarantee_ledger",
        "status IN ('computed','extended','refund_pending','settled','na')",
    )
    op.drop_column("guarantee_ledger", "extension_days_total")

    op.drop_constraint("ck_customers_wallet_balance_nonneg", "customers", type_="check")

    op.drop_constraint("ck_wallet_transactions_type", "wallet_transactions", type_="check")
    op.create_check_constraint(
        "ck_wallet_transactions_type",
        "wallet_transactions",
        "type IN ('topup','consumption','admin_adjustment')",
    )
    op.drop_index("uq_wallet_transactions_bounce_refund_application", table_name="wallet_transactions")
    op.drop_index("ix_wallet_transactions_application_id", table_name="wallet_transactions")
    op.drop_column("wallet_transactions", "application_id")
