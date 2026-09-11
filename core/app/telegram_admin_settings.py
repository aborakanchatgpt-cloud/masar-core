"""B9/B5: ⚙️ الإعدادات ببوت الأدمن — بيانات التحويل البنكي (جدول
`bank_accounts` منذ ترحيلة B3-متابعة 0018، راجع أدناه)، الباقات (products:
سعر/أيام/عدد تقديمات/إظهار-إخفاء)، ورقم واتساب الدعم.

للمالك حصرًا — فحص `is_owner_chat` يقع بملف `telegram_admin.py` **قبل**
استدعاء أي دالة هنا (نفس نمط `telegram_admin_delegates.py`: لا تكرار للفحص
بهذا الملف). لا إدارة جلسة هنا أيضًا — نفس السبب، راجع docstring
`telegram_admin_search.py`.

`support_whatsapp()` هنا هي نقطة القراءة الموحّدة الجديدة التي يحلّ بها
الدليل رقم الواتساب المكتوب بالكود مباشرة بعدة ملفات (B4/B6 القادمتين
ستستوردانها بدل التكرار.

**B3-متابعة (دعم أكثر من بنك واحد):** بيانات التحويل انتقلت من ثلاثة مفاتيح
أحادية بـ`app_settings` (bank_name/account_holder/iban — تبقى بجدولها بلا
استخدام، لا حذف) إلى جدول `bank_accounts` (ترحيلة 0018) — قائمة حسابات،
كل واحد بحالة نشط/متوقف (`active`، soft-delete نفس نمط `admin_delegates`،
لا حذف فعلي). `active_bank_accounts()` هي نقطة القراءة الموحّدة الجديدة
التي يستخدمها `telegram_payments.py` لعرض كل الحسابات النشطة للعميل دفعة
واحدة عند الدفع.

**B3-متابعة٢ (رقم الحساب + لغة الاسم، ترحيلة 0019):** بطلب أحمد — أُضيف
عمود `account_number` (رقم حساب عادي، بجانب الآيبان لا بدلًا عنه)، وعمود
`name_language` (ar/en، **لعرض/تنظيم داخلي للأدمن فقط** — لا يُعرض اسم
البنك للعميل بلغتين معًا إطلاقًا، هذا العمود لا يُستخدَم بأي رسالة عميل).
كلا `account_number`/`iban` أصبحا اختياريَّين (nullable) — يكفي تعبئة واحد
منهما، بقيد CHECK على مستوى القاعدة (`ck_bank_accounts_number_or_iban`)
يمنع حفظ حساب بكلا الحقلين فارغَين معًا (دفاع بالعمق فوق تحقّق التدفّق هنا).
تدفّق ➕ إضافة بنك جديد أصبح 4 خطوات نصّية + خطوة أزرار لللغة، مع أزرار
"⏭️ تخطي" باختياريّة رقم الحساب/الآيبان — راجع `handle_bank_step_text`/
`handle_bank_callback` أدناه لتفصيل التحقّق (رفض تخطي الآيبان لو رقم
الحساب أيضًا فارغ، والعكس غير ممكن أصلًا لأن رقم الحساب يُسأل أولًا).
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
    "account_number": "رقم الحساب",
    "iban": "رقم الآيبان (IBAN)",
}
# ملاحظة أمان: هذا القاموس هو القائمة البيضاء الوحيدة لأسماء أعمدة bank_accounts
# القابلة للتعديل عبر الإعدادات — f-string بناء SQL بـ`apply_bank_field` أدناه
# يعتمد عليه حصرًا (لا مدخل مستخدم يصل لاسم عمود مباشرة). `name_language` ليست
# هنا عمدًا — لها مسار تعديل خاص (`toggle_bank_language`، تبديل لا كتابة حرّة).
PKG_FIELD_LABELS = {
    "price_sar": "السعر (ريال)",
    "days": "عدد الأيام",
    "applications_included": "عدد التقديمات",
}

BANK_LANG_LABELS = {"ar": "عربي", "en": "English"}

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


def _mask_value(value: str | None) -> str:
    if not value:
        return "غير محدَّد"
    digits = value.strip()
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
# 🏦 بيانات التحويل — قائمة حسابات بنكية (B3-متابعة، جدول bank_accounts)
# =========================================================================


def _fetch_bank_accounts() -> list[dict[str, Any]]:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(sql_text("SELECT * FROM bank_accounts ORDER BY id")).mappings().all()
    return [dict(r) for r in rows]


def active_bank_accounts() -> list[dict[str, Any]]:
    """نقطة القراءة الموحّدة التي يستخدمها `telegram_payments.py` لعرض كل
    الحسابات النشطة (`active = true`) دفعة واحدة على العميل وقت الدفع."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            sql_text("SELECT * FROM bank_accounts WHERE active = true ORDER BY id")
        ).mappings().all()
    return [dict(r) for r in rows]


