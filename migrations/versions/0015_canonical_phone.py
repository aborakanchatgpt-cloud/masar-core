"""B9/A1: توحيد صيغة أرقام الجوال المخزّنة مسبقًا بجدول customers.

المشكلة: customers.phone كان يُخزّن بأي صيغة كتبها أحمد (غالبًا 05xxxxxxxx)،
بينما ربط تيليجرام يطابق الرقم الذي يرسله زر "مشاركة جهة الاتصال" (صيغة
دولية 966xxxxxxxxx) بمطابقة حرفية — راجع app/phone.py وcore/app/
telegram_onboarding.py::_link_telegram_by_phone للتفاصيل الكاملة والإصلاح
بمنطق التطبيق (كل كتابة/قراءة جديدة تمر الآن عبر canonical_phone).

هذا الترحيل يعيد كتابة الصفوف *الموجودة فعلًا* بنفس منطق canonical_phone
(معاد هنا بـPython صراحة، بلا استيراد app.phone من داخل الترحيلة، حتى لا
تتأثر الترحيلة مستقبلًا بأي تعديل على تلك الدالة) حتى تتوحّد الصيغة لكل
الصفوف القديمة والجديدة معًا.

Revision ID: 0015_canonical_phone
Revises: 0014_b8_telegram_outbound
Create Date: 2026-09-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0015_canonical_phone"
down_revision: Union[str, None] = "0014_b8_telegram_outbound"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _canonical(raw: str) -> str:
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    if not digits:
        return ""
    if digits.startswith("00") and len(digits) == 14 and digits[2:5] == "966":
        digits = digits[2:]
    if len(digits) == 10 and digits.startswith("0"):
        digits = "966" + digits[1:]
    elif len(digits) == 9 and digits.startswith("5"):
        digits = "966" + digits
    return digits


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, phone FROM customers WHERE phone IS NOT NULL")).fetchall()
    for row in rows:
        new_phone = _canonical(row.phone)
        if new_phone and new_phone != row.phone:
            conn.execute(
                sa.text("UPDATE customers SET phone = :phone WHERE id = :id"),
                {"phone": new_phone, "id": row.id},
            )


def downgrade() -> None:
    # لا رجوع منطقي (لا نعرف الصيغة الأصلية قبل التوحيد) — عملية بلا أثر
    # جانبي على المخطّط نفسه، فقط بيانات أُعيدت كتابتها لصيغة أفضل. downgrade
    # يُترك بلا عملية عمدًا.
    pass
