"""B9/B3: تدفّق الباقات والدفع ببوت العميل "مسار" — الدليل §B3.

يُستدعى حصرًا من `app.telegram_onboarding` (لا مسار وارد مستقل — نفس فلسفة
`app.telegram_admin_settings`/`telegram_admin_search`: ملف منطق مُستورَد،
لا يحمل نقطة دخول Webhook خاصة به). التدفّق: **الباقات → الشروط (أول مرة
فقط) → بيانات التحويل → الإيصال → المبلغ المُعلَن → اسم المُحوِّل → إشعار
الأدمن بالصورة/الملف + زرّي ✅/❌**. قرار الأدمن نفسه (تأكيد/رفض) يُعالَج
بملف منفصل `app.telegram_admin_payments` (بوت مختلف تمامًا، ويب هوك منفصل
— لا تصادم بأسماء callback_data رغم اشتراك بادئة "pay" في كليهما).

**جلسة المحادثة**: نفس جدول `telegram_sessions` المشترك مع
`telegram_onboarding.py`، لكن بدوال قراءة/كتابة/مسح **خاصة بهذا الملف**
(نفس نمط التكرار المتعمَّد المُستخدَم أصلًا بـ`telegram_admin.py` مقابل
`telegram_onboarding.py` — كل ملف مستقل تمامًا، لا اعتماد متبادل على دوال
خاصة (`_prefixed`) بملف آخر). `PAYMENT_FLOW_STEPS` أدناه هي القائمة التي
يتحقّق منها `telegram_onboarding._handle_onboarding_step` ليقرّر تفويض
معالجة الرسالة/الضغطة لهذا الملف بدل خطوات السيرة الذاتية/المدن/المجالات
المعتادة.

**التسعير**: باقة بلا `price_sar` (لم يحدّده أحمد بعد من ⚙️ الإعدادات) لا
تظهر للعميل إطلاقًا — إن لم تكن أي باقة مُسعّرة بعد، العميل الجديد لا يقدر
يكمل تسجيله، ويُخبَر بانتظار قصير بدل رسالة فنية، ويُنبّه أحمد فورًا (نفس
منطق `PRICE_TBD` بـ`catalog.py` — لا سعر تخميني أبدًا).

**الإيصال**: يُحفَظ خامًا تحت `{CV_DATA_DIR}/payments/{customer_id}/{request_id}.<ext>`
(نفس جذر `CV_DATA_DIR` المُستخدَم أصلًا للسير الذاتية، فيُشمَل تلقائيًا
بنسخ دعم/backup.sh الاحتياطي — لا حاجة لتعديل سكربت النسخ). لا حذف —
فشل/رفض الطلب يُبقي `payment_requests` كسجلّ تاريخي (`status='rejected'`)
لا صفًا محذوفًا.

**تكامل `catalog.py`**: تفعيل الطلب بعد موافقة الأدمن يستدعي
`catalog.create_order` **مباشرة كدالة بايثون** (لا HTTP داخلي، نفس نمط كل
`telegram_admin*.py` مع `customers_api`/`overview_api`/`guarantee_api`) —
هذه الدالة بلا أي معامل `Query(...)` افتراضي (جسم Pydantic فقط)، فلا تقع
بفخ B9/B5-hotfix الموثّق بـ`telegram_admin_commands.py`."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from sqlalchemy import text as sql_text

from app import catalog
from app import telegram_admin_settings as settings_mod
from app.discovery import get_engine
from app.telegram_client import ChatEvent, TelegramAPIError, TelegramClient, get_admin_bot_client

logger = logging.getLogger("masar.telegram_payments")

RECEIPT_MAX_BYTES = 5 * 1024 * 1024  # 5MB — يطابق الدليل §B3 البند 6
_ALLOWED_DOCUMENT_MIME = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
}

# B9/B3: الخطوات التي يعالجها هذا الملف — telegram_onboarding._handle_onboarding_step
# يتحقّق من هذه المجموعة (أو من بادئة callback_data الثابتة "payretry:"/"paycontact"
# التي قد تصل بلا خطوة جلسة نشطة، بعد رفض أدمن) ليقرّر تفويض المعالجة هنا.
PAYMENT_FLOW_STEPS = {
    "await_package",
    "await_bank_retry",
    "await_terms",
    "await_receipt_file",
    "await_receipt_amount",
    "await_amount_confirm",
    "await_receipt_sender",
}


# =========================================================================
# جلسة المحادثة — نسخة خاصة بهذا الملف (راجع docstring رأس الملف)
# =========================================================================


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


# =========================================================================
# استعلامات قصيرة
# =========================================================================


def _fetch_priced_products() -> list[dict[str, Any]]:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            sql_text(
                """
                SELECT code, name_ar, type AS kind, price_sar, days, applications_included
                FROM products
                WHERE active = true AND price_sar IS NOT NULL
                ORDER BY CASE type WHEN 'subscription' THEN 0 WHEN 'credits' THEN 1 ELSE 2 END, price_sar
                """
            )
        ).mappings().all()
    return [dict(r) for r in rows]


def _fetch_product(code: str) -> dict[str, Any] | None:
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            sql_text(
                "SELECT code, name_ar, type AS kind, price_sar, days, applications_included "
                "FROM products WHERE code = :c AND active = true AND price_sar IS NOT NULL"
            ),
            {"c": code},
        ).mappings().first()
    return dict(row) if row else None


def _fetch_customer_basic(customer_id: int) -> dict[str, Any]:
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            sql_text("SELECT id, name, phone, terms_accepted_at FROM customers WHERE id = :id"),
            {"id": customer_id},
        ).mappings().first()
    return dict(row) if row else {}


def _accept_terms(customer_id: int) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            sql_text("UPDATE customers SET terms_accepted_at = now(), updated_at = now() WHERE id = :id"),
            {"id": customer_id},
        )


def _create_payment_request(customer_id: int, product_code: str, expected_amount: float) -> int:
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            sql_text(
                """
                INSERT INTO payment_requests (customer_id, product_code, expected_amount, status)
                VALUES (:cid, :code, :amount, 'pending') RETURNING id
                """
            ),
            {"cid": customer_id, "code": product_code, "amount": expected_amount},
        ).first()
    return row[0]


def _update_payment_receipt(request_id: int, path: str, kind: str) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            sql_text(
                "UPDATE payment_requests SET receipt_path = :p, receipt_kind = :k WHERE id = :id"
            ),
            {"p": path, "k": kind, "id": request_id},
        )


def _finalize_payment_request(
    request_id: int, declared_amount: float, sender_name: str, *, amount_mismatch: bool
) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            sql_text(
                """
                UPDATE payment_requests
                SET declared_amount = :amount, sender_name = :sender,
                    admin_note = :note
                WHERE id = :id
                """
            ),
            {
                "amount": declared_amount,
                "sender": sender_name,
                "note": "⚠️ المبلغ المكتوب أقل من قيمة الباقة — أكّده العميل رغم التنبيه." if amount_mismatch else None,
                "id": request_id,
            },
        )


def _fetch_payment_request(request_id: int) -> dict[str, Any] | None:
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            sql_text(
                """
                SELECT pr.*, p.name_ar AS product_name_ar
                FROM payment_requests pr JOIN products p ON p.code = pr.product_code
                WHERE pr.id = :id
                """
            ),
            {"id": request_id},
        ).mappings().first()
    return dict(row) if row else None


# =========================================================================
# دوال نقية (نصوص/أزرار) — قابلة للاختبار بلا قاعدة بيانات
# =========================================================================


def build_packages_message(products: list[dict[str, Any]]) -> str:
    lines = ["اخترنا لك أفضل الباقات، وربنا يوفقنا نوفّق لك أفضل اختيار 🤍\n"]
    for p in products:
        price = f"{float(p['price_sar']):.0f} ريال"
        if p["kind"] == "subscription":
            desc = f"اشتراك شهري — تقديم يومي لمدة {p['days']} يومًا (حتى {p['applications_included']} تقديم)، مع ضمان استكمال العدد أو تعويض الفرق، وسيرة ذاتية بصيغة ATS مجانًا"
        elif p["kind"] == "credits":
            desc = f"رصيد {p['applications_included']} تقديم — لا ينتهي، بنفس الوتيرة اليومية (بلا ضمان)"
        else:
            desc = "إعداد سيرة ذاتية احترافية مستقلة"
        lines.append(f"📦 {p['name_ar']} — {price}\n{desc}\n")
    return "\n".join(lines).strip()


def build_packages_buttons(products: list[dict[str, Any]]) -> list[list[dict[str, str]]]:
    rows = [[{"text": p["name_ar"], "callback_data": f"pkg:{p['code']}"}] for p in products]
    rows.append([{"text": "❓ أيها يناسبني؟", "callback_data": "pkgwhich"}])
    return rows


WHICH_PACKAGE_ADVICE = (
    "إذا تبي بحثًا مستمرَّا يوميًا وتضمن عدد تقديمات ثابت كل شهر، الاشتراك الشهري أنسب لك 🤍\n\n"
    "وإذا تبي تجرب الخدمة أو تحتاج عدد تقديمات محدد بلا التزام شهري، باقات الرصيد أنسب — رصيدك ما "
    "ينتهي أبدًا مهما طال الوقت."
)


def build_terms_message(domain: str) -> str:
    link = f"https://{domain}/terms" if domain else "(الرابط غير متاح حاليًا، تواصل معنا)"
    return (
        "قبل ما نكمل، هذي شروط الخدمة وطريقة تعاملنا مع بياناتك:\n"
        f"{link}\n\n"
        "لما تقرأها اضغط \"أوافق\" ونكمل 🌿"
    )


def build_bank_message(banks: list[dict[str, Any]], package_name: str, price_sar: float) -> str:
    """B3-متابعة: `banks` قائمة حسابات نشطة (`bank_name`/`account_holder`/
    `iban`) من `telegram_admin_settings.active_bank_accounts()` — قد تكون
    أكثر من حساب واحد الآن، فيُعرَض كل حساب مرقّمًا إن كان أكثر من واحد."""
    intro = f"تمام ✅ اخترت {package_name} بقيمة {price_sar:.0f} ريال.\n"
    lead = "حوّل المبلغ على أي من الحسابات التالية:" if len(banks) > 1 else "حوّل المبلغ على:"
    lines = [intro, lead]
    for i, b in enumerate(banks, start=1):
        prefix = f"{i}) " if len(banks) > 1 else ""
        lines.append(f"\n{prefix}🏦 {b['bank_name']}\n👤 {b['account_holder']}\n🔢 IBAN: {b['iban']}")
    lines.append("\nبعد التحويل أرسل لي صورة الإيصال أو ملفه هنا 📎")
    return "\n".join(lines).strip()


# =========================================================================
# بداية التدفّق — تُستدعى من telegram_onboarding بعد التقاط اسم عميل جديد
# =========================================================================


async def start(client: TelegramClient, chat_id: int, customer_id: int) -> None:
    products = _fetch_priced_products()
    if not products:
        await client.send_message(
            chat_id, "نجهّز باقاتنا حاليًا، بنكمل معك خلال وقت قصير بإذن الله 🤍"
        )
        await _notify_admin_urgent(
            f"نحتاجك فورا — عميل جديد #{customer_id} وصل لخطوة اختيار الباقة، لكن ولا باقة مُسعّرة "
            "بعد بـ⚙️ الإعدادات → 💼 الباقات. أضف الأسعار حتى يقدر يكمل تسجيله."
        )
        _save_session(chat_id, "await_package", {})
        return
    _save_session(chat_id, "await_package", {})
    await client.send_message(
        chat_id, build_packages_message(products), buttons=build_packages_buttons(products)
    )


# =========================================================================
# المعالج الرئيسي — يُستدعى من telegram_onboarding._handle_onboarding_step
# =========================================================================


async def handle_step(event: ChatEvent, client: TelegramClient, customer_id: int, step: str, data: dict) -> None:
    # الضغطات (callback) تُوجّه أولاً حسب بادئة النص نفسه — أوثق من
    # الاعتماد على step وحده (زرّا "📎 إرسال الإيصال من جديد"/"📞 تواصل معنا"
    # بعد رفض الأدمن قد تصلان بجلسة ممسوحة، راجع docstring رأس الملف).
    if event.is_callback:
        cb = event.callback_data
        if cb.startswith("pkg:"):
            await _handle_package_chosen(event, client, customer_id, cb[len("pkg:") :])
            return
        if cb == "pkgwhich":
            products = _fetch_priced_products()
            await client.send_message(
                event.chat_id,
                WHICH_PACKAGE_ADVICE,
                buttons=build_packages_buttons(products) if products else None,
            )
            return
        if cb == "terms:accept":
            await _handle_terms_accepted(event, client, customer_id, data)
            return
        if cb == "amt:edit":
            _save_session(event.chat_id, "await_receipt_amount", data)
            await client.send_message(event.chat_id, "تمام، اكتب المبلغ الصحيح اللي حوّلته (أرقام فقط):")
            return
        if cb == "amt:confirm":
            await _proceed_to_sender_step(event, client, data, amount_mismatch=True)
            return
        if cb.startswith("payretry:"):
            await _handle_payment_retry(event, client, customer_id, cb[len("payretry:") :])
            return
        if cb == "paycontact":
            await client.send_message(
                event.chat_id, f"تواصل معنا عبر واتساب على: {settings_mod.support_whatsapp()}"
            )
            return
        return

    if step == "await_bank_retry":
        await client.send_message(event.chat_id, "لسّة نجهّز بيانات التحويل، صبرك علينا شوي 🙏")
        return

    if step == "await_receipt_file":
        await _handle_receipt_file(event, client, customer_id, data)
        return

    if step == "await_receipt_amount":
        await _handle_receipt_amount(event, client, data)
        return

    if step == "await_amount_confirm":
        await client.send_message(event.chat_id, "اختر من الأزرار بالأسفل 👇")
        return

    if step == "await_receipt_sender":
        await _handle_receipt_sender(event, client, data)
        return

    # await_package/await_terms بلا نص/ملف متوقّع هنا — أعد عرض القائمة المناسبة
    if step == "await_package":
        products = _fetch_priced_products()
        if products:
            await client.send_message(
                event.chat_id, "اختر باقتك من الأزرار بالأسفل 👇", buttons=build_packages_buttons(products)
            )
        return


# -------------------------------------------------------------------
# اختيار الباقة → الشروط (أول مرة) أو بيانات التحويل مباشرة
# -------------------------------------------------------------------


async def _handle_package_chosen(event: ChatEvent, client: TelegramClient, customer_id: int, code: str) -> None:
    product = _fetch_product(code)
    if not product:
        products = _fetch_priced_products()
        await client.send_message(
            event.chat_id,
            "عذرًا 🙏 هذي الباقة غير متاحة حاليًا، اختر من القائمة:",
            buttons=build_packages_buttons(products) if products else None,
        )
        return

    customer = _fetch_customer_basic(customer_id)
    if not customer.get("terms_accepted_at"):
        domain = os.environ.get("MASAR_DOMAIN", "")
        _save_session(event.chat_id, "await_terms", {"product_code": code})
        await client.send_message(
            event.chat_id,
            build_terms_message(domain),
            buttons=[[{"text": "✅ أوافق وأكمل", "callback_data": "terms:accept"}]],
        )
        return

    await _show_bank_and_create_request(event.chat_id, client, customer_id, product)


async def _handle_terms_accepted(event: ChatEvent, client: TelegramClient, customer_id: int, data: dict) -> None:
    code = data.get("product_code", "")
    product = _fetch_product(code)
    if not product:
        products = _fetch_priced_products()
        await client.send_message(
            event.chat_id,
            "عذرًا 🙏 هذي الباقة غير متاحة حاليًا، اختر من القائمة:",
            buttons=build_packages_buttons(products) if products else None,
        )
        return
    _accept_terms(customer_id)
    await _show_bank_and_create_request(event.chat_id, client, customer_id, product)


# -------------------------------------------------------------------
# بيانات التحويل + إنشاء payment_requests
# -------------------------------------------------------------------


async def _show_bank_and_create_request(
    chat_id: int, client: TelegramClient, customer_id: int, product: dict[str, Any]
) -> None:
    # B3-متابعة: قائمة الحسابات النشطة بدل حساب واحد — راجع docstring
    # telegram_admin_settings.active_bank_accounts().
    banks = settings_mod.active_bank_accounts()
    if not banks:
        _save_session(chat_id, "await_bank_retry", {})
        await client.send_message(chat_id, "بنرسل لك بيانات التحويل خلال دقائق 🤍")
        await _notify_admin_urgent(
            f"نحتاجك فورا — عميل #{customer_id} اختار {product['name_ar']} لكن ولا حساب بنكي نشط "
            "بـ⚙️ الإعدادات → 🏦 بيانات التحويل. أضف حسابًا واحدًا على الأقل."
        )
        return

    request_id = _create_payment_request(customer_id, product["code"], float(product["price_sar"]))
    _save_session(
        chat_id,
        "await_receipt_file",
        {"request_id": request_id, "product_code": product["code"], "expected_amount": float(product["price_sar"])},
    )
    await client.send_message(
        chat_id,
        build_bank_message(banks, product["name_ar"], float(product["price_sar"])),
    )


# -------------------------------------------------------------------
# الإيصال → المبلغ → اسم المُحوِّل → إشعار الأدمن
# -------------------------------------------------------------------


def _payment_dir(customer_id: int) -> Path:
    base = Path(os.environ.get("CV_DATA_DIR", "/data/cv")) / "payments" / str(customer_id)
    base.mkdir(parents=True, exist_ok=True)
    return base


async def _handle_receipt_file(event: ChatEvent, client: TelegramClient, customer_id: int, data: dict) -> None:
    request_id = int(data.get("request_id", 0))
    if not request_id:
        await client.send_message(event.chat_id, "عذرًا 🙏 صار خلل، ابدأ من جديد من قائمة الباقات.")
        return

    file_id: str | None = None
    kind: str | None = None
    ext = "bin"
    if event.photo:
        largest = event.photo[-1]
        file_id = largest.get("file_id")
        kind = "photo"
        ext = "jpg"
    elif event.document:
        mime_type = str(event.document.get("mime_type") or "")
        if mime_type not in _ALLOWED_DOCUMENT_MIME:
            await client.send_message(event.chat_id, "أرسل صورة الإيصال أو ملف PDF/JPG/PNG فقط 📎")
            return
        file_id = event.document.get("file_id")
        kind = "document"
        ext = _ALLOWED_DOCUMENT_MIME[mime_type]
    else:
        await client.send_message(event.chat_id, "أرسل صورة الإيصال أو ملفه 📎")
        return

    try:
        file_info = await client.get_file(file_id)
        content = await client.download_file_bytes(file_info["file_path"], max_bytes=RECEIPT_MAX_BYTES)
    except TelegramAPIError:
        logger.warning("فشل تنزيل مرفق إيصال دفع (customer_id=%s)", customer_id, exc_info=True)
        await client.send_message(event.chat_id, "تعذّر استلام الملف، حاول ترسله مرة ثانية 🙏")
        return

    path = _payment_dir(customer_id) / f"{request_id}.{ext}"
    path.write_bytes(content)
    _update_payment_receipt(request_id, str(path), kind)

    _save_session(event.chat_id, "await_receipt_amount", data)
    await client.send_message(event.chat_id, "كم المبلغ اللي حوّلته؟ (أرقام فقط)")


def _parse_amount(text_: str) -> float | None:
    cleaned = "".join(ch for ch in text_.strip() if ch.isdigit() or ch == ".")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


async def _handle_receipt_amount(event: ChatEvent, client: TelegramClient, data: dict) -> None:
    amount = _parse_amount(event.text or "")
    if amount is None or amount <= 0:
        await client.send_message(event.chat_id, "اكتب المبلغ أرقامًا فقط (مثال: 90):")
        return

    expected = float(data.get("expected_amount", 0))
    new_data = {**data, "declared_amount": amount}
    if amount < expected:
        _save_session(event.chat_id, "await_amount_confirm", new_data)
        await client.send_message(
            event.chat_id,
            f"المبلغ المكتوب ({amount:.0f}) أقل من قيمة الباقة ({expected:.0f}) 🙏 تأكد من الرقم:",
            buttons=[
                [
                    {"text": "✏️ أعدّل المبلغ", "callback_data": "amt:edit"},
                    {"text": "✅ المبلغ صحيح كما كتبته", "callback_data": "amt:confirm"},
                ]
            ],
        )
        return

    _save_session(event.chat_id, "await_receipt_sender", new_data)
    await client.send_message(event.chat_id, "باسم من تم التحويل؟")


async def _proceed_to_sender_step(
    event: ChatEvent, client: TelegramClient, data: dict, *, amount_mismatch: bool
) -> None:
    new_data = {**data, "amount_mismatch": amount_mismatch}
    _save_session(event.chat_id, "await_receipt_sender", new_data)
    await client.send_message(event.chat_id, "باسم من تم التحويل؟")


async def _handle_receipt_sender(event: ChatEvent, client: TelegramClient, data: dict) -> None:
    sender_name = (event.text or "").strip()
    if not sender_name:
        await client.send_message(event.chat_id, "اكتب الاسم اللي تم التحويل باسمه:")
        return

    request_id = int(data.get("request_id", 0))
    declared_amount = float(data.get("declared_amount", 0))
    amount_mismatch = bool(data.get("amount_mismatch", False))
    _finalize_payment_request(request_id, declared_amount, sender_name, amount_mismatch=amount_mismatch)

    await _notify_admin_with_receipt(event.chat_id, request_id, sender_name, amount_mismatch)

    _clear_session(event.chat_id)
    await client.send_message(
        event.chat_id,
        "وصلنا إيصالك ✅ بنتأكد من الحوالة ونبلّغك قريبًا بإذن الله.\n\n"
        "بالوقت هذا، خلّنا نجهّز ملفك: أرسل سيرتك الذاتية كملف PDF 📄",
    )


async def _notify_admin_with_receipt(
    customer_chat_id: int, request_id: int, sender_name: str, amount_mismatch: bool
) -> None:
    admin_client = get_admin_bot_client()
    owner_chat_id = os.environ.get("MASAR_OWNER_CHAT_ID", "")
    if admin_client is None or not owner_chat_id:
        return

    pr = _fetch_payment_request(request_id)
    if not pr:
        return
    customer = _fetch_customer_basic(int(pr["customer_id"]))

    caption_lines = [
        f"💳 طلب دفع #{request_id}",
        f"👤 {customer.get('name') or 'بلا اسم'} (#{pr['customer_id']}) — {customer.get('phone') or ''}",
        f"📦 {pr['product_name_ar']} — المتوقع {float(pr['expected_amount']):.0f} ريال",
        f"💰 المكتوب: {float(pr['declared_amount']):.0f} ريال — باسم: {sender_name}",
    ]
    if amount_mismatch:
        caption_lines.append("⚠️ المبلغ أقل من المتوقع (أكّده العميل رغم التنبيه)")
    caption = "\n".join(caption_lines)
    buttons = [
        [
            {"text": "✅ وصلت", "callback_data": f"pay:ok:{request_id}"},
            {"text": "❌ لم تصل", "callback_data": f"pay:no:{request_id}"},
        ]
    ]

    recipients = _active_admin_chat_ids(owner_chat_id)
    receipt_path = Path(str(pr.get("receipt_path") or ""))
    try:
        content = receipt_path.read_bytes() if receipt_path.exists() else b""
    except OSError:
        content = b""

    for chat_id in recipients:
        try:
            if content and pr.get("receipt_kind") == "photo":
                await admin_client.send_photo(chat_id, content, receipt_path.name, caption=caption, buttons=buttons)
            elif content:
                await admin_client.send_document(
                    chat_id, content, receipt_path.name, caption=caption, buttons=buttons
                )
            else:
                await admin_client.send_message(chat_id, caption, buttons=buttons)
        except TelegramAPIError:
            logger.warning("تعذّر إرسال إشعار طلب دفع لمستلم أدمن (chat_id=%s)", chat_id, exc_info=True)


def _active_admin_chat_ids(owner_chat_id: str) -> list[str]:
    chat_ids = [owner_chat_id]
    try:
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                sql_text(
                    "SELECT telegram_chat_id FROM admin_delegates "
                    "WHERE active = true AND telegram_chat_id IS NOT NULL"
                )
            ).all()
        chat_ids.extend(str(r[0]) for r in rows)
    except Exception:  # noqa: BLE001 — أفضل جهد، لا يمنع إشعار المالك
        logger.exception("تعذّر جلب قائمة المفوّضين النشطين لإشعارهم بطلب دفع")
    return chat_ids


async def _notify_admin_urgent(text: str) -> None:
    admin_client = get_admin_bot_client()
    owner_chat_id = os.environ.get("MASAR_OWNER_CHAT_ID", "")
    if admin_client is None or not owner_chat_id:
        return
    for chat_id in _active_admin_chat_ids(owner_chat_id):
        try:
            await admin_client.send_message(chat_id, text)
        except TelegramAPIError:
            logger.warning("تعذّر إرسال تنبيه إداري عاجل (chat_id=%s)", chat_id, exc_info=True)


# -------------------------------------------------------------------
# إعادة إرسال الإيصال بعد رفض الأدمن ("❌ لم تصل")
# -------------------------------------------------------------------


async def _handle_payment_retry(event: ChatEvent, client: TelegramClient, customer_id: int, product_code: str) -> None:
    product = _fetch_product(product_code)
    if not product:
        products = _fetch_priced_products()
        await client.send_message(
            event.chat_id,
            "عذرًا 🙏 هذي الباقة لم تعد متاحة، اختر من القائمة:",
            buttons=build_packages_buttons(products) if products else None,
        )
        return
    await _show_bank_and_create_request(event.chat_id, client, customer_id, product)