def _fetch_bank_account(account_id: int) -> dict[str, Any] | None:
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(sql_text("SELECT * FROM bank_accounts WHERE id = :id"), {"id": account_id}).mappings().first()
    return dict(row) if row else None


async def reply_bank_list(client: TelegramClient, chat_id: int) -> None:
    accounts = _fetch_bank_accounts()
    buttons = [
        [
            {
                "text": f"{'🟢' if a['active'] else '⚪️'} {a['bank_name']} — {a['account_holder']}",
                "callback_data": f"settings:bank:{a['id']}",
            }
        ]
        for a in accounts
    ]
    buttons.append([{"text": "➕ إضافة بنك جديد", "callback_data": "settings:bank_add"}])
    buttons.extend(nav_rows("admin:settings", "admin:menu"))
    header = (
        "🏦 بيانات التحويل — الحسابات الحالية (🟢 يظهر للعميل، ⚪️ متوقف):"
        if accounts
        else "🏦 بيانات التحويل — ما فيه أي حساب بنكي بعد. أضف واحدًا على الأقل حتى يقدر العميل يدفع:"
    )
    await client.send_message(chat_id, header, buttons=buttons)


async def reply_bank_account_detail(client: TelegramClient, chat_id: int, account_id: int) -> None:
    a = _fetch_bank_account(account_id)
    if not a:
        await client.send_message(chat_id, "⚠️ حساب بنكي غير موجود.", buttons=nav_rows("settings:bank", "admin:menu"))
        return
    lang = a.get("name_language")
    lang_suffix = f" ({BANK_LANG_LABELS[lang]})" if lang in BANK_LANG_LABELS else ""
    lines = [
        f"🏦 {a['bank_name']}{lang_suffix}",
        f"صاحب الحساب: {a['account_holder']}",
        f"رقم الحساب: {_mask_value(a.get('account_number'))}",
        f"IBAN: {_mask_value(a['iban'])}",
        f"الحالة: {'نشط 🟢 — يظهر للعميل' if a['active'] else 'متوقف ⚪️ — لا يظهر للعميل'}",
    ]
    buttons = [
        [{"text": "✏️ اسم البنك", "callback_data": f"settings:bank_edit:{account_id}:bank_name"}],
        [{"text": "✏️ صاحب الحساب", "callback_data": f"settings:bank_edit:{account_id}:account_holder"}],
        [{"text": "✏️ رقم الحساب", "callback_data": f"settings:bank_edit:{account_id}:account_number"}],
        [{"text": "✏️ IBAN", "callback_data": f"settings:bank_edit:{account_id}:iban"}],
        [
            {
                "text": f"🔤 لغة الاسم: {BANK_LANG_LABELS.get(lang, 'غير محدّدة')}",
                "callback_data": f"settings:bank_lang_toggle:{account_id}",
            }
        ],
        [{"text": "⚪️ إيقاف" if a["active"] else "🟢 تفعيل", "callback_data": f"settings:bank_toggle:{account_id}"}],
    ]
    buttons.extend(nav_rows("settings:bank", "admin:menu"))
    await client.send_message(chat_id, "\n".join(lines), buttons=buttons)


