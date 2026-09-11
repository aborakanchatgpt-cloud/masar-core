"""B9/B5: بحث الأدمن عن عميل (رقم/جوال/اسم) + بطاقة العميل الموسّعة بأزرار
إجراء (تفعيل/إيقاف، تمديد، تقرير اليوم، رسالة له، رابط ربط بريد) — و"✉️
رسالة لعميل" (يعيد استخدام نفس البحث). راجع الدليل §B5.

لا إدارة جلسة هنا إطلاقًا (لا `_save_session`/`_clear_session`) — نفس نمط
`telegram_admin_delegates.py`: `telegram_admin.py` وحده يملك خطوات الجلسة
متعددة الرسائل، وهذا الملف دوال صرفة (بحث/عرض/فعل) يستدعيها. كل استدعاء
له نقطة نهاية HTTP جاهزة يمر عبرها مباشرة (customers_api/reports_api/
link_api) — بحث متعدد المعايير فقط (لا نقطة نهاية جاهزة له) وقراءات بطاقة
العميل الإضافية (باقة/بريد/تقديمات) بـSQL مباشر بنفس نمط بقية الملف.

B9/B5-hotfix (11 سبتمبر، بعد مراجعة أحمد): `card_report` كان يستدعي
`reports_api.customer_report(customer_id)` بلا تمرير `date` صراحة — معامل
FastAPI الافتراضي `Query(default=None)` لا يُحلّ لقيمته الفعلية عند
الاستدعاء المباشر (بلا HTTP)، فيبقى كائن Query نفسه ويُمرَّر لاستعلام SQL
فيفشل بصمت. أُصلح بتمرير `date=None` صراحة + شبكة أمان `except Exception`
تُخبر أحمد بخطأ داخلي بدل صمت تام لأي عطل غير متوقع مستقبلي بنفس النمط.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text as sql_text

from app import customers_api, link_api, reports_api
from app.discovery import get_engine
from app.phone import canonical_phone
from app.telegram_client import TelegramClient
from app.telegram_nav import nav_rows
from app.telegram_notify import TelegramNotConfigured, TelegramSendError
from app.telegram_notify import send_message as send_customer_message

logger = logging.getLogger("masar.telegram_admin_search")

MAX_RESULTS = 5


# =========================================================================
# البحث
# =========================================================================


def search_customers(query: str) -> list[dict[str, Any]]:
    """رقم بالكامل → مُعرّف عميل أولًا (الحالة الأشيع)، وإلا جوال (canonical
    أو كما كُتب). نص → مطابقة جزئية بالاسم (ILIKE)، حتى 5 نتائج."""
    q = (query or "").strip()
    if not q:
        return []
    engine = get_engine()
    with engine.connect() as conn:
        if q.isdigit():
            row = conn.execute(
                sql_text("SELECT id, name, status FROM customers WHERE id = :id"), {"id": int(q)}
            ).mappings().first()
            if row:
                return [dict(row)]
            phone = canonical_phone(q) or q
            rows = conn.execute(
                sql_text(
                    "SELECT id, name, status FROM customers WHERE phone IN (:p1, :p2) "
                    "ORDER BY id DESC LIMIT :lim"
                ),
                {"p1": phone, "p2": q, "lim": MAX_RESULTS},
            ).mappings().all()
            return [dict(r) for r in rows]
        rows = conn.execute(
            sql_text(
                "SELECT id, name, status FROM customers WHERE name ILIKE :pat ORDER BY id DESC LIMIT :lim"
            ),
            {"pat": f"%{q}%", "lim": MAX_RESULTS},
        ).mappings().all()
        return [dict(r) for r in rows]


async def reply_not_found(client: TelegramClient, chat_id: int, query: str) -> None:
    suffix = "بهذا الرقم." if query.strip().isdigit() else f"يطابق «{query.strip()}»."
    await client.send_message(chat_id, f"لم أجد عميلًا {suffix}", buttons=nav_rows(None, "admin:menu"))


async def reply_pick_buttons(
    client: TelegramClient, chat_id: int, results: list[dict[str, Any]], *, prefix: str
) -> None:
    buttons = [
        [{"text": f"{r['name']} — #{r['id']} ({r['status']})", "callback_data": f"{prefix}{r['id']}"}]
        for r in results
    ]
    buttons.extend(nav_rows(None, "admin:menu"))
    await client.send_message(chat_id, f"وجدت {len(results)} نتيجة — اختر العميل:", buttons=buttons)


# =========================================================================
# بطاقة العميل + أزرار الإجراء
# =========================================================================


def _fetch_customer_extra(customer_id: int) -> dict[str, Any]:
    engine = get_engine()
    with engine.connect() as conn:
        mail_status = conn.execute(
            sql_text("SELECT status FROM mail_links WHERE customer_id = :id ORDER BY id DESC LIMIT 1"),
            {"id": customer_id},
        ).scalar()
        sub = conn.execute(
            sql_text(
                "SELECT product_code, ends_at FROM subscriptions "
                "WHERE customer_id = :id AND status = 'active' ORDER BY id DESC LIMIT 1"
            ),
            {"id": customer_id},
        ).mappings().first()
        sends_period = conn.execute(
            sql_text(
                "SELECT count(*) FROM applications WHERE customer_id = :id AND sent_at >= now() - interval '30 days'"
            ),
            {"id": customer_id},
        ).scalar()
        last_report = conn.execute(
            sql_text(
                "SELECT report_date FROM daily_reports WHERE customer_id = :id ORDER BY report_date DESC LIMIT 1"
            ),
            {"id": customer_id},
        ).scalar()
    return {
        "mail_status": mail_status,
        "subscription": dict(sub) if sub else None,
        "sends_30d": int(sends_period or 0),
        "last_report": last_report,
    }


def _fetch_customer_chat_id(customer_id: int) -> int | None:
    engine = get_engine()
    with engine.connect() as conn:
        return conn.execute(
            sql_text("SELECT telegram_chat_id FROM customers WHERE id = :id"), {"id": customer_id}
        ).scalar()


async def reply_customer_card(client: TelegramClient, chat_id: int, customer_id: int) -> None:
    try:
        result = await customers_api.get_customer(customer_id)
    except HTTPException:
        await client.send_message(chat_id, "لم أجد عميلًا بهذا الرقم.", buttons=nav_rows(None, "admin:menu"))
        return
    c = result["customer"]
    extra = _fetch_customer_extra(customer_id)

    if extra["subscription"]:
        package_line = f"{extra['subscription']['product_code']} — ينتهي {extra['subscription']['ends_at'].date()}"
    else:
        package_line = f"رصيد: {c.get('wallet_balance', 0)}"
    mail_line = {"ok": "مربوط ✅", "failed": "فشل ❌", "unverified": "بانتظار التحقّق"}.get(
        extra["mail_status"], "غير موجود"
    )

    lines = [
        f"🔍 عميل #{c['id']} — {c['name']}",
        f"الحالة: {c['status']}",
        f"الجوال: {c.get('phone') or '-'}",
        f"تيليجرام مربوط: {'نعم' if c.get('telegram_chat_id') else 'لا'}",
        f"الباقة: {package_line}",
        f"البريد: {mail_line}",
        f"المدن: {c.get('cities')}",
        f"المجالات: {c.get('families')}",
        f"تقديمات آخر 30 يومًا: {extra['sends_30d']}",
        f"آخر تقرير: {extra['last_report'] or 'لا يوجد'}",
    ]

    action_rows: list[list[dict[str, str]]] = []
    if c["status"] == "active":
        toggle_row = [{"text": "⏸️ إيقاف", "callback_data": f"admin:card_toggle:{customer_id}:paused"}]
    elif c["status"] in ("pending", "paused"):
        toggle_row = [{"text": "▶️ تفعيل", "callback_data": f"admin:card_toggle:{customer_id}:active"}]
    else:
        toggle_row = []
    toggle_row.append({"text": "⏳ تمديد", "callback_data": f"admin:card_extend:{customer_id}"})
    action_rows.append(toggle_row)
    action_rows.append(
        [
            {"text": "📨 تقرير اليوم", "callback_data": f"admin:card_report:{customer_id}"},
            {"text": "✉️ رسالة له", "callback_data": f"admin:msgto:{customer_id}"},
        ]
    )
    action_rows.append([{"text": "🔗 رابط ربط بريد", "callback_data": f"admin:card_link:{customer_id}"}])
    action_rows.extend(nav_rows(None, "admin:menu"))
    await client.send_message(chat_id, "\n".join(lines), buttons=action_rows)


async def card_toggle_status(client: TelegramClient, chat_id: int, customer_id: int, new_status: str) -> None:
    try:
        result = await customers_api.update_customer_status(
            customer_id, customers_api.CustomerStatusRequest(status=new_status)
        )
    except HTTPException as exc:
        await client.send_message(chat_id, f"⚠️ {exc.detail}", buttons=nav_rows(None, "admin:menu"))
        return
    label = "مُفعَّل ▶️" if new_status == "active" else "مُوقَف ⏸️"
    await client.send_message(chat_id, f"✅ تم تحديث حالة العميل #{result['customer_id']} إلى {label}.")
    await reply_customer_card(client, chat_id, customer_id)


async def card_report(client: TelegramClient, chat_id: int, customer_id: int) -> None:
    try:
        # B9/B5-hotfix: تمرير date صراحة — راجع docstring الملف أعلاه.
        result = await reports_api.customer_report(customer_id, date=None)
    except HTTPException as exc:
        await client.send_message(chat_id, f"⚠️ {exc.detail}", buttons=nav_rows(None, "admin:menu"))
        return
    except Exception:  # noqa: BLE001
        logger.exception("خطأ غير متوقع بتقرير اليوم للعميل #%s", customer_id)
        await client.send_message(
            chat_id, "⚠️ تعذّر جلب التقرير الآن (خطأ داخلي) — سنراجعه.", buttons=nav_rows(None, "admin:menu")
        )
        return
    payload = result.get("payload") or {}
    report_text = payload.get("text") if isinstance(payload, dict) else None
    await client.send_message(
        chat_id,
        f"📨 تقرير العميل #{customer_id}:\n\n{report_text or 'لا يوجد تقرير لهذا اليوم بعد.'}",
        buttons=nav_rows(None, "admin:menu"),
    )


async def card_link(client: TelegramClient, chat_id: int, customer_id: int) -> None:
    try:
        token_result = await link_api.create_link_token(customer_id)
    except HTTPException as exc:
        await client.send_message(chat_id, f"⚠️ {exc.detail}", buttons=nav_rows(None, "admin:menu"))
        return
    domain = os.environ.get("MASAR_DOMAIN", "")
    if not domain:
        await client.send_message(
            chat_id, "⚠️ MASAR_DOMAIN غير معرَّف بالخادم — لا يمكن توليد رابط قابل للفتح."
        )
        return
    link_url = f"https://{domain}{token_result['path']}"
    customer_chat_id = _fetch_customer_chat_id(customer_id)
    if customer_chat_id:
        try:
            send_customer_message(
                customer_chat_id, f"مرحبًا 👋 هذا رابط ربط بريدك الخاص بالخدمة (صالح لعدة ساعات):\n{link_url}"
            )
            await client.send_message(chat_id, f"✅ أُرسل رابط الربط للعميل #{customer_id} مباشرة على تيليجرام.")
        except (TelegramNotConfigured, TelegramSendError):
            logger.warning("تعذّر إرسال رابط الربط مباشرة للعميل #%s", customer_id, exc_info=True)
            await client.send_message(chat_id, f"⚠️ تعذّر الإرسال المباشر — إليك الرابط لترسله يدويًا:\n{link_url}")
    else:
        await client.send_message(chat_id, f"العميل غير مربوط بتيليجرام بعد. الرابط:\n{link_url}")


# =========================================================================
# ✉️ رسالة لعميل
# =========================================================================


async def send_customer_message_and_log(
    client: TelegramClient, chat_id: int, customer_id: int, message_text: str
) -> None:
    customer_chat_id = _fetch_customer_chat_id(customer_id)
    if not customer_chat_id:
        await client.send_message(
            chat_id,
            f"⚠️ العميل #{customer_id} غير مربوط بتيليجرام — لا يمكن إرسال رسالة له الآن.",
            buttons=nav_rows(None, "admin:menu"),
        )
        return
    try:
        send_customer_message(customer_chat_id, message_text)
    except (TelegramNotConfigured, TelegramSendError) as exc:
        await client.send_message(chat_id, f"⚠️ تعذّر إرسال الرسالة: {exc}", buttons=nav_rows(None, "admin:menu"))
        return
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            sql_text(
                "INSERT INTO customer_messages (customer_id, direction, text, sent_by_chat_id) "
                "VALUES (:cid, 'out', :txt, :by)"
            ),
            {"cid": customer_id, "txt": message_text, "by": chat_id},
        )
    await client.send_message(chat_id, "✅ أُرسلت الرسالة للعميل.", buttons=nav_rows(None, "admin:menu"))
