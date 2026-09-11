"""
Masar Core — بوت الأدمن الخاص على تيليجرام (B8: إزالة n8n نهائيًا من مسار
الدخول الوارد). يُعيد بناء منطق ورشتي عمل n8n القديمتين
(`n8n/workflows/masar_admin_bot.json` + `masar_admin_bot_part2.json`) داخل
Core مباشرة، بقائمة أزرار inline واحدة تُغطّي كل ما يحتاجه أحمد يوميًا.

**بوابة وصول مغلقة افتراضيًا (fail-closed)** — نفس فلسفة app.auth تمامًا:
لا رسالة تُعالَج ولا ردّ يُرسَل لأي محادثة غير `is_admin_chat` (المالك، أو
مفوّض نشط مربوط — B9/B2). إن غاب MASAR_OWNER_CHAT_ID من البيئة يتعطّل هذا
البوت بالكامل للمالك (لا "يفتح" بالخطأ)؛ محادثات المفوّضين تبقى معطّلة
تلقائيًا أيضًا (بلا مالك مُعرَّف، القائمة الرئيسية بلا معنى تشغيليًا).

**B9/B2 — طبقة الهوية الوحيدة الآن:** كانت `app.telegram_api` تحمل بوابة
`is_owner_chat` ثانوية عند مسار `/admin` *قبل* الوصول لهذا الملف، فتصدّ أي
محادثة غير المالك قبل أن تصل هنا إطلاقًا — هذا كان يمنع أي مفوّض من إتمام
خطوة الربط الأولى (رسالة تعريفية تُطابَق ضد `admin_delegates` غير المربوطة
بعد). أُزيلت تلك البوابة من `telegram_api.py`؛ `is_admin_chat` أدناه +
`app.telegram_admin_delegates.try_link_delegate` أصبحا الحارس الوحيد والكامل.

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
from app import telegram_admin_delegates as delegates
from app.discovery import get_engine
from app.phone import canonical_phone
from app.telegram_client import ChatEvent, TelegramClient
from app.telegram_nav import nav_rows

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
    MASAR_OWNER_CHAT_ID بالبيئة يعني "لا مالك مُعرَّف" فيُرفض أي chat_id
    بلا استثناء، بدل معاملة قيمة فارغة كمطابقة بالخطأ."""
    owner = os.environ.get("MASAR_OWNER_CHAT_ID", "")
    if not owner:
        return False
    try:
        return int(owner) == chat_id
    except ValueError:
        return False


def is_admin_chat(chat_id: int) -> bool:
    """B9/B2: المالك دومًا، أو مفوّض نشط مربوط فعليًا بهذا chat_id
    (`admin_delegates.active AND telegram_chat_id = chat_id`).

    فحص المالك أولًا **بلا أي استعلام قاعدة بيانات** — المسار الشائع لكل
    رسالة من أحمد نفسه (الاستخدام الغالب فعليًا)، واستعلام admin_delegates
    يقع فقط لو لم يكن chat_id هو المالك (خطوة إضافية نادرة الحدوث نسبيًا)."""
    if is_owner_chat(chat_id):
        return True
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            sql_text(
                "SELECT 1 FROM admin_delegates WHERE active = true AND telegram_chat_id = :cid LIMIT 1"
            ),
            {"cid": chat_id},
        ).first()
    return row is not None


# =========================================================================
# القائمة الرئيسية
# =========================================================================