async def apply_bank_field(client: TelegramClient, chat_id: int, account_id: int, field: str, value: str) -> None:
    """`field` من `BANK_FIELD_LABELS` حصرًا (قائمة بيضاء) — نفس نمط
    `apply_package_field` أدناه، f-string بناء SQL يعتمد عليها حصرًا.

    ملاحظة: يرفض قيمة فارغة دومًا (نفس السلوك قبل B3-متابعة٢) — تفريغ
    account_number/iban لحساب موجود مسبقًا غير مدعوم من هنا عمدًا (قد
    يخالف قيد CHECK لو كان الحقل الآخر فارغًا أيضًا وقتها)؛ لو احتاج أحمد
    تفريغ أحدهما مستقبلًا، ذلك تحسين منفصل يتحقّق من الحقل الآخر أولًا."""
    value = value.strip()
    if field not in BANK_FIELD_LABELS or not value:
        await client.send_message(chat_id, "⚠️ قيمة غير صالحة — لم يُحفَظ شيء.")
        await reply_bank_account_detail(client, chat_id, account_id)
        return
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            sql_text(f"UPDATE bank_accounts SET {field} = :v WHERE id = :id RETURNING id"),
            {"v": value, "id": account_id},
        ).first()
    if not row:
        await client.send_message(chat_id, "⚠️ حساب بنكي غير موجود.", buttons=nav_rows("settings:bank", "admin:menu"))
        return
    await client.send_message(chat_id, f"✅ تم تحديث {BANK_FIELD_LABELS[field]}.")
    await reply_bank_account_detail(client, chat_id, account_id)


async def toggle_bank_active(client: TelegramClient, chat_id: int, account_id: int) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            sql_text("UPDATE bank_accounts SET active = NOT active WHERE id = :id RETURNING active"),
            {"id": account_id},
        ).first()
    if not row:
        await client.send_message(chat_id, "⚠️ حساب بنكي غير موجود.", buttons=nav_rows("settings:bank", "admin:menu"))
        return
    await reply_bank_account_detail(client, chat_id, account_id)


async def toggle_bank_language(client: TelegramClient, chat_id: int, account_id: int) -> None:
    """B3-متابعة٢: تبديل ar↔en (لا كتابة حرّة — نفس فلسفة toggle_bank_active).
    حساب بلا لغة محدَّدة بعد (`NULL`) يُعامَل كـ'en' فيتحوّل لـ'ar' أول ضغطة."""
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            sql_text(
                "UPDATE bank_accounts SET name_language = "
                "CASE WHEN name_language = 'ar' THEN 'en' ELSE 'ar' END "
                "WHERE id = :id RETURNING name_language"
            ),
            {"id": account_id},
        ).first()
    if not row:
        await client.send_message(chat_id, "⚠️ حساب بنكي غير موجود.", buttons=nav_rows("settings:bank", "admin:menu"))
        return
    await reply_bank_account_detail(client, chat_id, account_id)


def create_bank_account(
    bank_name: str,
    account_holder: str,
    *,
    account_number: str | None = None,
    iban: str | None = None,
    name_language: str | None = None,
) -> int:
    """B3-متابعة٢: `account_number`/`iban` أصبحا اختياريَّين — يكفي واحد
    منهما (قيد CHECK `ck_bank_accounts_number_or_iban` بترحيلة 0019 يرفض
    الاثنين فارغَين معًا كدفاع بالعمق، حتى لو استُدعيت هذه الدالة من مسار
    آخر مستقبلًا بلا مرور بتحقّق `handle_bank_step_text` أدناه)."""
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            sql_text(
                "INSERT INTO bank_accounts (bank_name, account_holder, account_number, iban, name_language) "
                "VALUES (:b, :h, :n, :i, :l) RETURNING id"
            ),
            {
                "b": bank_name.strip(),
                "h": account_holder.strip(),
                "n": account_number.strip() if account_number else None,
                "i": iban.strip() if iban else None,
                "l": name_language if name_language in BANK_LANG_LABELS else None,
            },
        ).first()
    return row[0]


