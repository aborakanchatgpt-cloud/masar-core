"""B9/B5: أوامر بوت الأدمن البسيطة (نظرة عامة، تصنيف، تسجيل عميل، تقرير
عميل، تشغيل التقارير، تفعيل/إيقاف، تمديد، الضمانات) — انتُزعت من
`telegram_admin.py` نفسه فقط لتبقى دون حدّ `repo_write` (~25 ألف حرف)،
لا لأي سبب تصميمي (خلاف `telegram_admin_delegates.py`/`_search.py`/
`_settings.py` التي فُصلت أيضًا لتماسك المسؤولية). لا إدارة جلسة هنا —
`telegram_admin.py` وحده يملك خطوات `telegram_sessions` (نفس القاعدة
المتّبعة بكل الملفات الفرعية الأخرى)؛ كل دالة هنا تأخذ مُدخلات نظيفة جاهزة
وتستدعي نقطة النهاية الأساسية مباشرة (customers_api/overview_api/
reports_api/guarantee_api) — لا إعادة تطبيق لأي منطق أعمال.

B9/B5-hotfix (11 سبتمبر، بعد مراجعة أحمد): `reports_api.customer_report`
و`guarantee_api.pending_guarantees` نقطتا نهاية FastAPI بمعاملات افتراضية
`Query(...)` — عند استدعائهما هنا كدوال بايثون مباشرة (بلا HTTP) بلا تمرير
تلك المعاملات صراحة، لا تُحلّ `Query(...)` إلى قيمتها الفعلية (تبقى كائن
Query نفسه) → SQL يفشل بصمت (`cannot adapt type 'Query'`) والزر "لا
يستجيب". الإصلاح: تمرير القيم صراحة بكل استدعاء مباشر (`date=None`،
`limit=...، after_id=0`) + شبكة أمان `except Exception` تُخبر أحمد بخطأ
داخلي بدل الصمت التام لأي عطل غير متوقع مستقبلي بنفس النمط.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy import text as sql_text

from app import customers_api, guarantee_api, overview_api, reports_api
from app.discovery import get_engine
from app.phone import canonical_phone
from app.planner import now_riyadh
from app.reports import fetch_customer_chat_id
from app.telegram_client import TelegramClient
from app.telegram_nav import nav_rows
from app.telegram_notify_admin import notify_customer

logger = logging.getLogger("masar.telegram_admin_commands")


async def reply_create_customer(client: TelegramClient, chat_id: int, name: str, phone_text: str) -> None:
    # B9/A1: توحيد الصيغة هنا يضمن تطابقها لاحقًا مع الرقم الذي يرسله تيليجرام
    # فعليًا (966xxxxxxxxx) عند مشاركة العميل رقمه — راجع app/phone.py
    phone_digits = canonical_phone(phone_text)
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


async def reply_overview(client: TelegramClient, chat_id: int) -> None:
    """B9/B5: صيغة منسّقة بدل قواميس بايثون خام (راجع الدليل §B5) — تدمج
    🧭 تصنيف العملاء (أعلى 5 مجالات/مدن) بنهايتها بدل زر مستقل. كل الأرقام
    من `overview_api.overview()` (مصدر واحد — لا إعادة استعلام هنا)."""
    o = await overview_api.overview()
    by_status = o["customers_by_status"]
    mail_by_status = o["mail_links_by_status"]

    families_line = (
        " · ".join(f"{f} ({n})" for f, n in o["top_families"]) if o["top_families"] else "لا بيانات بعد"
    )
    cities_line = " · ".join(f"{c} ({n})" for c, n in o["top_cities"]) if o["top_cities"] else "لا بيانات بعد"

    lines = [
        f"📊 نظرة عامة — {now_riyadh().strftime('%Y-%m-%d')}",
        f"👥 العملاء: نشط {by_status.get('active', 0)} · بانتظار الدفع {by_status.get('pending', 0)} · "
        f"موقوف {by_status.get('paused', 0)} · منتهٍ {by_status.get('expired', 0)}",
        f"📧 صناديق بريد مربوطة: {mail_by_status.get('ok', 0)} (فشل: {mail_by_status.get('failed', 0)})",
        f"📤 تقديمات اليوم: {o['sends_today']} · آخر 7 أيام: {o['sends_last_7_days']} · "
        f"فشل اليوم: {o['sends_failed_today']}",
        f"📬 ارتدادات اليوم: {o['bounces_today']}",
        f"💳 طلبات دفع معلّقة: {o['pending_payment_requests']}",
        f"📨 تقارير معلّقة: {o['pending_reports']} · 💰 ضمانات معلّقة: {o['pending_guarantees']}",
        f"🧭 أكثر المجالات: {families_line}",
        f"🏙️ أكثر المدن: {cities_line}",
        f"✉️ وضع الإرسال: {'تجريبي 🧪' if o['dry_run'] else 'حقيقي ✅'}",
        # B4/v2-B6 (12 سبتمبر): skill_gap.py استبدل اعتماد ANTHROPIC_API_KEY
        # ببديل إحصائي مجاني بالكامل (راجع docstring رأس ذلك الملف) — الميزة
        # مفعّلة دومًا الآن بلا أي بوابة مفتاح خارجي؛ عميل واحد قد يُتخطّى
        # بصمت فقط لو قلّت بيانات وظائف مجاله عن 10 آخر 30 يومًا.
        "🌟 نصيحة الجمعة: مفعّلة (إحصائية، بلا اعتماد خارجي)",
    ]
    await client.send_message(chat_id, "\n".join(lines), buttons=nav_rows(None, "admin:menu"))


async def reply_segments(client: TelegramClient, chat_id: int) -> None:
    """B9/B5: لم تعد زرًا مستقلًا بالقائمة (أُدمجت ب📊 نظرة عامة)، لكن
    الدالة/المسار يبقيان فعّالين لأي مرجع قديم (`admin:segments`)."""
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

    lines = ["🧭 تصنيف العملاء", "", f"حسب الحالة: {by_status}", "", "أكثر 10 مجالات مهنية:"]
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


async def reply_customer_report(client: TelegramClient, chat_id: int, text: str) -> None:
    try:
        customer_id = int(text.strip())
    except ValueError:
        await client.send_message(chat_id, "اكتب رقم معرّف عميل صحيح (أرقام فقط).")
        return
    try:
        # B9/B5-hotfix: تمرير date صراحة — راجع docstring الملف أعلاه.
        result = await reports_api.customer_report(customer_id, date=None)
    except HTTPException as exc:
        await client.send_message(chat_id, f"⚠️ {exc.detail}")
        return
    except Exception:  # noqa: BLE001
        logger.exception("خطأ غير متوقع بجلب تقرير العميل #%s", customer_id)
        await client.send_message(chat_id, "⚠️ تعذّر جلب التقرير الآن (خطأ داخلي) — سنراجعه.")
        return
    payload = result.get("payload") or {}
    report_text = payload.get("text") if isinstance(payload, dict) else None
    await client.send_message(
        chat_id, f"📨 تقرير العميل #{customer_id}:\n\n{report_text or 'لا يوجد تقرير لهذا اليوم بعد.'}"
    )


async def reply_run_reports(client: TelegramClient, chat_id: int) -> None:
    result = await reports_api.run_reports(None)
    await client.send_message(
        chat_id,
        "✅ تشغيل التقارير: "
        f"أُنشئ {result.get('created', 0)}، موجود مسبقًا {result.get('already_existed', 0)}، "
        f"أخطاء {result.get('errors', 0)}.",
    )


async def reply_set_status(client: TelegramClient, chat_id: int, customer_id: int, new_status: str) -> None:
    try:
        result = await customers_api.update_customer_status(
            customer_id, customers_api.CustomerStatusRequest(status=new_status)
        )
    except HTTPException as exc:
        await client.send_message(chat_id, f"⚠️ {exc.detail}")
        return
    label = "مُفعّل ▶️" if new_status == "active" else "مُوقف ⏸️"
    await client.send_message(chat_id, f"✅ تم تحديث حالة العميل #{result['customer_id']} إلى {label}.")
    # B4/v2 §تحديثات تلقائية: نُشعر العميل نفسه فور تغيير حالته — best-effort
    # (notify_customer لا يرفع استثناءً أبدًا)؛ عميل بلا chat_id (لم يربط
    # حسابه بعد عبر تيليجرام) يُتجاهل بصمت هنا، لا خطأ للأدمن.
    customer_chat_id = fetch_customer_chat_id(get_engine(), customer_id)
    if customer_chat_id is not None:
        if new_status == "active":
            notify_customer(customer_chat_id, "تم تفعيل حسابك ✅ يمكنك الآن استخدام خدمات مسار بالكامل.")
        elif new_status == "paused":
            notify_customer(
                customer_chat_id,
                "تم إيقاف حسابك مؤقتًا 🙏 لأي استفسار تواصل معنا من القائمة الرئيسية.",
            )


async def reply_extend_subscription(client: TelegramClient, chat_id: int, customer_id: int, days: int) -> None:
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
    # B4/v2 §تحديثات تلقائية: إشعار العميل بالتمديد (best-effort، راجع
    # التعليق بـreply_set_status أعلاه لنفس منطق التجاهل الصامت بلا chat_id).
    customer_chat_id = fetch_customer_chat_id(engine, customer_id)
    if customer_chat_id is not None:
        notify_customer(customer_chat_id, f"تم تمديد اشتراكك {days} يومًا ✅ ينتهي الآن: {row[1]}.")


async def reply_guarantees(client: TelegramClient, chat_id: int) -> None:
    try:
        # B9/B5-hotfix: تمرير limit/after_id صراحة — راجع docstring الملف أعلاه.
        result = await guarantee_api.pending_guarantees(limit=guarantee_api.DEFAULT_PAGE_LIMIT, after_id=0)
    except Exception:  # noqa: BLE001
        logger.exception("خطأ غير متوقع بجلب الضمانات المعلّقة")
        await client.send_message(chat_id, "⚠️ تعذّر جلب الضمانات الآن (خطأ داخلي) — سنراجعه.")
        return
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


async def reply_settle_guarantee(client: TelegramClient, chat_id: int, ledger_id: int) -> None:
    try:
        result = await guarantee_api.settle_guarantee(ledger_id)
    except HTTPException as exc:
        await client.send_message(chat_id, f"⚠️ {exc.detail}")
        return
    suffix = " (كانت مُسوّاة مسبقًا)" if result.get("already_settled") else ""
    await client.send_message(chat_id, f"✅ تمت تسوية الضمان #{result['id']}{suffix}.")
