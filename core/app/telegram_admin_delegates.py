"""B9/B2: منطق المفوّضين (`admin_delegates`) — ربط تلقائي عند أول رسالة من
مفوّض غير مربوط بعد، وCRUD خفيف (إضافة/إزالة). فُصل عن `app.telegram_admin`
(الذي يستدعي هذه الدوال من قائمة "👥 المفوّضون" وتوجيه الرسائل) حصرًا لإبقاء
حجم كل ملف دون الحد التوثيقي لدفعة واحدة عبر `repo_write` (~25 ألف حرف) —
لا اعتماد دائري: هذا الملف لا يستورد شيئًا من telegram_admin.py.
"""
from __future__ import annotations

from sqlalchemy import text as sql_text

from app.discovery import get_engine
from app.phone import canonical_phone
from app.telegram_client import ChatEvent, TelegramClient
from app.telegram_nav import nav_rows


async def try_link_delegate(event: ChatEvent) -> str | None:
    """يحاول ربط مفوّض بانتظار الربط (`telegram_chat_id IS NULL`) — إمّا
    عبر تطابق @username (lower-case، بلا "@") أو عبر مشاركة جهة اتصال
    حقيقية (زر Telegram request_contact، يتحقّق أنها جهة اتصال المُرسِل
    نفسه: `from_user_id == chat_id`، نفس نمط telegram_onboarding._step_phone
    الخاص بربط عميل موجود مسبقًا). يُستدعى فقط لمحادثة ليست أصلًا `is_admin_chat`
    (لا مالك ولا مفوّض مربوط مسبقًا)، قبل الصمت التام لأي شخص آخر (قرار
    أحمد: "إذا لا، لا يتجاوب معه أبدًا"). يُرجع اسم المفوّض عند نجاح
    الربط، أو None (لا تطابق أو لا معلومات كافية بالرسالة للمطابقة)."""
    username = (event.from_username or "").strip().lstrip("@").lower()
    phone_digits = ""
    if event.contact and event.contact.get("phone_number") and event.from_user_id == event.chat_id:
        phone_digits = canonical_phone(str(event.contact["phone_number"]))

    if not username and not phone_digits:
        return None

    engine = get_engine()
    with engine.begin() as conn:
        row = None
        if username:
            row = conn.execute(
                sql_text(
                    "SELECT id, name FROM admin_delegates WHERE active = true "
                    "AND telegram_chat_id IS NULL AND lower(telegram_username) = :u LIMIT 1"
                ),
                {"u": username},
            ).mappings().first()
        if not row and phone_digits:
            row = conn.execute(
                sql_text(
                    "SELECT id, name FROM admin_delegates WHERE active = true "
                    "AND telegram_chat_id IS NULL AND phone = :p LIMIT 1"
                ),
                {"p": phone_digits},
            ).mappings().first()
        if not row:
            return None
        conn.execute(
            sql_text("UPDATE admin_delegates SET telegram_chat_id = :cid WHERE id = :id"),
            {"cid": event.chat_id, "id": row["id"]},
        )
    return row["name"]


async def reply_delegates_menu(client: TelegramClient, chat_id: int) -> None:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            sql_text(
                "SELECT id, name, telegram_username, phone, telegram_chat_id "
                "FROM admin_delegates WHERE active = true ORDER BY id"
            )
        ).mappings().all()

    lines = ["👥 المفوّضون:"]
    buttons: list[list[dict[str, str]]] = []
    if not rows:
        lines.append("لا يوجد مفوّضون حاليًا.")
    for d in rows:
        contact = ("@" + d["telegram_username"]) if d["telegram_username"] else (d["phone"] or "-")
        linked = "مربوط ✅" if d["telegram_chat_id"] else "بانتظار الربط ⏳"
        lines.append(f"#{d['id']} — {d['name']} ({contact}) — {linked}")
        buttons.append([{"text": f"🗑️ إزالة {d['name']}", "callback_data": f"deleg:rm:{d['id']}"}])
    buttons.append([{"text": "➕ إضافة مفوّض", "callback_data": "admin:delegates_add"}])
    buttons.extend(nav_rows(None, "admin:menu"))
    await client.send_message(chat_id, "\n".join(lines), buttons=buttons)


async def reply_add_delegate(client: TelegramClient, chat_id: int, name: str, contact_text: str) -> None:
    """يقبل إمّا @username (يبدأ بـ"@" أو نص بلا أرقام كافية لاعتباره
    جوالًا) أو رقم جوال (≥ 8 أرقام، يُوحّد بcanonical_phone). لا مطابقة
    فعلية هنا — تخزين فقط، الربط الفعلي يقع لاحقًا بأول رسالة يرسلها
    المفوّض نفسه (`try_link_delegate` أعلاه)."""
    raw = contact_text.strip()
    digit_count = sum(ch.isdigit() for ch in raw)

    username: str | None = None
    phone_digits: str | None = None
    if raw.startswith("@"):
        username = raw[1:].strip().lower() or None
    elif digit_count >= 8:
        phone_digits = canonical_phone(raw) or None
    else:
        username = raw.lstrip("@").lower() or None

    if not username and not phone_digits:
        await client.send_message(
            chat_id,
            "لم أفهم هذا — اكتب @username المفوّض أو رقم جواله:",
            buttons=nav_rows("admin:delegates", "admin:menu"),
        )
        return

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            sql_text("INSERT INTO admin_delegates (name, telegram_username, phone) VALUES (:n, :u, :p)"),
            {"n": name.strip() or "بلا اسم", "u": username, "p": phone_digits},
        )
    await client.send_message(
        chat_id,
        "تم ✅ اطلب منه يرسل أي رسالة لهذا البوت (أو يشارك رقمه) وسيُفعّل تلقائيًا.",
        buttons=nav_rows(None, "admin:menu"),
    )


async def reply_remove_delegate(client: TelegramClient, chat_id: int, delegate_id: int) -> None:
    """إزالة فورية — `active=false` + `removed_at=now()` (لا حذف صفّ، نفس
    قاعدة "لا حذف بيانات" العامة بالمشروع). يسري فورًا: `is_admin_chat`/
    `notify_admin` يستبعدان أي صف `active=false` بشرط WHERE مباشرة."""
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            sql_text(
                "UPDATE admin_delegates SET active = false, removed_at = now() "
                "WHERE id = :id AND active = true RETURNING name"
            ),
            {"id": delegate_id},
        ).first()
    if not row:
        await client.send_message(chat_id, "⚠️ لم أجد مفوّضًا نشطًا بهذا الرقم.")
        return
    await client.send_message(chat_id, f"🗑️ تمت إزالة المفوّض {row[0]} — لن يستقبل أي رسائل بعد الآن.")
    await reply_delegates_menu(client, chat_id)