# -------------------------------------------------------------------------
# توجيه callback_data/خطوات النصّ الخاصة بـ🏦 بيانات التحويل — منقول من
# telegram_admin.py بسبب حجم repo_write فقط (نفس نمط فصل telegram_admin_*
# الأخرى، راجع الدليل §0) لا لتغيير معماري: فحص is_owner_chat وحفظ/مسح
# الجلسة يبقيان مسؤولية المستدعي (نُمرّر save_session/get_session/clear_session).
# -------------------------------------------------------------------------


async def handle_bank_callback(
    data: str, chat_id: int, client: TelegramClient, save_session, get_session, clear_session
) -> None:
    if data == "settings:bank":
        await reply_bank_list(client, chat_id)
        return
    if data == "settings:bank_add":
        save_session(chat_id, "bank_add_name", {})
        await client.send_message(chat_id, "اكتب اسم البنك:", buttons=nav_rows("settings:bank", "admin:menu"))
        return
    if data.startswith("settings:bank_lang_toggle:"):
        account_id = int(data[len("settings:bank_lang_toggle:") :])
        await toggle_bank_language(client, chat_id, account_id)
        return
    # اختيار لغة اسم البنك بتدفّق الإضافة (خطوة "bank_add_lang" — أزرار لا نص).
    if data.startswith("settings:bank_lang:"):
        step, sess_data = get_session(chat_id)
        if step != "bank_add_lang":
            return
        lang = data[len("settings:bank_lang:") :]
        if lang not in BANK_LANG_LABELS:
            return
        save_session(chat_id, "bank_add_holder", {**sess_data, "name_language": lang})
        await client.send_message(
            chat_id, "تمام. الآن اكتب اسم صاحب الحساب:", buttons=nav_rows("settings:bank", "admin:menu")
        )
        return
    # "⏭️ تخطي" باختياريّة رقم الحساب/الآيبان بتدفّق الإضافة — راجع docstring
    # الملف: تخطي الآيبان مرفوض لو رقم الحساب أيضًا فارغ (يجب واحد على الأقل).
    if data.startswith("settings:bank_skip:"):
        which = data[len("settings:bank_skip:") :]
        step, sess_data = get_session(chat_id)
        if which == "number" and step == "bank_add_number":
            save_session(chat_id, "bank_add_iban", {**sess_data, "account_number": None})
            buttons = [[{"text": "⏭️ تخطي", "callback_data": "settings:bank_skip:iban"}]]
            buttons.extend(nav_rows("settings:bank", "admin:menu"))
            await client.send_message(
                chat_id, "وأخيرًا اكتب رقم الآيبان (IBAN) — أو تخطَّ إذا اكتفيت برقم الحساب:", buttons=buttons
            )
            return
        if which == "iban" and step == "bank_add_iban":
            if not sess_data.get("account_number"):
                await client.send_message(
                    chat_id, "⚠️ يجب إدخال رقم الحساب أو الآيبان على الأقل — اكتب رقم الآيبان:"
                )
                return
            clear_session(chat_id)
            account_id = create_bank_account(
                sess_data.get("bank_name", ""),
                sess_data.get("account_holder", ""),
                account_number=sess_data.get("account_number"),
                iban=None,
                name_language=sess_data.get("name_language"),
            )
            await client.send_message(chat_id, "✅ تمت إضافة الحساب البنكي.")
            await reply_bank_account_detail(client, chat_id, account_id)
            return
        return
    if data.startswith("settings:bank_toggle:"):
        account_id = int(data[len("settings:bank_toggle:") :])
        await toggle_bank_active(client, chat_id, account_id)
        return
    if data.startswith("settings:bank_edit:"):
        _, _, account_id_str, field = data.split(":", 3)
        save_session(chat_id, "settings_bank_edit", {"id": int(account_id_str), "field": field})
        label = BANK_FIELD_LABELS.get(field, field)
        await client.send_message(
            chat_id, f"اكتب القيمة الجديدة لـ{label}:", buttons=nav_rows("settings:bank", "admin:menu")
        )
        return
    # "settings:bank:{id}" — فتح تفاصيل حساب (الحرف التالي لـ"settings:bank"
    # هنا ":" لا "_"، فلا تصادم مع الفروع أعلاه.
    if data.startswith("settings:bank:"):
        account_id = int(data[len("settings:bank:") :])
        await reply_bank_account_detail(client, chat_id, account_id)
        return


