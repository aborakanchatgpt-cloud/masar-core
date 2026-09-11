"""
Masar Core — بوت الأدمن الخاص على تيليجرام (B8: إزالة n8n نهائيًا من مسار
الدخول الوارد). يُعيد بناء منطق ورشتي عمل n8n القديمتين
(`n8n/workflows/masar_admin_bot.json` + `masar_admin_bot_part2.json`) داخل
Core مباشرة، بقائمة أزرار inline واحدة تُغطّي كل ما يحتاجه أحمد يوميًا.

**بوابة وصول مغلقة افتراضيًا (fail-closed)** — نفس فلسفة app.auth تمامًا:
لا رسالة تُعالَج ولا ردّ يُرسَل لأي محادثة غير MASAR_OWNER_CHAT_ID، وإن غاب
MASAR_OWNER_CHAT_ID من البيئة يتعطّل هذا البوت بالكامل (لا "يفتح" بالخطأ).
هذا التحقق مكرّر هنا عمدًا فوق فحص app.telegram_api (دفاع بالعمق — لا
نعتمد على طبقة توجيه واحدة فقط لحماية أوامر إدارية حسّاسة كالتفعيل/الإيقاف).

كل أمر يستدعي **مباشرة** الدالة الأساسية بنفس نقطة النهاية HTTP الموجودة
أصلًا (app.customers_api / app.overview_api / app.reports_api /
app.guarantee_api) — لا إعادة تطبيق لأي منطق أعمال، فقط تنسيق نص عربي حول
نتيجة كل استدعاء (نفس ما كانت تفعله عقد "تنسيق ..." بـn8n).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text as sql_text

from app import customers_api, guarantee_api, overview_api, reports_api
from app.discovery import get_engine
from app.phone import canonical_phone
from app.telegram_client import ChatEvent, TelegramClient

logger = logging.getLogger("masar.telegram_admin")

EXTEND_DEFAULT_DAYS = 30


# =========================================================================
# جلسة الأدمن — نفس جدول telegram_sessions المشترك مع onboarding (بلا
# تصادم عمليًا: chat_id الأدمن هو معرّف حساب Telegram الشخصي لأحمد، ولا
# يمكن أن يتطابق مع chat_id عميل حقيقي آخر — راجع تعليق التصميم بتقرير
# التسليم REPORT.md لتفصيل هذا القرار).
# =========================================================================


def _get_session(chat_id: int) -> tuple[str, dict[str, Any]]:
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            sql_text("SELECT step, data FROM telegram_sessions WHERE chat_id = :cid"),
            {"cid": chat_id},
        ).mappings().first()
    if not row:
        return "", {}
    return row["step"] or "", dict(row["data"] or {})


def _save_session(chat_id: int, step: str, data: dict[str, Any]) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            sql_text(
                """
                INSERT INTO telegram_sessions (chat_id, step, data, updated_at)
                VALUES (:cid, :step, :data, now())
                ON CONFLICT (chat_id) DO UPDATE SET
                    step = EXCLUDED.step, data = EXCLUDED.data, updated_at = now()
                """
            ),
            {"cid": chat_id, "step": step, "data": json.dumps(data, ensure_ascii=False)},
        )


def _clear_session(chat_id: int) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(sql_text("DELETE FROM telegram_sessions WHERE chat_id = :cid"), {"cid": chat_id})


def is_owner_chat(chat_id: int) -> bool:
    """فشل مغلق عمدًا (نفس نمط app.auth.require_admin_token): غياب
    MASAR_OWNER_CHAT_ID بالبيئة يعني "لا مالك مُعرّف" فيُرفض أي chat_id بلا
    استثناء، بدل معاملة قيمة فارغة كمطابقة بالخطأ."""
    owner = os.environ.get("MASAR_OWNER_CHAT_ID", "")
    if not owner:
        return False
    try:
        return int(owner) == chat_id
    except ValueError:
        return False


# =========================================================================
# القائمة الرئيسية
# =========================================================================


def _main_menu_buttons() -> list[list[dict[str, str]]]:
    return [
        [{"text": "🆕 تسجيل عميل جديد", "callback_data": "admin:new_customer"}],
        [{"text": "📊 نظرة عامة", "callback_data": "admin:overview"}],
        [{"text": "🧩 تصنيف العملاء", "callback_data": "admin:segments"}],
        [
            {"text": "🔍 بحث عن عميل", "callback_data": "admin:lookup"},
            {"text": "📨 تقرير عميل", "callback_data": "admin:report"},
        ],
        [{"text": "▶️ تشغيل كل التقارير الآن", "callback_data": "admin:run_reports"}],
        [
            {"text": "⏯️ تفعيل/إيقاف عميل", "callback_data": "admin:status"},
            {"text": "⏳ تمديد اشتراك", "callback_data": "admin:extend"},
        ],
        [{"text": "💰 الضمانات المعلّقة", "callback_data": "admin:guarantees"}],
    ]


async def _send_main_menu(client: TelegramClient, chat_id: int, prefix: str = "") -> None:
    text = (prefix + "\n\n" if prefix else "") + "لوحة تحكّم مسار 🌿\nاختر من القائمة:"
    await client.send_message(chat_id, text, buttons=_main_menu_buttons())


# =========================================================================
# نقطة الدخول الرئيسية
# =========================================================================


async def handle_update(update: dict[str, Any], event: ChatEvent, client: TelegramClient) -> None:
    if not is_owner_chat(event.chat_id):
        logger.warning("رسالة أدمن من chat_id غير مصرّح به — تُتجاهل بصمت: %s", event.chat_id)
        return

    if event.is_callback and event.callback_data:
        await _handle_callback(event, client)
        return

    if not event.text:
        return

    step, data = _get_session(event.chat_id)
    if step:
        await _handle_step_text(event, client, step, data)
        return

    await _send_main_menu(client, event.chat_id)


# -------------------------------------------------------------------
# توجيه الأزرار
# -------------------------------------------------------------------


async def _handle_callback(event: ChatEvent, client: TelegramClient) -> None:
    data = event.callback_data

    if data == "admin:menu":
        _clear_session(event.chat_id)
        await _send_main_menu(client, event.chat_id)
        return

    if data == "admin:new_customer":
        _save_session(event.chat_id, "new_customer_name", {})
        await client.send_message(event.chat_id, "اكتب اسم العميل الجديد:")
        return

    if data == "admin:overview":
        await _reply_overview(client, event.chat_id)
        return

    if data == "admin:segments":
        await _reply_segments(client, event.chat_id)
        return

    if data == "admin:lookup":
        _save_session(event.chat_id, "lookup_id", {})
        await client.send_message(event.chat_id, "اكتب رقم معرّف العميل (customer id):")
        return

    if data == "admin:report":
        _save_session(event.chat_id, "report_id", {})
        await client.send_message(event.chat_id, "اكتب رقم معرّف العميل لعرض تقرير اليوم:")
        return

    if data == "admin:run_reports":
        await _reply_run_reports(client, event.chat_id)
        return

    if data == "admin:status":
        _save_session(event.chat_id, "status_id", {})
        await client.send_message(event.chat_id, "اكتب رقم معرّف العميل الذي تريد تفعيله/إيقافه:")
        return

    if data.startswith("status_choice:"):
        _, customer_id_str, new_status = data.split(":", 2)
        await _reply_set_status(client, event.chat_id, int(customer_id_str), new_status)
        return

    if data == "admin:extend":
        _save_session(event.chat_id, "extend_id", {})
        await client.send_message(event.chat_id, "اكتب رقم معرّف العميل الذي تريد تمديد اشتراكه:")
        return

    if data == "admin:guarantees":
        await _reply_guarantees(client, event.chat_id)
        return

    if data.startswith("settle:"):
        ledger_id = data[len("settle:") :]
        await _reply_settle_guarantee(client, event.chat_id, int(ledger_id))
        return


# -------------------------------------------------------------------
# توجيه الرسائل النصية أثناء انتظار مُدخَل (خطوات متعددة الرسائل)
# -------------------------------------------------------------------


async def _handle_step_text(event: ChatEvent, client: TelegramClient, step: str, data: dict) -> None:
    text = event.text.strip()

    if step == "new_customer_name":
        _save_session(event.chat_id, "new_customer_phone", {"name": text})
        await client.send_message(event.chat_id, "تمام. الآن اكتب رقم جوال العميل (أرقام فقط):")
        return

    if step == "new_customer_phone":
        await _reply_create_customer(client, event.chat_id, data.get("name", ""), text)
        return

    if step == "lookup_id":
        _clear_session(event.chat_id)
        await _reply_lookup(client, event.chat_id, text)
        return

    if step == "report_id":
        _clear_session(event.chat_id)
        await _reply_customer_report(client, event.chat_id, text)
        return

    if step == "status_id":
        try:
            customer_id = int(text)
        except ValueError:
            await client.send_message(event.chat_id, "اكتب رقم معرّف عميل صحيح (أرقام فقط):")
            return
        _clear_session(event.chat_id)
        await client.send_message(
            event.chat_id,
            f"اختر الحالة الجديدة للعميل #{customer_id}:",
            buttons=[
                [
                    {"text": "▶️ تفعيل", "callback_data": f"status_choice:{customer_id}:active"},
                    {"text": "⏸️ إيقاف", "callback_data": f"status_choice:{customer_id}:paused"},
                ]
            ],
        )
        return

    if step == "extend_id":
        try:
            customer_id = int(text)
        except ValueError:
            await client.send_message(event.chat_id, "اكتب رقم معرّف عميل صحيح (أرقام فقط):")
            return
        _save_session(event.chat_id, "extend_days", {"customer_id": customer_id})
        await client.send_message(
            event.chat_id, f"كم عدد الأيام اللي تحب تمدّدها؟ (اكتب رقمًا، افتراضيًا {EXTEND_DEFAULT_DAYS}):"
        )
        return

    if step == "extend_days":
        # B9/A6: كان أي إدخال غير رقمي هنا (خطأ كتابة، مثلًا) يُمرّر بصمت
        # كـEXTEND_DEFAULT_DAYS (30 يومًا) بلا أي إشعار — قد يُمدّد اشتراك
        # عميل بعدد أيام لم يقصده أحمد إطلاقًا. الآن: نفس نمط إعادة الطلب
        # المُستخدَم بكل خطوة رقمية أخرى بهذا الملف (extend_id/status_id/...)
        # — إدخال غير صحيح يُعيد نفس السؤال بدل الاستمرار بقيمة مخمّنة.
        try:
            days = int(text)
        except ValueError:
            await client.send_message(
                event.chat_id,
                f"لم أفهم هذا كعدد أيام. اكتب رقمًا صحيحًا (مثال: 30)، افتراضيًا {EXTEND_DEFAULT_DAYS}:",
            )
            return
        customer_id = int(data.get("customer_id", 0))
        _clear_session(event.chat_id)
        await _reply_extend_subscription(client, event.chat_id, customer_id, days)
        return

    _clear_session(event.chat_id)
    await _send_main_menu(client, event.chat_id)


# =========================================================================
# كل أمر — يستدعي الدالة الأساسية مباشرة (بلا HTTP، بلا إعادة تطبيق منطق)
# ثم يُنسّق النتيجة كنص عربي
# =========================================================================


async def _reply_create_customer(client: TelegramClient, chat_id: int, name: str, phone_text: str) -> None:
    # B9/A1: توحيد الصيغة هنا يضمن تطابقها لاحقًا مع الرقم الذي يرسله
    # تيليجرام فعليًا (966xxxxxxxxx) عند مشاركة العميل رقمه — راجع app/phone.py
    phone_digits = canonical_phone(phone_text)
    _clear_session(chat_id)
    try:
        result = await customers_api.create_customer(
            customers_api.CustomerCreateRequest(name=name.strip(), phone=phone_digits or None)
        )
    except HTTPException as exc:
        await client.send_message(chat_id, f"⚠️ تعذّر إنشاء العميل: {exc.detail}")
        return
    await client.send_message(
        chat_id,
        f"✅ تم تسجيل العميل #{result['customer_id']} — {name}\n"
        f"الجوال: {phone_digits or 'غير محدّد'}\n\n"
        "سيربط العميل حسابه بنفسه عند مراسلة بوت مسار ومشاركة رقم جواله.",
    )


async def _reply_overview(client: TelegramClient, chat_id: int) -> None:
    o = await overview_api.overview()
    lines = [
        "📊 نظرة عامة",
        "",
        f"العملاء حسب الحالة: {o['customers_by_status']}",
        f"تقديمات اليوم: {o['sends_today']} | آخر 7 أيام: {o['sends_last_7_days']}",
        f"ارتدادات اليوم: {o['bounces_today']}",
        f"طابور الإرسال: {o['send_queue_by_status']}",
        f"صناديق البريد: {o['mail_links_by_status']}",
        f"تقارير معلّقة: {o['pending_reports']}",
        f"ضمانات معلّقة: {o['pending_guarantees']}",
        f"وضع التجربة (DRY_RUN): {'مفعّل' if o['dry_run'] else 'متوقف'}",
    ]
    await client.send_message(chat_id, "\n".join(lines))


async def _reply_segments(client: TelegramClient, chat_id: int) -> None:
    engine = get_engine()
    with engine.connect() as conn:
        by_status = dict(conn.execute(sql_text("SELECT status, count(*) FROM customers GROUP BY status")).all())
        by_family = conn.execute(
            sql_text(
                """
                SELECT elem AS family, count(*) AS n
                FROM customers, LATERAL jsonb_array_elements_text(families) AS elem
                GROUP BY elem ORDER BY n DESC LIMIT 10
                """
            )
        ).all()
        by_city = conn.execute(
            sql_text(
                """
                SELECT elem AS city, count(*) AS n
                FROM customers, LATERAL jsonb_array_elements_text(cities) AS elem
                GROUP BY elem ORDER BY n DESC LIMIT 10
                """
            )
        ).all()

    lines = ["🧩 تصنيف العملاء", "", f"حسب الحالة: {by_status}", "", "أكثر 10 مجالات مهنية:"]
    if by_family:
        lines.extend(f"  {family}: {n}" for family, n in by_family)
    else:
        lines.append("  لا بيانات بعد")
    lines.append("")
    lines.append("أكثر 10 مدن/مناطق مفضّلة:")
    if by_city:
        lines.extend(f"  {city}: {n}" for city, n in by_city)
    else:
        lines.append("  لا بيانات بعد")
    await client.send_message(chat_id, "\n".join(lines))


async def _reply_lookup(client: TelegramClient, chat_id: int, text: str) -> None:
    try:
        customer_id = int(text.strip())
    except ValueError:
        await client.send_message(chat_id, "اكتب رقم معرّف عميل صحيح (أرقام فقط).")
        return
    try:
        result = await customers_api.get_customer(customer_id)
    except HTTPException:
        await client.send_message(chat_id, "لم أجد عميلًا بهذا الرقم.")
        return
    c = result["customer"]
    await client.send_message(
        chat_id,
        f"🔍 عميل #{c['id']} — {c['name']}\n"
        f"الحالة: {c['status']}\n"
        f"الجوال: {c.get('phone') or '-'}\n"
        f"تيليجرام مربوط: {'نعم' if c.get('telegram_chat_id') else 'لا'}\n"
        f"المدن: {c.get('cities')}\n"
        f"المجالات: {c.get('families')}\n"
        f"الرصيد: {c.get('wallet_balance')}",
    )


async def _reply_customer_report(client: TelegramClient, chat_id: int, text: str) -> None:
    try:
        customer_id = int(text.strip())
    except ValueError:
        await client.send_message(chat_id, "اكتب رقم معرّف عميل صحيح (أرقام فقط).")
        return
    try:
        result = await reports_api.customer_report(customer_id)
    except HTTPException as exc:
        await client.send_message(chat_id, f"⚠️ {exc.detail}")
        return
    payload = result.get("payload") or {}
    report_text = payload.get("text") if isinstance(payload, dict) else None
    await client.send_message(
        chat_id, f"📨 تقرير العميل #{customer_id}:\n\n{report_text or 'لا يوجد تقرير لهذا اليوم بعد.'}"
    )


async def _reply_run_reports(client: TelegramClient, chat_id: int) -> None:
    result = await reports_api.run_reports(None)
    await client.send_message(
        chat_id,
        "✅ تشغيل التقارير: "
        f"أُنشئ {result.get('created', 0)}، موجود مسبقًا {result.get('already_existed', 0)}، "
        f"أخطاء {result.get('errors', 0)}.",
    )


async def _reply_set_status(client: TelegramClient, chat_id: int, customer_id: int, new_status: str) -> None:
    try:
        result = await customers_api.update_customer_status(
            customer_id, customers_api.CustomerStatusRequest(status=new_status)
        )
    except HTTPException as exc:
        await client.send_message(chat_id, f"⚠️ {exc.detail}")
        return
    label = "مُفعّل ▶️" if new_status == "active" else "مُوقف ⏸️"
    await client.send_message(chat_id, f"✅ تم تحديث حالة العميل #{result['customer_id']} إلى {label}.")


async def _reply_extend_subscription(client: TelegramClient, chat_id: int, customer_id: int, days: int) -> None:
    """يمدّد أحدث اشتراك فعّال للعميل بعدد أيام محدّد — لا نقطة نهاية HTTP
    جاهزة لهذا حاليًا بالمستودع، فيُنفذ هنا مباشرة (SQL بسيط، نفس نمط
    الملفات الأخرى: UPDATE محمي بشرط status='active' فلا يُمدّد اشتراك
    مُغلَق سهوًا."""
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            sql_text(
                """
                UPDATE subscriptions SET ends_at = ends_at + make_interval(days => :days)
                WHERE id = (
                    SELECT id FROM subscriptions
                    WHERE customer_id = :cid AND status = 'active'
                    ORDER BY id DESC LIMIT 1
                )
                RETURNING id, ends_at
                """
            ),
            {"cid": customer_id, "days": days},
        ).first()
    if not row:
        await client.send_message(chat_id, f"⚠️ لا يوجد اشتراك فعّال للعميل #{customer_id} لتمديده.")
        return
    await client.send_message(
        chat_id, f"✅ تم تمديد اشتراك العميل #{customer_id} بـ{days} يومًا — ينتهي الآن: {row[1]}."
    )


async def _reply_guarantees(client: TelegramClient, chat_id: int) -> None:
    result = await guarantee_api.pending_guarantees()
    items = result["items"]
    if not items:
        await client.send_message(chat_id, "لا توجد ضمانات بانتظار التسوية الآن.")
        return
    lines = [f"💰 ضمانات بانتظار التسوية ({len(items)}):"]
    lines.extend(
        f"#{g['id']} — {g.get('customer_name') or ('عميل ' + str(g['customer_id']))} — "
        f"العجز {g['shortfall']} — المبلغ {g.get('refund_amount') if g.get('refund_amount') is not None else 'غير محدد'}"
        for g in items
    )
    buttons = [[{"text": f"✅ تسوية #{g['id']}", "callback_data": f"settle:{g['id']}"}] for g in items[:20]]
    await client.send_message(chat_id, "\n".join(lines), buttons=buttons)


async def _reply_settle_guarantee(client: TelegramClient, chat_id: int, ledger_id: int) -> None:
    try:
        result = await guarantee_api.settle_guarantee(ledger_id)
    except HTTPException as exc:
        await client.send_message(chat_id, f"⚠️ {exc.detail}")
        return
    suffix = " (كانت مُسوّاة مسبقًا)" if result.get("already_settled") else ""
    await client.send_message(chat_id, f"✅ تمت تسوية الضمان #{result['id']}{suffix}.")
