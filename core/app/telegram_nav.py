"""B9/B1: بنية التنقّل المشتركة (رجوع/رئيسية) للبوتين.

`nav_rows(back_cb, home_cb)` يُرجع صفًا أخيرًا ثابتًا يُلحَق بأي لوحة أزرار
أو طلب إدخال نصي: "◀️ رجوع" (إن وُجدت خطوة سابقة معقولة) ثم "🏠 القائمة
الرئيسية" دومًا. `home_cb`: "menu:home" (بوت العميل) / "admin:menu"
(بوت الأدمن) — راجع الدليل §B1.

**قرار تنفيذي (هذه الدفعة، B1+B2):** بوت الأدمن يستخدم `nav_rows` بأهداف
"رجوع" مباشرة ثابتة لكل خطوة (مثال: خطوة الجوال بتسجيل عميل جديد تُرجع
بـ"admin:new_customer" لإعادة طلب الاسم) بدل مكدس عام محفوظ بالجلسة —
تدفّقات بوت الأدمن كلها ضحلة (خطوة أو خطوتان)، فهدف ثابت يكفي ويبسّط
الاختبار. `push_nav`/`pop_nav` أدناه احتياطيّان جاهزان لتدفّقات أعمق
(onboarding العميل الجديد، B3/B4 القادمتين) حيث خطوة "رجوع" تعني فعليًا
"عد لآخر شاشة معروضة" لا هدفًا واحدًا ثابتًا.
"""
from __future__ import annotations

NAV_STACK_MAX = 10


def nav_rows(back_cb: str | None, home_cb: str) -> list[list[dict[str, str]]]:
    """صفّ أزرار أخير جاهز للإلحاق (`buttons.extend(nav_rows(...))` أو
    كقيمة `buttons=` مباشرة لرسالة بلا أزرار أخرى)."""
    row: list[dict[str, str]] = []
    if back_cb:
        row.append({"text": "◀️ رجوع", "callback_data": back_cb})
    row.append({"text": "🏠 القائمة الرئيسية", "callback_data": home_cb})
    return [row]


def push_nav(data: dict, step: str, extra: dict | None = None) -> dict:
    """يضيف خطوة حالية لمكدس الرجوع (`data["nav"]`، أقصى NAV_STACK_MAX)
    قبل الانتقال لخطوة فرعية جديدة — يُستدعى بنقطة الانتقال نفسها (عرض
    شاشة/قائمة فرعية)، لا بكل تحديث بيانات صغير داخل نفس الشاشة."""
    stack = list(data.get("nav") or [])
    stack.append({"step": step, "extra": extra or {}})
    data["nav"] = stack[-NAV_STACK_MAX:]
    return data


def pop_nav(data: dict) -> tuple[str, dict] | None:
    """يزيل ويُرجع آخر خطوة بمكدس الرجوع كـ(step, extra)، أو None إن كان
    فارغًا (رجوع من أول خطوة معناه القائمة الرئيسية/النشطة)."""
    stack = list(data.get("nav") or [])
    if not stack:
        return None
    last = stack.pop()
    data["nav"] = stack
    return last["step"], (last.get("extra") or {})