BANK_STEP_NAMES = {
    "bank_add_name",
    "bank_add_lang",
    "bank_add_holder",
    "bank_add_number",
    "bank_add_iban",
    "settings_bank_edit",
}


async def handle_bank_step_text(
    step: str, chat_id: int, client: TelegramClient, data: dict[str, Any], text: str, save_session, clear_session
) -> None:
    """يُستدعى فقط لخطوة ضمن `BANK_STEP_NAMES` (المستدعي يتحقّق أولًا)."""
    if step == "bank_add_name":
        save_session(chat_id, "bank_add_lang", {"bank_name": text})
        buttons = [
            [
                {"text": "🇸🇦 عربي", "callback_data": "settings:bank_lang:ar"},
                {"text": "🇬🇧 English", "callback_data": "settings:bank_lang:en"},
            ]
        ]
        buttons.extend(nav_rows("settings:bank", "admin:menu"))
        await client.send_message(
            chat_id,
            "تمام. اسم البنك — تنظيم داخلي فقط (ما يظهر للعميل أبدًا بلغتين معًا)، اختر لغته:",
            buttons=buttons,
        )
        return
    if step == "bank_add_lang":
        # وصل نص بدل ضغط زر — أعد عرض الأزرار بدل قبول نص حرّ باسم "لغة".
        buttons = [
            [
                {"text": "🇸🇦 عربي", "callback_data": "settings:bank_lang:ar"},
                {"text": "🇬🇧 English", "callback_data": "settings:bank_lang:en"},
            ]
        ]
        buttons.extend(nav_rows("settings:bank", "admin:menu"))
        await client.send_message(chat_id, "اختر عبر الأزرار 👇", buttons=buttons)
        return
    if step == "bank_add_holder":
        save_session(chat_id, "bank_add_number", {**data, "account_holder": text})
        buttons = [[{"text": "⏭️ تخطي", "callback_data": "settings:bank_skip:number"}]]
        buttons.extend(nav_rows("settings:bank", "admin:menu"))
        await client.send_message(
            chat_id, "اكتب رقم الحساب (أو تخطَّ هذي الخطوة إذا بتكتفي بالآيبان):", buttons=buttons
        )
        return
    if step == "bank_add_number":
        save_session(chat_id, "bank_add_iban", {**data, "account_number": text})
        buttons = [[{"text": "⏭️ تخطي", "callback_data": "settings:bank_skip:iban"}]]
        buttons.extend(nav_rows("settings:bank", "admin:menu"))
        await client.send_message(
            chat_id, "وأخيرًا اكتب رقم الآيبان (IBAN) — أو تخطَّ إذا اكتفيت برقم الحساب:", buttons=buttons
        )
        return
    if step == "bank_add_iban":
        clear_session(chat_id)
        account_id = create_bank_account(
            data.get("bank_name", ""),
            data.get("account_holder", ""),
            account_number=data.get("account_number"),
            iban=text,
            name_language=data.get("name_language"),
        )
        await client.send_message(chat_id, "✅ تمت إضافة الحساب البنكي.")
        await reply_bank_account_detail(client, chat_id, account_id)
        return
    if step == "settings_bank_edit":
        account_id = int(data.get("id", 0))
        field = data.get("field", "")
        clear_session(chat_id)
        await apply_bank_field(client, chat_id, account_id, field, text)
        return


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