def _main_menu_buttons(is_owner: bool) -> list[list[dict[str, str]]]:
    rows: list[list[dict[str, str]]] = [
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
    # B9/B2: قائمة المفوّضين — للمالك فقط (المفوّض لا يرى هذه القائمة ولا
    # يديرها، قرار أحمد). ⚙️ الإعدادات تُضاف لاحقًا بـB5.
    if is_owner:
        rows.append([{"text": "👥 المفوّضون", "callback_data": "admin:delegates"}])
    return rows


async def _send_main_menu(client: TelegramClient, chat_id: int, prefix: str = "") -> None:
    text = (prefix + "\n\n" if prefix else "") + "لوحة تحكّم مسار 🌿\nاختر من القائمة:"
    await client.send_message(chat_id, text, buttons=_main_menu_buttons(is_owner_chat(chat_id)))


# =========================================================================
# نقطة الدخول الرئيسية
# =========================================================================


async def handle_update(update: dict[str, Any], event: ChatEvent, client: TelegramClient) -> None:
    if is_admin_chat(event.chat_id):
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
        return

    # B9/B2: محادثة غير معروفة (لا مالك ولا مفوّض مربوط مسبقًا) — حاول ربط
    # مفوّض بانتظار الربط أولًا، وإلا صمت تام (لا رد، لا كشف لوجود البوت).
    delegate_name = await delegates.try_link_delegate(event)
    if delegate_name:
        logger.info("تم ربط مفوّض جديد بمحادثة أدمن (الاسم=%s, chat_id=%s)", delegate_name, event.chat_id)
        await _send_main_menu(
            client, event.chat_id, prefix=f"أهلًا بك {delegate_name} 👋 تم ربطك كمفوّض بلوحة تحكّم مسار."
        )
        return

    logger.warning("رسالة أدمن من chat_id غير مصرّح به — تُتجاهل بصمت: %s", event.chat_id)


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
        await client.send_message(
            event.chat_id, "اكتب اسم العميل الجديد:", buttons=nav_rows(None, "admin:menu")
        )
        return

    if data == "admin:overview":
        await _reply_overview(client, event.chat_id)
        return

    if data == "admin:segments":
        await _reply_segments(client, event.chat_id)
        return

    if data == "admin:lookup":
        _save_session(event.chat_id, "lookup_id", {})
        await client.send_message(
            event.chat_id, "اكتب رقم معرّف العميل (customer id):", buttons=nav_rows(None, "admin:menu")
        )
        return

    if data == "admin:report":
        _save_session(event.chat_id, "report_id", {})
        await client.send_message(
            event.chat_id, "اكتب رقم معرّف العميل لعرض تقرير اليوم:", buttons=nav_rows(None, "admin:menu")
        )
        return

    if data == "admin:run_reports":
        await _reply_run_reports(client, event.chat_id)
        return

    if data == "admin:status":
        _save_session(event.chat_id, "status_id", {})
        await client.send_message(
            event.chat_id,
            "اكتب رقم معرّف العميل الذي تريد تفعيله/إيقافه:",
            buttons=nav_rows(None, "admin:menu"),
        )
        return

    if data.startswith("status_choice:"):
        _, customer_id_str, new_status = data.split(":", 2)
        await _reply_set_status(client, event.chat_id, int(customer_id_str), new_status)
        return

    if data == "admin:extend":
        _save_session(event.chat_id, "extend_id", {})
        await client.send_message(
            event.chat_id,
            "اكتب رقم معرّف العميل الذي تريد تمديد اشتراكه:",
            buttons=nav_rows(None, "admin:menu"),
        )
        return

    if data == "admin:guarantees":
        await _reply_guarantees(client, event.chat_id)
        return

    if data.startswith("settle:"):
        ledger_id = data[len("settle:") :]
        await _reply_settle_guarantee(client, event.chat_id, int(ledger_id))
        return

    # B9/B2: المفوّضون — للمالك فقط (لا زر لها أصلًا بقائمة المفوّض، وهذا
    # الفحص الإضافي دفاع بالعمق لو خمّن مفوّض callback_data بنفسه).
    if data == "admin:delegates":
        if not is_owner_chat(event.chat_id):
            return
        await delegates.reply_delegates_menu(client, event.chat_id)
        return

    if data == "admin:delegates_add":
        if not is_owner_chat(event.chat_id):
            return
        _save_session(event.chat_id, "delegate_name", {})
        await client.send_message(
            event.chat_id, "اكتب اسم المفوّض الجديد:", buttons=nav_rows("admin:delegates", "admin:menu")
        )
        return

    if data.startswith("deleg:rm:"):
        if not is_owner_chat(event.chat_id):
            return
        delegate_id = int(data[len("deleg:rm:") :])
        await delegates.reply_remove_delegate(client, event.chat_id, delegate_id)
        return


# -------------------------------------------------------------------
# توجيه الرسائل النصية أثناء انتظار مُدخَل (خطوات متعددة الرسائل)
# -------------------------------------------------------------------


async def _handle_step_text(event: ChatEvent, client: TelegramClient, step: str, data: dict) -> None:
    text = event.text.strip()

    if step == "new_customer_name":
        _save_session(event.chat_id, "new_customer_phone", {"name": text})
        await client.send_message(
            event.chat_id,
            "تمام. الآن اكتب رقم جوال العميل (أرقام فقط):",
            buttons=nav_rows("admin:new_customer", "admin:menu"),
        )
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
        buttons = [
            [
                {"text": "▶️ تفعيل", "callback_data": f"status_choice:{customer_id}:active"},
                {"text": "⏸️ إيقاف", "callback_data": f"status_choice:{customer_id}:paused"},
            ]
        ]
        buttons.extend(nav_rows(None, "admin:menu"))
        await client.send_message(
            event.chat_id, f"اختر الحالة الجديدة للعميل #{customer_id}:", buttons=buttons
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
            event.chat_id,
            f"كم عدد الأيام اللي تحب تمدّدها؟ (اكتب رقمًا، افتراضيًا {EXTEND_DEFAULT_DAYS}):",
            buttons=nav_rows("admin:extend", "admin:menu"),
        )
        return

    if step == "extend_days":
        # B9/A6: كان أي إدخال غير رقمي هنا (خطأ كتابة، مثلًا) يُمرّر بصمت
        # كـEXTEND_DEFAULT_DAYS (30 يومًا) بلا أي إشعار — قد يُمدّد اشتراك
        # عميل بعدد أيام لم يقصده أحمد إطلاقًا. الآن: نفس نمط إعادة الطلب
        # المُستخدم بكل خطوة رقمية أخرى بهذا الملف (extend_id/status_id/...)
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

    # B9/B1: المفوّضون — خطوتا الإضافة (اسم ثم @username/جوال)
    if step == "delegate_name":
        _save_session(event.chat_id, "delegate_contact", {"name": text})
        await client.send_message(
            event.chat_id,
            "اكتب @username المفوّض أو رقم جواله:",
            buttons=nav_rows("admin:delegates", "admin:menu"),
        )
        return

    if step == "delegate_contact":
        _clear_session(event.chat_id)
        await delegates.reply_add_delegate(client, event.chat_id, data.get("name", ""), text)
        return

    # B9/B1: نص لا يطابق أي خطوة معروفة — اعتذار قصير + القائمة، بدل صمت
    # ضمني كما كان (راجع الدليل §B1: "ما فهمت الطلب — اختر من القائمة 👇").
    _clear_session(event.chat_id)
    await _send_main_menu(client, event.chat_id, prefix="ما فهمت الطلب — اختر من القائمة 👇")


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
        "سيربط العميل حسابه بنفسه عند مراسلة بوت مسار ومشاركة رقم جواله.\n"
        # B9/B0: customers.status الافتراضي أصبح 'pending' (ترحيل
        # 0017_b9_payments_delegates) — لم يعد العميل الجديد active فورًا.
        "سيُفعّل عند تأكيد الدفع أو يدويًا من بطاقته.",
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
    مُغلَق سهوًا)."""
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
    buttons.extend(nav_rows(None, "admin:menu"))
    await client.send_message(chat_id, "\n".join(lines), buttons=buttons)


async def _reply_settle_guarantee(client: TelegramClient, chat_id: int, ledger_id: int) -> None:
    try:
        result = await guarantee_api.settle_guarantee(ledger_id)
    except HTTPException as exc:
        await client.send_message(chat_id, f"⚠️ {exc.detail}")
        return
    suffix = " (كانت مُسوّاة مسبقًا)" if result.get("already_settled") else ""
    await client.send_message(chat_id, f"✅ تمت تسوية الضمان #{result['id']}{suffix}.")


# B9/B2: منطق المفوّضين (ربط تلقائي، قائمة، إضافة، إزالة) بملف منفصل
# app.telegram_admin_delegates (مُستورَد أعلاه كـ`delegates`) — راجع docstring
# ذلك الملف لسبب الفصل (حجم repo_write لكل ملف).
