"""B9/B3: 💳 طلبات الدفع ببوت الأدمن — عرض المعلّقة يدويًا + قرار ✅/❌.

**الفحص الفعلي** (المالك أو مفوّض) يقع بـ`telegram_admin.py` **قبل** استدعاء
أي دالة هنا (نفس نمط `telegram_admin_settings.py`/`telegram_admin_search.py`
— لا تكرار للفحص بهذا الملف). لا إدارة جلسة هنا — لا خطوة نصية تحتاجها هذه
الميزة (كل شيء أزرار).

**الإشعار الفوري بالصورة/الملف** (الذي يحمل زرّي ✅/❌ فعليًا) يُرسَل مباشرة
من `app.telegram_payments._notify_admin_with_receipt` وقت تقديم العميل
لإيصاله — هذا الملف هو **قائمة احتياطية** (`💳 طلبات الدفع` بالقائمة
الرئيسية) لمراجعة كل الطلبات المعلّقة دفعة واحدة (نصًّا مختصرًا لا صورة،
حتى 20)، لأي طلب فاته أحمد أو يريد مراجعته لاحقًا — كلا المسارين ينتهيان
لنفس `decide_payment` أدناه فلا ازدواج منطق.

**تكامل `catalog.py`**: عند الموافقة، `catalog.create_order` تُستدعى
مباشرة كدالة بايثون (بلا HTTP، بلا معامل `Query(...)` — راجع docstring
`app.telegram_payments` لتفصيل لماذا هذا آمن من فخّ B9/B5-hotfix). تحديث
`payment_requests.status` يقع بمعاملة منفصلة **قبل** استدعاء `create_order`
(لا معاملة واحدة موحّدة بين الملفين — قرار عملي: `catalog.create_order`
تدير معاملتها الخاصة أصلًا ولم نُرِد إعادة هيكلتها؛ عند فشلها نُعيد
`payment_requests.status` لـ'pending' ونُخبر أحمد بالخطأ الفعلي بدل ترك
حالة متضاربة صامتة)."""
from __future__ import annotations

import logging

from sqlalchemy import text as sql_text

from app import catalog
from app.discovery import get_engine
from app.telegram_client import TelegramAPIError, TelegramClient, get_customer_bot_client
from app.telegram_nav import nav_rows

logger = logging.getLogger("masar.telegram_admin_payments")


def _fetch_pending_requests(limit: int = 20) -> list[dict]:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            sql_text(
                """
                SELECT pr.id, pr.customer_id, pr.expected_amount, pr.declared_amount, pr.sender_name,
                       pr.created_at, p.name_ar AS product_name_ar, c.name AS customer_name
                FROM payment_requests pr
                JOIN products p ON p.code = pr.product_code
                JOIN customers c ON c.id = pr.customer_id
                WHERE pr.status = 'pending'
                ORDER BY pr.created_at ASC
                LIMIT :lim
                """
            ),
            {"lim": limit},
        ).mappings().all()
    return [dict(r) for r in rows]


async def reply_payment_requests_menu(client: TelegramClient, chat_id: int) -> None:
    rows = _fetch_pending_requests()
    if not rows:
        await client.send_message(
            chat_id, "💳 لا توجد طلبات دفع معلّقة حاليًا.", buttons=nav_rows(None, "admin:menu")
        )
        return

    lines = ["💳 طلبات الدفع المعلّقة:\n"]
    buttons: list[list[dict[str, str]]] = []
    for r in rows:
        amount = f"{float(r['declared_amount']):.0f}" if r["declared_amount"] is not None else "؟"
        lines.append(
            f"#{r['id']} — {r['customer_name'] or 'بلا اسم'} (#{r['customer_id']}) — "
            f"{r['product_name_ar']} — مكتوب: {amount} ريال — باسم: {r['sender_name'] or '؟'}"
        )
        buttons.append(
            [
                {"text": f"✅ وصلت #{r['id']}", "callback_data": f"pay:ok:{r['id']}"},
                {"text": f"❌ لم تصل #{r['id']}", "callback_data": f"pay:no:{r['id']}"},
            ]
        )
    buttons.extend(nav_rows(None, "admin:menu"))
    await client.send_message(chat_id, "\n".join(lines), buttons=buttons)


def _fetch_request(request_id: int) -> dict | None:
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            sql_text(
                """
                SELECT pr.id, pr.customer_id, pr.product_code, pr.status, p.name_ar AS product_name_ar,
                       c.name AS customer_name, c.telegram_chat_id, c.cv_pdf_path, c.cities, c.families
                FROM payment_requests pr
                JOIN products p ON p.code = pr.product_code
                JOIN customers c ON c.id = pr.customer_id
                WHERE pr.id = :id
                """
            ),
            {"id": request_id},
        ).mappings().first()
    return dict(row) if row else None


def _try_claim_request(request_id: int, new_status: str, decided_by_chat_id: int) -> bool:
    """يحدّث الحالة **فقط** إن كانت ما زالت 'pending' (شرط WHERE يمنع البتّ
    المزدوج بضغطتين متزامنتين على نفس الطلب — دفاع بالعمق فوق فحص الحالة
    المسبق بـ`decide_payment`)."""
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            sql_text(
                """
                UPDATE payment_requests
                SET status = :status, decided_at = now(), decided_by_chat_id = :chat_id
                WHERE id = :id AND status = 'pending'
                RETURNING id
                """
            ),
            {"status": new_status, "chat_id": decided_by_chat_id, "id": request_id},
        ).first()
    return row is not None


