"""
Masar Core — بوت الأدمن الخاص على تيليجرام (B8: إزالة n8n نهائيًا من مسار
الدخول الوارد). يُعيد بناء منطق ورشتي عمل n8n القديمتين
(`n8n/workflows/masar_admin_bot.json` + `masar_admin_bot_part2.json`) داخل
Core مباشرة، بقائمة أزرار inline واحدة تُغطّي كل ما يحتاجه أحمد يوميًا.

**بوابة وصول مغلقة افتراضيًا (fail-closed)** — نفس فلسفة app.auth تمامًا:
لا رسالة تُعالَج ولا ردّ يُرسل لأي محادثة غير `is_admin_chat` (المالك، أو
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

**B9/B5 — القائمة الموسّعة + البحث والإعدادات:** أُضيفت 🔍 بحث عن عميل
(يقبل الآن رقم/جوال/اسم جزئيًا، لا مُعرّفًا رقميًا فقط) ببطاقة عميل موسّعة
وأزرار إجراء (تفعيل/إيقاف/تمديد/تقرير/رسالة/رابط ربط بريد) بملف منفصل
`app.telegram_admin_search` (مستورَد كـ`search_mod`)، و✉️ رسالة لعميل (تعيد
استخدام نفس البحث)، و⚙️ الإعدادات (بيانات التحويل/الباقات/واتساب الدعم)
بملف منفصل `app.telegram_admin_settings` (مستورَد كـ`settings_mod`) —
للمالك حصرًا، نفس نمط فحص `is_owner_chat` المُستخدَم أصلًا مع 👥 المفوّضون.
🧩 تصنيف العملاء أُدمِجت بنهاية 📊 نظرة عامة (لم تعد زرًا مستقلًا بالقائمة،
لكن `admin:segments` يبقى مسارًا فعّالًا لأي مرجع قديم).

**B9/B3 — 💳 طلبات الدفع:** أصبحت زرًا فعليًا بالقائمة الرئيسية (ملف منفصل
`app.telegram_admin_payments`، مستورَد كـ`payments_mod`) — يعرض الطلبات
المعلّقة (حتى 20) بأزرار ✅/❌ لكل واحد. الإشعار الفوري بصورة/ملف الإيصال
(الذي يحمل نفس الزرّين) يصل مباشرة من `app.telegram_payments` وقت تقديم
العميل لإيصاله — هذه القائمة احتياطية لمراجعة كل المعلّق دفعة واحدة. كلا
المسارين يوجّهان لنفس `pay:ok:{id}`/`pay:no:{id}` أدناه.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text as sql_text

from app import telegram_admin_commands as commands
from app import telegram_admin_delegates as delegates
from app import telegram_admin_payments as payments_mod
from app import telegram_admin_search as search_mod
from app import telegram_admin_settings as settings_mod
from app.discovery import get_engine
from app.telegram_client import ChatEvent, TelegramClient
from app.telegram_nav import nav_rows

logger = logging.getLogger("masar.telegram_admin")

EXTEND_DEFAULT_DAYS = 30


# =========================================================================
# جلسة الأدمن — نفس جدول telegram_sessions المشترك مع onboarding (بلا
# تصادم عمليًا: chat_id الأدمن هو معرّف حساب Telegram الشخصي لأحمد، ولا
# يمكن أن يتطابق مع chat_id عميل حقيقي آخر — راجع تعليق تصميم الملف بتقرير
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
    # B9/B5: 🧩 تصنيف العملاء أُدمِجت بنهاية 📊 نظرة عامة (لم تعد زرًا
    # مستقلًا). ⏯️/⏳ تبقيان أيضًا هنا للوصول السريع (الدليل: "لا مانع")
    # فوق كونهما أزرار إجراء تحت بطاقة نتيجة البحث الآن.
    rows: list[list[dict[str, str]]] = [
        [{"text": "🆕 تسجيل عميل جديد", "callback_data": "admin:new_customer"}],
        [
            {"text": "🔍 بحث عن عميل", "callback_data": "admin:lookup"},
            {"text": "📨 تقرير عميل", "callback_data": "admin:report"},
        ],
        [{"text": "💳 طلبات الدفع", "callback_data": "admin:payments"}],
        [{"text": "📊 نظرة عامة", "callback_data": "admin:overview"}],
        [{"text": "▶️ تشغيل كل التقارير الآن", "callback_data": "admin:run_reports"}],
        [
            {"text": "⏯️ تفعيل/إيقاف عميل", "callback_data": "admin:status"},
            {"text": "⏳ تمديد اشتراك", "callback_data": "admin:extend"},
        ],
        [{"text": "💰 الضمانات المعلّقة", "callback_data": "admin:guarantees"}],
        [{"text": "✉️ رسالة لعميل", "callback_data": "admin:msg"}],
    ]
    # B9/B2+B5: قائمة المفوّضين والإعدادات — للمالك فقط (المفوّض لا يديرهما).
    if is_owner:
        rows.append(
            [
                {"text": "👥 المفوّضون", "callback_data": "admin:delegates"},
                {"text": "⚙️ الإعدادات", "callback_data": "admin:settings"},
            ]
        )
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
    # مفوّض بانتظار الربط أولاً، وإلا صمت تام (لا ردّ، لا كشف لوجود البوت).
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
        await commands.reply_overview(client, event.chat_id)
        return

    if data == "admin:segments":
        await commands.reply_segments(client, event.chat_id)
        return

    if data == "admin:lookup":
        _save_session(event.chat_id, "lookup_query", {})
        await client.send_message(
            event.chat_id,
            "اكتب رقم العميل، جواله، أو جزءًا من اسمه:",
            buttons=nav_rows(None, "admin:menu"),
        )
        return

    if data.startswith("admin:card:"):
        customer_id = int(data[len("admin:card:") :])
        await search_mod.reply_customer_card(client, event.chat_id, customer_id)
        return

    if data.startswith("admin:card_toggle:"):
        _, _, customer_id_str, new_status = data.split(":", 3)
        await search_mod.card_toggle_status(client, event.chat_id, int(customer_id_str), new_status)
        return

    if data.startswith("admin:card_extend:"):
        customer_id = int(data[len("admin:card_extend:") :])
        _save_session(event.chat_id, "extend_days", {"customer_id": customer_id})
        await client.send_message(
            event.chat_id,
            f"كم عدد الأيام اللي تحب تمدّدها؟ (افتراضيًا {EXTEND_DEFAULT_DAYS}):",
            buttons=nav_rows(None, "admin:menu"),
        )
        return

    if data.startswith("admin:card_report:"):
        customer_id = int(data[len("admin:card_report:") :])
        await search_mod.card_report(client, event.chat_id, customer_id)
        return

    if data.startswith("admin:card_link:"):
        customer_id = int(data[len("admin:card_link:") :])
        await search_mod.card_link(client, event.chat_id, customer_id)
        return

    if data == "admin:msg":
        _save_session(event.chat_id, "msg_search_query", {})
        await client.send_message(
            event.chat_id,
            "ابحث عن العميل (رقم/جوال/اسم) لإرسال رسالة له:",
            buttons=nav_rows(None, "admin:menu"),
        )
        return

    if data.startswith("admin:msgto:"):
        customer_id = int(data[len("admin:msgto:") :])
        _save_session(event.chat_id, "msg_text", {"customer_id": customer_id})
        await client.send_message(
            event.chat_id, "اكتب نص الرسالة اللي تبي ترسلها للعميل:", buttons=nav_rows(None, "admin:menu")
        )
        return

    if data == "admin:report":
        _save_session(event.chat_id, "report_id", {})
        await client.send_message(
            event.chat_id, "اكتب رقم معرّف العميل لعرض تقرير اليوم:", buttons=nav_rows(None, "admin:menu")
        )
        return

    if data == "admin:run_reports":
        await commands.reply_run_reports(client, event.chat_id)
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
        await commands.reply_set_status(client, event.chat_id, int(customer_id_str), new_status)
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
        await commands.reply_guarantees(client, event.chat_id)
        return

    if data == "admin:payments":
        await payments_mod.reply_payment_requests_menu(client, event.chat_id)
        return

    if data.startswith("pay:ok:") or data.startswith("pay:no:"):
        decision = "ok" if data.startswith("pay:ok:") else "no"
        request_id = int(data.rsplit(":", 1)[-1])
        await payments_mod.decide_payment(client, event.chat_id, event.message_id, request_id, decision)
        return

    if data.startswith("settle:"):
        ledger_id = data[len("settle:") :]
        await commands.reply_settle_guarantee(client, event.chat_id, int(ledger_id))
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

    # B9/B5: ⚙️ الإعدادات — للمالك فقط (نفس نمط 👥 المفوّضون أعلاه).
    if data == "admin:settings":
        if not is_owner_chat(event.chat_id):
            return
        await settings_mod.reply_settings_menu(client, event.chat_id)
        return

    if data == "settings:bank":
        if not is_owner_chat(event.chat_id):
            return
        await settings_mod.reply_bank_details(client, event.chat_id)
        return

    if data.startswith("settings:bank_edit:"):
        if not is_owner_chat(event.chat_id):
            return
        field = data[len("settings:bank_edit:") :]
        _save_session(event.chat_id, "settings_bank_edit", {"field": field})
        label = settings_mod.BANK_FIELD_LABELS.get(field, field)
        await client.send_message(
            event.chat_id, f"اكتب القيمة الجديدة لـ{label}:", buttons=nav_rows("settings:bank", "admin:menu")
        )
        return

    if data == "settings:whatsapp":
        if not is_owner_chat(event.chat_id):
            return
        _save_session(event.chat_id, "settings_whatsapp_edit", {})
        await settings_mod.reply_whatsapp_prompt(client, event.chat_id)
        return

    if data == "settings:packages":
        if not is_owner_chat(event.chat_id):
            return
        await settings_mod.reply_packages_menu(client, event.chat_id)
        return

    if data.startswith("settings:pkg_toggle:"):
        if not is_owner_chat(event.chat_id):
            return
        code = data[len("settings:pkg_toggle:") :]
        await settings_mod.toggle_package_active(client, event.chat_id, code)
        return

    if data.startswith("settings:pkg_edit:"):
        if not is_owner_chat(event.chat_id):
            return
        _, _, code, field = data.split(":", 3)
        _save_session(event.chat_id, "settings_pkg_edit", {"code": code, "field": field})
        label = settings_mod.PKG_FIELD_LABELS.get(field, field)
        await client.send_message(
            event.chat_id,
            f"اكتب القيمة الجديدة لـ{label} (أرقام فقط):",
            buttons=nav_rows(f"settings:pkg:{code}", "admin:menu"),
        )
        return

    # "settings:pkg:{code}" — فتح تفاصيل باقة (لا تصادم startswith مع
    # "settings:pkg_toggle:"/"settings:pkg_edit:" أعلاه: الحرف التالي لـ
    # "settings:pkg" هنا ":" لا "_"، فالبادئات الثلاث متمايزة تمامًا).
    if data.startswith("settings:pkg:"):
        if not is_owner_chat(event.chat_id):
            return
        code = data[len("settings:pkg:") :]
        await settings_mod.reply_package_detail(client, event.chat_id, code)
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
        _clear_session(event.chat_id)
        await commands.reply_create_customer(client, event.chat_id, data.get("name", ""), text)
        return

    if step == "lookup_query":
        _clear_session(event.chat_id)
        results = search_mod.search_customers(text)
        if not results:
            await search_mod.reply_not_found(client, event.chat_id, text)
        elif len(results) == 1:
            await search_mod.reply_customer_card(client, event.chat_id, results[0]["id"])
        else:
            await search_mod.reply_pick_buttons(client, event.chat_id, results, prefix="admin:card:")
        return

    if step == "msg_search_query":
        _clear_session(event.chat_id)
        results = search_mod.search_customers(text)
        if not results:
            await search_mod.reply_not_found(client, event.chat_id, text)
        elif len(results) == 1:
            customer_id, customer_name = results[0]["id"], results[0]["name"]
            _save_session(event.chat_id, "msg_text", {"customer_id": customer_id})
            await client.send_message(
                event.chat_id,
                f"اكتب نص الرسالة اللي تبي ترسلها لـ{customer_name} (#{customer_id}):",
                buttons=nav_rows(None, "admin:menu"),
            )
        else:
            await search_mod.reply_pick_buttons(client, event.chat_id, results, prefix="admin:msgto:")
        return

    if step == "msg_text":
        customer_id = int(data.get("customer_id", 0))
        _clear_session(event.chat_id)
        await search_mod.send_customer_message_and_log(client, event.chat_id, customer_id, text)
        return

    if step == "settings_bank_edit":
        field = data.get("field", "")
        _clear_session(event.chat_id)
        await settings_mod.apply_bank_field(client, event.chat_id, field, text)
        return

    if step == "settings_whatsapp_edit":
        _clear_session(event.chat_id)
        await settings_mod.apply_whatsapp(client, event.chat_id, text)
        return

    if step == "settings_pkg_edit":
        code = data.get("code", "")
        field = data.get("field", "")
        label = settings_mod.PKG_FIELD_LABELS.get(field, field)
        try:
            parsed: float | int = float(text) if field == "price_sar" else int(text)
        except ValueError:
            await client.send_message(event.chat_id, f"⚠️ اكتب رقمًا صحيحًا لـ{label}:")
            return
        _clear_session(event.chat_id)
        await settings_mod.apply_package_field(client, event.chat_id, code, field, parsed)
        return

    if step == "report_id":
        _clear_session(event.chat_id)
        await commands.reply_customer_report(client, event.chat_id, text)
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
            f"كم عدد الأيام اللي تحب تمدّدها؟ (افتراضيًا {EXTEND_DEFAULT_DAYS}):",
            buttons=nav_rows("admin:extend", "admin:menu"),
        )
        return

    if step == "extend_days":
        # B9/A6: كان أي إدخال غير رقمي هنا (خطأ كتابة، مثلاً) يُمرّر بصمت
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
        await commands.reply_extend_subscription(client, event.chat_id, customer_id, days)
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


# B9/B2: منطق المفوّضين (ربط تلقائي، قائمة، إضافة، إزالة) بملف منفصل
# app.telegram_admin_delegates (مستورَد أعلاه كـ`delegates`) — راجع docstring
# ذلك الملف لسبب الفصل (حجم repo_write لكل ملف).
