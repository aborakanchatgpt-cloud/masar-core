"""B8 (إزالة n8n — الإرسال الصادر عبر تيليجرام مباشرة): عمودان اختياريان
على subscriptions يدعمان idempotency جولة الاحتفاظ بالعملاء الجديدة
core/app/retention.py (بديل مباشر لركفلو n8n السابق
job-bot-customer-retention-auto__B2PYcIMOQN7i5VXA.json):

    1. reminder_sent_date — آخر تاريخ (وقت رياض) أُرسل فيه تذكير "اشتراكك
       ينتهي قريبًا" لهذا الاشتراك؛ يمنع تكرار نفس التذكير أكثر من مرة
       باليوم الواحد (نفس فحص `lastRenewalReminderSent !== todayIso` السابق
       بـn8n، لكن بعمود قاعدة بيانات حقيقي بدل جدول n8n Data Table المهجور).
    2. expiry_notified_at — طابع زمني وحيد يُسجَّل أول مرة يُشعَر فيها
       العميل/الأدمن بأن الاشتراك انتقل لحالة 'closed' نهائيًا (بلوغ الهدف
       أو تعويض معلّق بعد استنفاد المهلة الإلزامية — راجع core/app/guarantee.py)
       — يمنع إشعار "انتهى اشتراكك" المكرّر لنفس الفترة عند كل تشغيلة يومية.

كلا العمودين اختياريان (nullable، بلا قيمة افتراضية غير NULL) — هذا الترحيل
لا يلمس عمود subscriptions.status ولا أي قيد CHECK حالي؛ قرار متى/كيف تتغيّر
حالة الاشتراك نفسها يبقى حصريًا بـguarantee.py (لا تكرار منطق — راجع
core/app/retention.py قسم "لماذا لا تُعدَّل subscriptions.status من هنا").

Revision ID: 0014_b8_telegram_outbound
Revises: 0013_telegram_sessions
Create Date: 2026-09-10

ملاحظة دمج (B8): كانت هذه الترحيلة مرقّمة أصلًا 0013 (نفس رقم ترحيلة
telegram_sessions بتيار الوارد — تطوير متوازٍ منفصل، كلاهما اعتمد
0012_b7_catalog كأساس). أُعيد ترقيمها 0014 وdown_revision إلى
0013_telegram_sessions عند الدمج لتبقى سلسلة alembic خطّية واحدة — لا
تعارض مخطّط فعليًا (جدولان/أعمدة مختلفة تمامًا: telegram_sessions جدول
جديد، هذه الترحيلة تضيف عمودين لـsubscriptions)، فقط تصادم رقم إصدار.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0014_b8_telegram_outbound"
down_revision: Union[str, None] = "0013_telegram_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("subscriptions", sa.Column("reminder_sent_date", sa.Date(), nullable=True))
    op.add_column(
        "subscriptions", sa.Column("expiry_notified_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "expiry_notified_at")
    op.drop_column("subscriptions", "reminder_sent_date")
