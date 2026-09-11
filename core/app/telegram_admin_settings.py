"""B9/B5: ⚙️ الإعدادات ببوت الأدمن — بيانات التحويل البنكي (app_settings)،
الباقات (products: سعر/أيام/عدد تقديمات/إظهار-إخفاء)، ورقم واتساب الدعم.

للمالك حصرًا — فحص `is_owner_chat` يقع بملف `telegram_admin.py` **قبل**
استدعاء أي دالة هنا (نفس نمط `telegram_admin_delegates.py`: لا تكرار للفحص
بهذا الملف). لا إدارة جلسة هنا أيضًا — نفس السبب، راجع docstring
`telegram_admin_search.py`.

`support_whatsapp()` هنا هي نقطة القراءة الموحَّدة الجديدة التي يحلّ بها
الدليل رقم الواتساب المكتوب بالكود مباشرة بعدة ملفات (B4/B6 القادمتين
ستستوردانها بدل التكرار).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text as sql_text

from app.discovery import get_engine
from app.telegram_client import TelegramClient
from app.telegram_nav import nav_rows

BANK_FIELD_LABELS = {
    "bank_name": "اسم البنك",
    "account_holder": "اسم صاحب الحساب",
    "iban": "رقم الآيبان (IBAN)",
}
# ملاحظة أمان: هذا القاموس هو القائمة البيضاء الوحيدة لأسماء أعمدة products
# القابلة للتعديل عبر الإعدادات — f-string بناء SQL أدناه يعتمد عليه حصرًا
# (لا مدخل مستخدم يصل لاسم عمود مباشرة).
PKG_FIELD_LABELS = {
    "price_sar": "السعر (ريال)",
    "days": "عدد الأيام",
    "applications_included": "عدد التقديمات",
}

_DEFAULT_SUPPORT_WHATSAPP = "+966544161255"


def get_setting(key: str) -> str | None:
    engine = get_engine()
    with engine.connect() as conn:
        return conn.execute(sql_text("SELECT value FROM app_settings WHERE key = :k"), {"k": key}).scalar()


def set_setting(key: str, value: str) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            sql_text(
                "INSERT INTO app_settings (key, value, updated_at) VALUES (:k, :v, now()) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
            ),
            {"k": key, "v": value},
        )


def support_whatsapp() -> str:
    """B9/B4/B6: يحلّ محل كل رقم واتساب مكتوب بالكود مباشرة بأي ملف آخر —
    راجع الدليل. قيمة افتراضية احتياطية فقط لو غاب الصف المبذور بـ0017."""
    return get_setting("support_whatsapp") or _DEFAULT_SUPPORT_WHATSAPP


def _mask_iban(iban: str | None) -> str:
    if not iban:
        return "غير محدَّد"
    digits = iban.strip()
    if len(digits) <= 6:
        return digits
    return f"{digits[:4]} **** **** {digits[-4:]}"


# =========================================================================
# القائمة الرئيسية للإعدادات
# =========================================================================


async def reply_settings_menu(client: TelegramClient, chat_id: int) -> None:
    buttons = [
        [{"text": "🏦 بيانات التحويل", "callback_data": "settings:bank"}],
        [{"text": "💼 الباقات", "callback_data": "settings:packages"}],
        [{"text": "📞 رقم الواتساب", "callback_data": "settings:whatsapp"}],
    ]
    buttons.extend(nav_rows(None, "admin:menu"))
    await client.send_message(chat_id, "⚙️ الإعدادات — اختر ما تريد تعديله:", buttons=buttons)


# =========================================================================
# 🏦 بيانات التحويل
# =========================================================================


async def reply_bank_details(client: TelegramClient, chat_id: int) -> None:
    lines = [
        "🏦 بيانات التحويل الحالية:",
        f"البنك: {get_setting('bank_name') or 'غير محدَّد'}",
        f"صاحب الحساب: {get_setting('account_holder') or 'غير محدَّد'}",
        f"IBAN: {_mask_iban(get_setting('iban'))}",
    ]
    buttons = [
        [{"text": "✏️ اسم البنك", "callback_data": "settings:bank_edit:bank_name"}],
        [{"text": "✏️ صاحب الحساب", "callback_data": "settings:bank_edit:account_holder"}],
        [{"text": "✏️ IBAN", "callback_data": "settings:bank_edit:iban"}],
    ]
    buttons.extend(nav_rows("admin:settings", "admin:menu"))
    await client.send_message(chat_id, "\n".join(lines), buttons=buttons)


async def apply_bank_field(client: TelegramClient, chat_id: int, field: str, value: str) -> None:
    value = value.strip()
    if field not in BANK_FIELD_LABELS or not value:
        await client.send_message(chat_id, "⚠️ قيمة غير صالحة — لم يُحفَظ شيء.")
        await reply_bank_details(client, chat_id)
        return
    set_setting(field, value)
    await client.send_message(chat_id, f"✅ تم تحديث {BANK_FIELD_LABELS[field]}.")
    await reply_bank_details(client, chat_id)


# =========================================================================
# 📞 رقم واتساب الدعم
# =========================================================================


async def reply_whatsapp_prompt(client: TelegramClient, chat_id: int) -> None:
    await client.send_message(
        chat_id,
        f"الرقم الحالي: {support_whatsapp()}\nاكتب رقم الواتساب الجديد (مع رمز الدولة، مثال: +9665xxxxxxxx):",
        buttons=nav_rows("admin:settings", "admin:menu"),
    )


async def apply_whatsapp(client: TelegramClient, chat_id: int, value: str) -> None:
    value = value.strip()
    if not value:
        await client.send_message(chat_id, "⚠️ رقم فارغ — لم يُحفَظ شيء.")
        await reply_settings_menu(client, chat_id)
        return
    set_setting("support_whatsapp", value)
    await client.send_message(chat_id, f"✅ تم تحديث رقم الواتساب إلى {value}.")


# =========================================================================
# 💼 الباقات (products)
# =========================================================================


async def reply_packages_menu(client: TelegramClient, chat_id: int) -> None:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            sql_text("SELECT code, name_ar, price_sar, active FROM products ORDER BY code")
        ).mappings().all()
    if not rows:
        await client.send_message(chat_id, "لا توجد باقات بعد.", buttons=nav_rows("admin:settings", "admin:menu"))
        return
    buttons = []
    for r in rows:
        price = f"{float(r['price_sar']):.0f} ريال" if r["price_sar"] is not None else "بلا سعر"
        flag = "🟢" if r["active"] else "⚪️"
        buttons.append(
            [{"text": f"{flag} {r['name_ar']} — {price}", "callback_data": f"settings:pkg:{r['code']}"}]
        )
    buttons.extend(nav_rows("admin:settings", "admin:menu"))
    await client.send_message(chat_id, "💼 الباقات — اضغط على باقة لتعديلها:", buttons=buttons)


def _fetch_product(code: str) -> dict[str, Any] | None:
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(sql_text("SELECT * FROM products WHERE code = :c"), {"c": code}).mappings().first()
    return dict(row) if row else None


async def reply_package_detail(client: TelegramClient, chat_id: int, code: str) -> None:
    p = _fetch_product(code)
    if not p:
        await client.send_message(chat_id, "⚠️ باقة غير موجودة.", buttons=nav_rows("settings:packages", "admin:menu"))
        return
    price = f"{float(p['price_sar']):.2f} ريال" if p["price_sar"] is not None else "بلا سعر (لن تظهر للعميل)"
    lines = [
        f"💼 {p['name_ar']} ({p['code']})",
        f"النوع: {p['type']}",
        f"السعر: {price}",
        f"الأيام: {p['days'] if p['days'] is not None else '-'}",
        f"عدد التقديمات: {p['applications_included'] if p['applications_included'] is not None else '-'}",
        f"الحالة: {'ظاهرة للعميل 🟢' if p['active'] else 'مخفية ⚪️'}",
    ]
    buttons = [
        [{"text": "✏️ السعر", "callback_data": f"settings:pkg_edit:{code}:price_sar"}],
        [{"text": "✏️ الأيام", "callback_data": f"settings:pkg_edit:{code}:days"}],
        [{"text": "✏️ عدد التقديمات", "callback_data": f"settings:pkg_edit:{code}:applications_included"}],
        [{"text": "⚪️ إخفاء" if p["active"] else "🟢 إظهار", "callback_data": f"settings:pkg_toggle:{code}"}],
    ]
    buttons.extend(nav_rows("settings:packages", "admin:menu"))
    await client.send_message(chat_id, "\n".join(lines), buttons=buttons)


async def toggle_package_active(client: TelegramClient, chat_id: int, code: str) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            sql_text("UPDATE products SET active = NOT active WHERE code = :c RETURNING active"), {"c": code}
        ).first()
    if not row:
        await client.send_message(chat_id, "⚠️ باقة غير موجودة.", buttons=nav_rows("settings:packages", "admin:menu"))
        return
    await reply_package_detail(client, chat_id, code)


async def apply_package_field(
    client: TelegramClient, chat_id: int, code: str, field: str, value: float | int
) -> None:
    """`value` وصلت مُتحقَّقة ومُحوَّلة رقميًا مسبقًا من `telegram_admin.py`
    (نفس نمط إعادة الطلب عند إدخال غير رقمي بـ`extend_days`/B9-A6) — هذا
    الملف يكتب مباشرة. `field` من `PKG_FIELD_LABELS` حصرًا (قائمة بيضاء)."""
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            sql_text(f"UPDATE products SET {field} = :v WHERE code = :c RETURNING code"),
            {"v": value, "c": code},
        ).first()
    if not row:
        await client.send_message(chat_id, "⚠️ باقة غير موجودة.", buttons=nav_rows("settings:packages", "admin:menu"))
        return
    await client.send_message(chat_id, f"✅ تم تحديث {PKG_FIELD_LABELS[field]}.")
    await reply_package_detail(client, chat_id, code)