def _revert_to_pending(request_id: int) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            sql_text(
                "UPDATE payment_requests SET status = 'pending', decided_at = NULL, decided_by_chat_id = NULL "
                "WHERE id = :id"
            ),
            {"id": request_id},
        )


def _activate_customer(customer_id: int) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            sql_text(
                "UPDATE customers SET status = 'active', updated_at = now() "
                "WHERE id = :id AND status IN ('pending', 'paused', 'expired')"
            ),
            {"id": customer_id},
        )


def _as_list(value) -> list:
    if isinstance(value, list):
        return value
    return []


async def _notify_customer_confirmed(req: dict) -> None:
    chat_id = req.get("telegram_chat_id")
    if not chat_id:
        return
    customer_client = get_customer_bot_client()
    if customer_client is None:
        return

    onboarding_done = bool(req.get("cv_pdf_path")) and bool(_as_list(req.get("cities"))) and bool(
        _as_list(req.get("families"))
    )
    if onboarding_done:
        text = (
            f"تم تأكيد اشتراكك 🎉 {req['product_name_ar']}.\n\n"
            "نبدأ البحث والتقديم لك من أول يوم عمل بإذن الله 🤍"
        )
    elif not req.get("cv_pdf_path"):
        text = (
            f"تم تأكيد اشتراكك 🎉 {req['product_name_ar']}.\n\n"
            "نكمل تجهيز ملفك — أرسل سيرتك الذاتية كملف PDF 📄"
        )
    else:
        text = (
            f"تم تأكيد اشتراكك 🎉 {req['product_name_ar']}.\n\n"
            "باقي خطوة بسيطة: أكمل ملفك (المدن/المجالات) من نفس المحادثة."
        )
    try:
        await customer_client.send_message(chat_id, text)
    except TelegramAPIError:
        logger.warning("تعذّر إشعار عميل #%s بتأكيد الدفع", req["customer_id"], exc_info=True)


async def _notify_customer_rejected(req: dict) -> None:
    chat_id = req.get("telegram_chat_id")
    if not chat_id:
        return
    customer_client = get_customer_bot_client()
    if customer_client is None:
        return
    buttons = [
        [
            {"text": "📎 إرسال الإيصال من جديد", "callback_data": f"payretry:{req['product_code']}"},
            {"text": "📞 تواصل معنا", "callback_data": "paycontact"},
        ]
    ]
    try:
        await customer_client.send_message(
            chat_id,
            "ما قدرنا نتأكد من وصول الحوالة 🙏 تأكد من التحويل ثم أرسل الإيصال مرة ثانية، أو تواصل معنا:",
            buttons=buttons,
        )
    except TelegramAPIError:
        logger.warning("تعذّر إشعار عميل #%s برفض الدفع", req["customer_id"], exc_info=True)


async def decide_payment(client: TelegramClient, chat_id: int, message_id: int | None, request_id: int, decision: str) -> None:
    req = _fetch_request(request_id)
    if not req:
        await client.send_message(chat_id, "⚠️ طلب دفع غير موجود.")
        return
    if req["status"] != "pending":
        await client.send_message(chat_id, "تم البتّ بهذا الطلب مسبقًا.")
        return

    new_status = "confirmed" if decision == "ok" else "rejected"
    claimed = _try_claim_request(request_id, new_status, chat_id)
    if not claimed:
        await client.send_message(chat_id, "تم البتّ بهذا الطلب مسبقًا.")
        return

    # إزالة أزرار الرسالة الأصلية (إن وُجدت) فورًا — يمنع ضغطًا مزدوجًا،
    # نفس نمط _handle_feedback_callback بـtelegram_api.py.
    if message_id is not None:
        try:
            await client.edit_message_reply_markup(chat_id, message_id, buttons=[])
        except TelegramAPIError:
            logger.warning("تعذّر إزالة أزرار رسالة طلب الدفع (message_id=%s)", message_id)

    if decision == "no":
        await _notify_customer_rejected(req)
        await client.send_message(chat_id, f"✅ تم رفض طلب الدفع #{request_id} وإشعار العميل.")
        return

    try:
        await catalog.create_order(
            catalog.AdminOrderCreateRequest(customer_id=req["customer_id"], product_code=req["product_code"])
        )
    except Exception as exc:  # noqa: BLE001 — نعيد الحالة السابقة ونُخبر أحمد بالخطأ الفعلي بدل حالة متضاربة صامتة
        logger.exception("فشل تفعيل الطلب بعد تأكيد الدفع #%s", request_id)
        _revert_to_pending(request_id)
        await client.send_message(
            chat_id,
            f"⚠️ تعذّر تفعيل الاشتراك للطلب #{request_id} (خطأ داخلي: {exc}) — الطلب أُعيد لحالة معلّق، حاول مرة ثانية.",
        )
        return

    _activate_customer(req["customer_id"])
    await _notify_customer_confirmed(req)
    await client.send_message(
        chat_id, f"✅ تم تأكيد الدفع للعميل #{req['customer_id']} ({req['customer_name'] or ''}) — {req['product_name_ar']}."
    )
