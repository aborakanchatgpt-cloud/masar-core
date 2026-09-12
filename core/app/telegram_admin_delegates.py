"""B9/B2: منطق المفوّضين (`admin_delegates`) — ربط مبدئي (معلّق) عند أول
رسالة من مفوّض غير مربوط بعد، وCRUD خفيف (إضافة/إزالة). فُصل عن
`app.telegram_admin` (الذي يستدعي هذه الدوال من قائمة "👥 المفوّضون" وتوجيه
الرسائل) حصرًا لإبقاء حجم كل ملف دون الحد التوثيقي لدفعة واحدة عبر
`repo_write` (~25 ألف حرف) — لا اعتماد دائري: هذا الملف لا يستورد شيئًا من
telegram_admin.py.

B11.3: تفعيل المفوّض لم يعد تلقائيًا عند أول رسالة تطابق — `try_link_delegate`
يخزّن `pending_chat_id` فقط (ترحيلة 0022) ويُرسل للمالك زرّي ✅ تأكيد/❌ رفض
(`dlg:confirm:<id>`/`dlg:reject:<id>`، تُعالَج بـtelegram_admin.py). حتى قبل
تأكيد المالك صراحة، يبقى المُرسِل غريبًا تمامًا بالنسبة للبوت (صمت — لا رسالة
ترحيب، لا وصول لأي أمر) — هذا هو سبب إرجاع `try_link_delegate` لـNone دومًا
الآن (لم يعد يُرجع اسم المفوّض عند "نجاح" الربط، لأن لا ربط فوريًا يحدث)."""
from __future__ import annotations

import logging
import os

from sqlalchemy import text as sql_text

from app.discovery import get_engine
from app.phone import canonical_phone
from app.telegram_client import ChatEvent, TelegramClient, get_admin_bot_client
from app.telegram_nav import nav_rows

logger = logging.getLogger("masar.telegram_admin_delegates")


async def _notify_owner_pending_delegate(delegate_id: int, name: str) -> None:
    """B11.3: يُرسل للمالك (MASAR_OWNER_CHAT_ID) طلب تأكيد ربط مفوّض معلّق
    بزرّي ✅/❌. best-effort بالكامل — أي فشل (توكن غائب، خطأ شبكة) يُسجّل
    فقط ولا يُسقط استقبال رسالة المفوّض نفسها."""
    owner_chat_id = os.environ.get("MASAR_OWNER_CHAT_ID", "")
    if not owner_chat_id:
        logger.warning("MASAR_OWNER_CHAT_ID غير معرّف — تعذّر إشعار المالك بطلب ربط مفوّض معلّق (%s)", name)
        return
    client = get_admin_bot_client()
    if client is None:
        logger.warning("TELEGRAM_ADMIN_BOT_TOKEN غير معرّف — تعذّر إشعار المالك بطلب ربط مفوّض معلّق (%s)", name)
        return
    try:
        await client.send_message(
            owner_chat_id,
            f"👤 طلب ربط مفوّض جديد: {name}\n\nهل تؤكّد ربطه بلوحة تحكّم مسار؟",
            buttons=[
                [
                    {"text": "✅ تأكيد", "callback_data": f"dlg:confirm:{delegate_id}"},
                    {"text": "❌ رفض", "callback_data": f"dlg:reject:{delegate_id}"},
                ]
            ],
        )
    except Exception:  # noqa: BLE001 — best-effort، لا يرفع استثناءً أبدًا
        logger.exception("فشل إشعار المالك بطلب ربط مفوّض معلّق (%s)", name)


async def try_link_delegate(event: ChatEvent) -> str | None:
    """يبحث عن مفوّض بانتظار الربط (`telegram_chat_id IS NULL`) — إمّا عبر
    تطابق @username (lower-case، بلا "@") أو عبر مشاركة جهة اتصال حقيقية
    (زر Telegram request_contact، يتحقّق أنها جهة اتصال المُرسِل نفسه:
    `from_user_id == chat_id`). عند وجود تطابق: يخزّن `pending_chat_id`
    (بانتظار تأكيد المالك الصريح، B11.3) ويُرسل له طلب تأكيد — idempotent
    (لا إشعار مكرر لو كرّر نفس الشخص رسائله وهو معلّق أصلًا لنفس chat_id).
    **يُرجع None دومًا** — لا تفعيل فوري بعد الآن، فيبقى المُرسِل غريبًا
    (صمت تام) حتى يضغط المالك ✅ تأكيد."""
    username = (event.from_username or "").strip().lstrip("@").lower()
    phone_digits = ""
    if event.contact and event.contact.get("phone_number") and event.from_user_id == event.chat_id:
        phone_digits = canonical_phone(str(event.contact["phone_number"]))

    if not username and not phone_digits:
        return None

    engine = get_engine()
    already_pending = False
    row = None
    with engine.begin() as conn:
        if username:
            row = conn.execute(
                sql_text(
                    "SELECT id, name, pending_chat_id FROM admin_delegates WHERE active = true "
                    "AND telegram_chat_id IS NULL AND lower(telegram_username) = :u LIMIT 1"
                ),
                {"u": username},
            ).mappings().first()
        if not row and phone_digits:
            row = conn.execute(
                sql_text(
                    "SELECT id, name, pending_chat_id FROM admin_delegates WHERE active = true "
                    "AND telegram_chat_id IS NULL AND phone = :p LIMIT 1"
                ),
                {"p": phone_digits},
            ).mappings().first()
        if not row:
            return None
        already_pending = row["pending_chat_id"] == event.chat_id
        if not already_pending:
            conn.execute(
                sql_text("UPDATE admin_delegates SET pending_chat_id = :cid WHERE id = :id"),
                {"cid": event.chat_id, "id": row["id"]},
            )

    if not already_pending:
        await _notify_owner_pending_delegate(row["id"], row["name"])
    return None


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
    جوالًا) أو رقم جوال (≥ 8 أرقام، يُوحّد bcanonical_phone). لا مطابقة
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
        # B11.3: لم يعد التفعيل تلقائيًا — رسالته الأولى تُخزّن معلّقة
        # وتُرسل لك أنت طلب تأكيد صريح (✅/❌) قبل أي ربط فعلي.
        "تم ✅ اطلب منه يرسل أي رسالة لهذا البوت (أو يشارك رقمه) — ستصلك رسالة لتأكيد ربطه.",
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


async def reply_confirm_delegate(client: TelegramClient, chat_id: int, delegate_id: int) -> None:
    """B11.3: تأكيد صريح من المالك (زر ✅) — يحوّل `pending_chat_id` الأخير
    إلى `telegram_chat_id` الفعلي (تفعيل)، ويُرسل للمفوّض نفسه رسالة ترحيب
    عبر بوت الأدمن (best-effort — فشل إشعار المفوّض لا يُفشل التأكيد نفسه)."""
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            sql_text(
                "UPDATE admin_delegates SET telegram_chat_id = pending_chat_id, pending_chat_id = NULL "
                "WHERE id = :id AND pending_chat_id IS NOT NULL "
                "RETURNING name, telegram_chat_id"
            ),
            {"id": delegate_id},
        ).mappings().first()
    if not row:
        await client.send_message(chat_id, "⚠️ لا يوجد طلب ربط معلّق بهذا الرقم (ربما أُلغي أو أُكّد مسبقًا).")
        return
    await client.send_message(chat_id, f"✅ تم تأكيد ربط المفوّض {row['name']} بنجاح.")
    try:
        await client.send_message(
            row["telegram_chat_id"],
            f"أهلاً بك {row['name']} 👋 تم تأكيد ربطك كمفوّض بلوحة تحكّم مسار من قبل المالك.",
        )
    except Exception:  # noqa: BLE001 — best-effort، لا يُفشل التأكيد نفسه
        logger.exception("فشل إرسال رسالة ترحيب للمفوّض بعد التأكيد (id=%s)", delegate_id)


async def reply_reject_delegate(client: TelegramClient, chat_id: int, delegate_id: int) -> None:
    """B11.3: رفض صريح من المالك (زر ❌) — يمسح `pending_chat_id` فقط (الصفّ
    يبقى نشطًا بانتظار محاولة ربط لاحقة، لا حذف/تعطيل للمفوّض نفسه)."""
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            sql_text("UPDATE admin_delegates SET pending_chat_id = NULL WHERE id = :id RETURNING name"),
            {"id": delegate_id},
        ).first()
    if not row:
        await client.send_message(chat_id, "⚠️ لم أجد هذا المفوّض.")
        return
    await client.send_message(chat_id, f"❌ تم رفض طلب ربط {row[0]}.")
