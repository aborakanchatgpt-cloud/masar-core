"""
Masar Core — محادثة بوت العملاء "مسار" على تيليجرام (B8: إزالة n8n نهائيًا
من مسار الدخول الوارد — الدليل: كل تفاعل تيليجرام يُدار داخل Core مباشرة).

يُعيد بناء منطق ورشة عمل n8n القديمة
(`n8n/workflows/job-bot-customer-onboarding-telegram-intake__8TCvk5BsrMPosr9q.json`،
58 عقدة — المرجع الكامل لهذا التصميم) داخل Core مباشرة، بتبسيطات
مقصودة تُوثّق أدناه لأن n8n كان يعتمد أدوات (Data Tables خاصة بـn8n، وكيل
ذكاء اصطناعي Claude لقراءة صور/PDF السيرة الذاتية) لا وجود لمثيل حقيقي لها
بمخطّط Core بعد:

    1. **بلا بوابة "Pending Approvals" (موافقة واتساب يدوية)** — ذلك الجدول
       كان يعيش فقط بـn8n Data Tables ولا مقابل له بمخطّط Core. البديل هنا:
       أحمد (المالك) هو من يُنشئ صفّ العميل أولًا عبر بوت الأدمن (بالاسم
       والجوال فقط، `telegram_chat_id` يبقى NULL) بعد استلام الدفع خارج
       Telegram — تمامًا كما كانت بوابة الموافقة تعمل، لكن بلا جدول وسيط.
       العميل بعدها **يربط** هويّة تيليجرام بصفّه عبر مشاركة رقم جواله
       (زر Telegram الحقيقي request_contact، موثّق من حساب Telegram نفسه —
       نفس مستوى التحقق الذي كان يعتمده تدفّق n8n القديم) — إن طابق الرقم
       صفًّا موجودًا بلا telegram_chat_id، يُربط فورًا؛ إن لم يطابق أي شيء،
       نُخبر العميل بلطف ونُنبّه أحمد (عبر بوت الأدمن) ليتحقق من الاشتراك.
       (B9: منذ دفعة الباقات والدفع، أصبح التسجيل الذاتي أيضًا ممكنًا —
       راجع app/telegram_payments.py — وهذا المسار يبقى للعملاء الذين
       سجّلهم أحمد يدويًا.)
    2. **استخراج ملف شخصي حتمي (rules) لا وكيل ذكاء اصطناعي** — n8n كان
       يستخدم Claude Haiku لقراءة صورة/PDF ويستخرج JSON مهيكل (الاسم/
       المجال/المسميات). هنا: نص PDF يُستخرَج بمكتبة `pypdf` (تبعية خفيفة
       جديدة — أول قدرة استخراج نص PDF بالمستودع، راجع core/requirements.txt)
       ثم يمرّ لنفس مسار الاستخراج الحتمي الموجود أصلًا
       (`app.customers_api.upsert_profile`، لا يُعاد تطبيقه هنا). الصور (JPEG/PNG) **غير مدعومة حاليًا** (بلا OCR) — نطلب من العميل ملف PDF
       فقط؛ هذا قيد مذكور صراحةً بتقرير التسليم كسؤال مفتوح لأحمد.
    3. **بلا "جتني مقابلة! 🎉" (تحضير مقابلة بالذكاء الاصطناعي)** — تلك
       الميزة بـn8n كانت تستدعي وكيل Claude آخر مباشرة؛ لا مقابل لها بـCore
       اليوم وخارج نطاق هذا التسليم (تدفّق داخلي/صادر، لا وارد Telegram).
    4. **لا نسأل عن بريد إلكتروني منفصل** — الحقل `email_service` بجدول
       customers هو صندوق Gmail المخصّص الذي تربطه صفحة `/link/{token}`
       (app.link_api) حصريًا، لا حقل "بريدك الشخصي" النصي الذي كان يُسأل
       بـn8n كخطوة منفصلة بلا استخدام حقيقي بمخطّط Core — أُسقطت الخطوة.

الحالة بين رسائل نفس المحادثة تُخزَّن بجدول `telegram_sessions` جديد
(migrations/versions/0013_telegram_sessions.py) — عمود واحد `step` (أين
نحن بالتدفّق) وعمود `data` (JSONB، أي بيانات مؤقتة قبل أن تُكتب لجداول
customers/profiles الحقيقية عند الإنهاء). **لا نُبقي أي اتصال قاعدة بيانات
مفتوحًا عبر استدعاء شبكي (Telegram/تنزيل ملف)** — كل دالة تفتح اتصالها
الخاص القصير، تُنفّذ عملها، وتُغلقه، ثم تُكمل الشبكة بعده (تفاديًا لاستنزاف
مجمّع اتصالات محدود أثناء انتظار شبكي، الدليل: DB_POOL_SIZE=10+5 بـcore).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text as sql_text

from app import customers_api, link_api
from app import telegram_payments
from app.discovery import classify_family, get_engine
from app.phone import canonical_phone
from app.telegram_client import (
    ChatEvent,
    TelegramAPIError,
    TelegramClient,
    get_admin_bot_client,
    get_customer_bot_client,
)

logger = logging.getLogger("masar.telegram_onboarding")

# -----------------------------------------------------------------------
# ثوابت التدفّق — مناطق السعودية الـــــــــــــ 13 والحدود القصوى منقولة حرفيًا
# من منطق n8n القديم (Onboarding Router node) للحفاظ على نفس تجربة المستخدم.
# -----------------------------------------------------------------------

REGIONS: list[str] = [
    "الرياض", "مكة المكرمة", "المدينة المنورة", "القصيم", "المنطقة الشرقية",
    "عسير", "تبوك", "حائل", "الحدود الشمالية", "جازان", "نجران", "الباحة", "الجوف",
]
FLEX_LABEL = "🌐 مرن - أي مكان بالمملكة"
MAX_CITIES = 5
MAX_FAMILIES = 3
CV_MAX_BYTES = 5 * 1024 * 1024  # 5MB — يطابق app.customers_api.CV_MAX_BYTES عمدًا
_PDF_MAGIC = b"%PDF-"
MIN_CV_TEXT_CHARS = 40  # أقل من هذا = فشل تحليل فعلي (ملف فارغ/تالف/صورة نصّها غير قابل للقراءة)

CONTACT_BUTTON_TEXT = "📱 مشاركة رقم الجوال"


# =========================================================================
# جلسة المحادثة (telegram_sessions) — قراءة/كتابة/مسح، كل استدعاء بمعاملة
# مستقلة قصيرة (لا تُفتح أثناء أي await شبكي).
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
    data = row["data"]
    if isinstance(data, str):  # دفاع إضافي — JSONB يعود list/dict عادةً، لكن بعض السائقين قد تُرجعه نصًا خامًا
        try:
            data = json.loads(data)
        except (ValueError, TypeError):
            data = {}
    return row["step"] or "", dict(data or {})


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
# دوال نقية (بلا قاعدة بيانات/شبكة) — قابلة للاختبار مباشرة، تبني الرسائل
# ولوحات الأزرار من بيانات الجلسة فقط.
# =========================================================================


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return [str(v) for v in parsed] if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []


def normalize_phone(raw: str) -> str:
    """يوحّد رقم الجوال لصيغة 966xxxxxxxxx (B9/A1 — راجع app/phone.py
    لتفاصيل المشكلة والحل؛ كانت هذه الدالة تُبقي الأرقام فقط بلا توحيد صيغة،
    ما كان يمنع مطابقة رقم كتبه أحمد يدويًا برقم أرسلته جهة اتصال Telegram
    الحقيقية)."""
    return canonical_phone(raw)


def build_region_buttons() -> list[list[dict[str, str]]]:
    rows: list[list[dict[str, str]]] = []
    for i in range(0, len(REGIONS), 2):
        pair = REGIONS[i : i + 2]
        rows.append([{"text": r, "callback_data": f"city:{r}"} for r in pair])
    rows.append([{"text": FLEX_LABEL, "callback_data": "city:flex"}])
    rows.append([{"text": "✏️ مدينة/منطقة أخرى (اكتبها)", "callback_data": "city:other"}])
    return rows


def build_cities_confirm_buttons() -> list[list[dict[str, str]]]:
    return [
        [{"text": "✅ القائمة كافية، كمّل", "callback_data": "city:done"}],
        [{"text": "➕ أضف منطقة أخرى", "callback_data": "city:more"}],
    ]


def build_cities_message(cities: list[str]) -> str:
    lines = ["تمام ✅ هذي مدنك/مناطقك المفضّلة حتى الآن:\n"]
    lines.extend(f"{i + 1}. {c}" for i, c in enumerate(cities))
    return "\n".join(lines)


def build_families_buttons(families: list[str]) -> list[list[dict[str, str]]]:
    rows: list[list[dict[str, str]]] = [[{"text": "✅ القائمة كافية، تقدم بها", "callback_data": "families:ok"}]]
    if len(families) < MAX_FAMILIES:
        rows.append([{"text": "➕ أضف مجالًا آخر", "callback_data": "families:add"}])
    for i, fam in enumerate(families):
        rows.append([{"text": f"🗑️ احذف: {fam}", "callback_data": f"families:rm:{i}"}])
    return rows


def build_families_message(families: list[str]) -> str:
    lines = [
        f"بناءً على ما وصفته، هذي مجالاتك المهنية حتى الآن "
        f"(بحد أقصى {MAX_FAMILIES}) اللي راح نقدّم عليها نيابةً عنك:\n"
    ]
    lines.extend(f"{i + 1}. {f}" for i, f in enumerate(families))
    if not families:
        lines.append("(لا يوجد شيء بعد — اكتب أول مجال مهني تعمل به أو تبحث عنه)")
    return "\n".join(lines)


def classify_family_input(existing: list[str], typed: str) -> tuple[str | None, list[str]]:
    """يصنّف نصًا حرًّا (اسم مجال/مسمى كتبه العميل) لعائلة مهنية معروفة
    بمعجم taxonomy_local.yaml عبر app.discovery.classify_family (نفس محرّك
    تصنيف الوظائف — لا منطق مستقل هنا)، ويضيفها للقائمة إن لم تكن موجودة
    ولم نتجاوز الحد الأقصى. يُرجع (اسم العائلة المُطابقة أو None، القائمة
    الجديدة) — دالة نقية بالكامل، قابلة للاختبار بلا قاعدة بيانات."""
    if len(existing) >= MAX_FAMILIES:
        return None, existing
    family = classify_family(typed)
    if not family or family in existing:
        return None, existing
    return family, existing + [family]


# =========================================================================
# مساعدات PDF — استخراج نص السيرة الذاتية (pypdf، لا OCR).
# =========================================================================


def extract_pdf_text(content: bytes) -> str:
    """يستخرج نص PDF نصّي (لا صور مصوّرة/ممسوحة ضوئيًا بلا طبقة نص) عبر
    pypdf. يُرجع نصًا فارغًا (لا استثناء) عند أي فشل قراءة — المستدعي يقرر
    وفق MIN_CV_TEXT_CHARS إن كان هذا "فشل تحليل فعلي" يستدعي طلب ملف أوضح."""
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.warning("مكتبة pypdf غير مثبّتة — تعذّر استخراج نص PDF")
        return ""
    try:
        import io

        reader = PdfReader(io.BytesIO(content))
        parts = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(parts).strip()
    except Exception:  # noqa: BLE001 — ملف PDF تالف/محمي بكلمة مرور/غير قياسي
        logger.warning("تعذّر استخراج نص من ملف PDF مرفوع")
        return ""


def _store_cv_pdf(customer_id: int, content: bytes) -> dict[str, Any]:
    """يخزّن ملف PDF خام بنفس اتفاقية app.customers_api.upload_customer_cv
    (CV_DATA_DIR/<id>/uploaded_cv.pdf) ويُحدّث customers.cv_pdf_*. لا نستدعي
    upload_customer_cv نفسها لأنها تتطلّب fastapi.UploadFile (واجهة HTTP
    متعددة الأجزاء) بينما نملك هنا بايتات خام من تنزيل Telegram مباشرة —
    نفس القيود (الحجم/توقيع PDF) والمسار الناتج مطابقان تمامًا عمدًا."""
    if len(content) > CV_MAX_BYTES:
        raise ValueError(f"حجم الملف يتجاوز {CV_MAX_BYTES // (1024 * 1024)}MB")
    if not content.startswith(_PDF_MAGIC):
        raise ValueError("الملف المرفوع ليس PDF صالحًا")

    sha256_hex = hashlib.sha256(content).hexdigest()
    cv_dir = Path(os.environ.get("CV_DATA_DIR", "/data/cv")) / str(customer_id)
    cv_dir.mkdir(parents=True, exist_ok=True)
    cv_path = cv_dir / "uploaded_cv.pdf"
    cv_path.write_bytes(content)

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            sql_text(
                "UPDATE customers SET cv_pdf_path = :path, cv_pdf_sha256 = :sha, "
                "cv_pdf_uploaded_at = now(), updated_at = now() WHERE id = :id"
            ),
            {"path": str(cv_path), "sha": sha256_hex, "id": customer_id},
        )
    return {"cv_pdf_path": str(cv_path), "cv_pdf_sha256": sha256_hex}


# =========================================================================
# استعلامات عميل قصيرة
# =========================================================================


def _fetch_onboarding_state(customer_id: int) -> dict[str, Any]:
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            sql_text(
                """
                SELECT c.id, c.name, c.phone, c.status, c.cities, c.families, c.cv_pdf_path,
                       p.cv_text
                FROM customers c LEFT JOIN profiles p ON p.customer_id = c.id
                WHERE c.id = :id
                """
            ),
            {"id": customer_id},
        ).mappings().first()
    return dict(row) if row else {}


def _finalize_preferences(customer_id: int, cities: list[str], families: list[str]) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            sql_text(
                "UPDATE customers SET cities = :cities, families = :families, updated_at = now() WHERE id = :id"
            ),
            {
                "cities": json.dumps(cities, ensure_ascii=False),
                "families": json.dumps(families, ensure_ascii=False),
                "id": customer_id,
            },
        )


def _self_register_customer(chat_id: int, phone_digits: str) -> int:
    """B9/B3: تسجيل ذاتي — لا يطابق `_link_telegram_by_phone` أي صفّ موجود
    (لم يسجّله أحمد يدويًا مسبقًا) فننشئ صفًّا جديدًا فورًا بلا أي تدخّل من
    أحمد (قرار المنتج 11 سبتمبر: "تسجيل ذاتي"). الاسم يبقى فارغًا هنا —
    يُلتقَط بالخطوة التالية مباشرة (راجع `_handle_unlinked`) ثم يُحدّث عبر
    `_update_customer_name` أدناه. `customers.status` الافتراضي `pending`
    (0017) يُطبّق تلقائيًا بلا تمريره صراحةً هنا."""
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            sql_text(
                "INSERT INTO customers (telegram_chat_id, phone, name, target_daily) "
                "VALUES (:cid, :phone, '', 17) RETURNING id"
            ),
            {"cid": chat_id, "phone": phone_digits},
        ).first()
        customer_id = row[0]
        conn.execute(
            sql_text("INSERT INTO wallets (customer_id, balance) VALUES (:id, 0) ON CONFLICT DO NOTHING"),
            {"id": customer_id},
        )
    return customer_id


def _update_customer_name(customer_id: int, name: str) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            sql_text("UPDATE customers SET name = :name, updated_at = now() WHERE id = :id"),
            {"name": name, "id": customer_id},
        )


def _link_telegram_by_phone(chat_id: int, phone_digits: str) -> int | None:
    """يربط chat_id بصفّ عميل موجود مسبقًا (أنشأه أحمد عبر بوت الأدمن)
    برقم الجوال — فقط إن لم يكن ذلك الصفّ مربوطًا بمحادثة تيليجرام أخرى
    مسبقًا (فهرس فريد جزئي uq_customers_telegram_chat_id_not_null، 0010،
    يمنع أي ربط مزدوج على مستوى القاعدة كشبكة أمان إضافية)."""
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            sql_text(
                """
                UPDATE customers SET telegram_chat_id = :cid, updated_at = now()
                WHERE phone = :phone AND telegram_chat_id IS NULL
                RETURNING id
                """
            ),
            {"cid": chat_id, "phone": phone_digits},
        ).first()
    return row[0] if row else None


async def _notify_admin(text: str) -> None:
    """إشعار أحمد عبر بوت الأدمن — أفضل جهد بالكامل (best-effort): لا يرفع
    أي استثناء أبدًا (فشل إشعار إداري يجب ألا يُسقط محادثة عميل حيّة)."""
    owner_chat_id = os.environ.get("MASAR_OWNER_CHAT_ID", "")
    admin_client = get_admin_bot_client()
    if not owner_chat_id or admin_client is None:
        return
    try:
        await admin_client.send_message(owner_chat_id, text)
    except TelegramAPIError:
        logger.warning("تعذّر إرسال إشعار إداري لأحمد", exc_info=True)


# =========================================================================
# المعالج الرئيسي
# =========================================================================


async def handle_update(update: dict[str, Any], event: ChatEvent, client: TelegramClient) -> None:
    """نقطة الدخول التي يستدعيها app.telegram_api لكل تحديث موجّه لبوت
    العملاء (chat_id != MASAR_OWNER_CHAT_ID). `event` مُستخرَج مسبقًا
    بالمستدعي (extract_chat_event) و`client` هو عميل بوت العملاء الجاهز —
    كلاهما يُمرّر بدل إعادة استخراجهما هنا تفاديًا لازدواج المنطق مع
    app.telegram_admin الذي يمرّ بنفس نقطة الدخول بالموجّه المشترك."""
    try:
        lookup = await customers_api.get_customer_by_telegram(event.chat_id)
        customer_id: int | None = lookup["customer_id"]
        customer_status: str | None = lookup["status"]
    except HTTPException:
        customer_id = None
        customer_status = None

    if customer_id is None:
        await _handle_unlinked(event, client)
        return

    if customer_status in ("paused", "expired"):
        await _handle_inactive_customer(event, client, customer_id, customer_status)
        return

    state = _fetch_onboarding_state(customer_id)
    onboarding_done = bool(state.get("cv_pdf_path")) and bool(_as_list(state.get("cities"))) and bool(
        _as_list(state.get("families"))
    )
    if onboarding_done:
        await _handle_active_menu(event, client, customer_id, state)
    else:
        await _handle_onboarding_step(event, client, customer_id, state)


# -------------------------------------------------------------------
# عميل غير مربوط بعد (لا يوجد customer_id لهذه المحادثة)
# -------------------------------------------------------------------


async def _handle_unlinked(event: ChatEvent, client: TelegramClient) -> None:
    if event.is_callback:
        return  # لا أزرار متوقّعة قبل الربط

    if event.contact and event.contact.get("phone_number") and event.from_user_id == event.chat_id:
        phone_digits = normalize_phone(str(event.contact["phone_number"]))
        linked_customer_id = _link_telegram_by_phone(event.chat_id, phone_digits)
        if linked_customer_id:
            state = _fetch_onboarding_state(linked_customer_id)
            name = state.get("name") or ""
            await client.send_message(
                event.chat_id,
                f"أهلًا وسهلًا {name} 👋 تم التحقق من رقمك وربط حسابك بنجاح.".strip(),
            )
            if state.get("cv_pdf_path") and _as_list(state.get("cities")) and _as_list(state.get("families")):
                await _send_active_menu(client, event.chat_id, "تم كل شي مسبقًا ✅")
            else:
                _save_session(event.chat_id, "awaiting_cv", {})
                await client.send_message(
                    event.chat_id,
                    "الآن أرسل لي سيرتك الذاتية كملف PDF لنبدأ العمل عليها 📄",
                )
        else:
            # B9/B3: لا صفّ موجود بهذا الرقم — تسجيل ذاتي فورًا (قرار
            # المنتج 11 سبتمبر)، لا رسالة دخول مسدود كما كان سابقًا.
            new_customer_id = _self_register_customer(event.chat_id, phone_digits)
            _save_session(event.chat_id, "await_name", {})
            await client.send_message(
                event.chat_id,
                "أهلًا وسهلًا 👋 يبدو هذي أول مرة تتواصل معنا فيها بهذا الرقم — تمام، نبدأ تسجيلك الآن.\n\n"
                "وش اسمك الكريم؟",
            )
            await _notify_admin(f"🆕 عميل جديد بدأ التسجيل الذاتي: #{new_customer_id} — {phone_digits}")
        return

    # أول تواصل (أو أعاد الكتابة بدل الضغط على الزر) — نُرسل الترحيب الدافئ
    # ونطلب مشاركة الرقم (نفس نص n8n الأصلي، بلا تعديل — النبرة مقصودة).
    await client.send_contact_request(
        event.chat_id,
        "🌱 \"وَأَن لَّيْسَ لِلْإِنسَانِ إِلَّا مَا سَعَى\"\n\n"
        "كل خطوة تخطوها بحثًا عن رزقك هي خطوة مباركة. امنح نفسك اليوم فرصة "
        "المحاولة، فالسعي عبادة والرزق بيد الله.\n\n"
        "أهلاً وسهلاً 👋 أنا مسار، وجهتنا معك واحدة: نبحث ونقدّم نيابةً عنك "
        "بكل صدق واهتمام حتى تصل لوظيفة تليق فيك بإذن الله.\n\n"
        "اضغط الزر بالأسفل لمشاركة رقم جوالك ونتحقق من اشتراكك:",
        CONTACT_BUTTON_TEXT,
    )


async def _handle_inactive_customer(
    event: ChatEvent, client: TelegramClient, customer_id: int, status: str
) -> None:
    if event.is_callback:
        return
    label = "مُوقَف مؤقتًا" if status == "paused" else "منتهي"
    await client.send_message(
        event.chat_id,
        f"اشتراكك حاليًا {label} 🙏\n\n"
        "تواصل معنا عبر واتساب على: +966544161255 وبنساعدك تكمل معنا بأسرع وقت.",
    )


# -------------------------------------------------------------------
# خطوات onboarding (CV → مدن → مجالات → إنهاء)
# -------------------------------------------------------------------


async def _handle_onboarding_step(
    event: ChatEvent, client: TelegramClient, customer_id: int, state: dict[str, Any]
) -> None:
    step, data = _get_session(event.chat_id)

    # B9/B3: زرّا "📎 إرسال الإيصال من جديد"/"📞 تواصل معنا" (يظهران للعميل
    # بعد رفض الأدمن لطلب دفع سابق) قد تصل بجلسة ممسوحة (لا step نشط) —
    # يُتحقّق منهما ببادئة callback_data مباشرة بصرف النظر عن step، راجع
    # docstring app.telegram_payments.
    if event.is_callback and (event.callback_data.startswith("payretry:") or event.callback_data == "paycontact"):
        await telegram_payments.handle_step(event, client, customer_id, step, data)
        return

    if step == "await_name":
        if event.is_callback or not event.text:
            await client.send_message(event.chat_id, "وش اسمك الكريم؟ اكتبه هنا:")
            return
        name = event.text.strip()[:255]
        _update_customer_name(customer_id, name)
        await telegram_payments.start(client, event.chat_id, customer_id)
        return

    if step in telegram_payments.PAYMENT_FLOW_STEPS:
        await telegram_payments.handle_step(event, client, customer_id, step, data)
        return

    if not state.get("cv_pdf_path"):
        await _step_cv(event, client, customer_id, state, step, data)
        return

    cities = _as_list(data.get("cities")) or _as_list(state.get("cities"))
    if not cities or step in ("ask_cities", "awaiting_city_text"):
        await _step_cities(event, client, customer_id, step, data, cities)
        return

    families = _as_list(data.get("families")) or _as_list(state.get("families"))
    await _step_families(event, client, customer_id, step, data, families)


async def _step_cv(
    event: ChatEvent, client: TelegramClient, customer_id: int, state: dict[str, Any], step: str, data: dict
) -> None:
    if event.is_callback:
        return

    if event.document:
        mime_type = str(event.document.get("mime_type") or "")
        if mime_type != "application/pdf":
            await client.send_message(
                event.chat_id, "الصيغة غير مدعومة حاليًا 🙏 أرسل سيرتك الذاتية كملف PDF."
            )
            return
        try:
            file_info = await client.get_file(event.document["file_id"])
            # B9/A6: نمرّر حد التنزيل الفعلي (CV_MAX_BYTES=5MB) لا الافتراضي
            # (8MB) حتى تكون رسالة الخطأ صحيحة ("يتجاوز 5MB") بدل رسالة تنزيل خطأ
            # عامة مضلّلة لملف بين 5 ور8 ميجابايت.
            content = await client.download_file_bytes(file_info["file_path"], max_bytes=CV_MAX_BYTES)
        except TelegramAPIError:
            logger.warning("فشل تنزيل مرفق CV من Telegram", exc_info=True)
            await client.send_message(event.chat_id, "تعذّر تنزيل الملف، حاول ترسله مرة ثانية 🙏")
            return

        try:
            _store_cv_pdf(customer_id, content)
        except ValueError as exc:
            await client.send_message(event.chat_id, f"تعذّر قبول الملف: {exc}")
            return

        cv_text = extract_pdf_text(content)
        if len(cv_text) < MIN_CV_TEXT_CHARS:
            await client.send_message(
                event.chat_id,
                "عذرًا 🙏 ما قدرت أقرأ محتوى واضحًا من هذا الملف (قد يكون صورة ممسوحة ضوئيًا "
                "بلا طبقة نص، أو ملفًا فارغًا/تالفًا).\n\n"
                "حاول ترسل ملف PDF نصّيًا (وليس صورة مُصدرة كـPDF):",
            )
            await _notify_admin(
                f"⚠️ فشل استخراج نص من سيرة عميل #{customer_id} — تحقق يدويًا إن تكرّر."
            )
            return

        await customers_api.upsert_profile(customer_id, customers_api.ProfileUpsertRequest(cv_text=cv_text))
        _save_session(event.chat_id, "ask_cities", {})
        await client.send_message(
            event.chat_id,
            "تم تحليل سيرتك بنجاح ✅\n\nالآن اختر منطقتك المفضّلة للعمل بالمملكة "
            "(تقدر تختار أكثر من منطقة):",
            buttons=build_region_buttons(),
        )
        return

    if event.photo:
        await client.send_message(
            event.chat_id,
            "حاليًا نحتاج ملف PDF لسيرتك الذاتية (لا صورة) حتى نقرأه بدقّة 🙏\n\n"
            "صدّرها كـPDF من الجوال/الحاسب وأرسلها هنا:",
        )
        return

    await client.send_message(event.chat_id, "لسّة وصلت! 📄 أرسل سيرتك الذاتية كملف PDF عشان نبدأ:")


async def _step_cities(
    event: ChatEvent, client: TelegramClient, customer_id: int, step: str, data: dict, cities: list[str]
) -> None:
    if event.is_callback and event.callback_data.startswith("city:"):
        action = event.callback_data[len("city:") :]
        if action == "flex":
            cities = [FLEX_LABEL]
            _save_session(event.chat_id, "ask_families", {**data, "cities": cities})
            await client.send_message(
                event.chat_id,
                "تمام ✅ اخترت المرونة الكاملة.\n\n" + build_families_message([]),
            )
            return
        if action == "other":
            _save_session(event.chat_id, "awaiting_city_text", data)
            await client.send_message(event.chat_id, "تمام، اكتب اسم مدينتك أو منطقتك:")
            return
        if action == "more":
            await client.send_message(
                event.chat_id, "اختر منطقة إضافية:", buttons=build_region_buttons()
            )
            return
        if action == "done":
            if not cities:
                await client.send_message(
                    event.chat_id, "اختر منطقة واحدة على الأقل قبل المتابعة 🙏", buttons=build_region_buttons()
                )
                return
            _save_session(event.chat_id, "ask_families", {**data, "cities": cities})
            await client.send_message(event.chat_id, build_families_message([]))
            return
        if action in REGIONS:
            if action not in cities and len(cities) < MAX_CITIES:
                cities = cities + [action]
            _save_session(event.chat_id, "ask_cities", {**data, "cities": cities})
            await client.send_message(
                event.chat_id, build_cities_message(cities), buttons=build_cities_confirm_buttons()
            )
            return
        return

    if step == "awaiting_city_text" and not event.is_callback and event.text:
        if event.text not in cities and len(cities) < MAX_CITIES:
            cities = cities + [event.text]
        _save_session(event.chat_id, "ask_cities", {**data, "cities": cities})
        await client.send_message(
            event.chat_id, build_cities_message(cities), buttons=build_cities_confirm_buttons()
        )
        return

    # رسالة نصية عادية بمنتصف اختيار المدن — نُعيد عرض الاختيار الحالي
    await client.send_message(
        event.chat_id,
        "اختر من الأزرار بالأسفل 👇\n\n" + (build_cities_message(cities) if cities else "اختر منطقتك المفضّلة:"),
        buttons=build_region_buttons() if not cities else build_cities_confirm_buttons(),
    )


async def _step_families(
    event: ChatEvent, client: TelegramClient, customer_id: int, step: str, data: dict, families: list[str]
) -> None:
    cities = _as_list(data.get("cities"))

    if event.is_callback and event.callback_data.startswith("families:"):
        action = event.callback_data[len("families:") :]
        if action == "ok":
            if not families:
                await client.send_message(
                    event.chat_id, "اكتب مجالًا مهنيًا واحدًا على الأقل قبل المتابعة 🙏"
                )
                return
            await _finalize_onboarding(event, client, customer_id, cities, families)
            return
        if action == "add":
            await client.send_message(event.chat_id, "تمام، اكتب المجال المهني الإضافي:")
            return
        if action.startswith("rm:"):
            try:
                idx = int(action[len("rm:") :])
            except ValueError:
                idx = -1
            if 0 <= idx < len(families):
                families = families[:idx] + families[idx + 1 :]
            _save_session(event.chat_id, "ask_families", {**data, "families": families})
            await client.send_message(
                event.chat_id, build_families_message(families), buttons=build_families_buttons(families)
            )
            return
        return

    if not event.is_callback and event.text:
        matched, new_families = classify_family_input(families, event.text)
        _save_session(event.chat_id, "ask_families", {**data, "families": new_families})
        if matched:
            await client.send_message(
                event.chat_id,
                build_families_message(new_families),
                buttons=build_families_buttons(new_families),
            )
        else:
            hint = (
                f"وصلت للحد الأقصى ({MAX_FAMILIES} مجالات) 🙏\n\n"
                if len(families) >= MAX_FAMILIES
                else "ما قدرت أحدد مجالًا مهنيًا واضحًا من كلامك 🙏 حاول تكتبه بشكل أوضح "
                "(مثال: محاسبة، هندسة مدنية، تسويق رقمي):\n\n"
            )
            await client.send_message(
                event.chat_id, hint + build_families_message(families), buttons=build_families_buttons(families)
            )
        return

    await client.send_message(
        event.chat_id, build_families_message(families), buttons=build_families_buttons(families)
    )


async def _finalize_onboarding(
    event: ChatEvent, client: TelegramClient, customer_id: int, cities: list[str], families: list[str]
) -> None:
    _finalize_preferences(customer_id, cities, families)
    _clear_session(event.chat_id)

    link_url = None
    try:
        token_result = await link_api.create_link_token(customer_id)
        domain = os.environ.get("MASAR_DOMAIN", "")
        if domain:
            link_url = f"https://{domain}{token_result['path']}"
    except Exception:  # noqa: BLE001 — فشل توليد الرابط لا يجب أن يُسقط رسالة النجاح نفسها
        logger.warning("تعذّر توليد رابط ربط البريد للعميل #%s", customer_id, exc_info=True)

    closing = (
        "🎉 تم كل شي بنجاح! سيرتك جاهزة، ومدنك ومجالاتك محفوظة، وراح نبدأ نبحث ونقدم لك "
        "على الوظائف المناسبة نيابةً عنك بإذن الله.\n\n"
    )
    if link_url:
        closing += (
            "خطوة أخيرة مهمة: افتح هذا الرابط لربط بريد إلكتروني خاص بالخدمة (تُنشئه بنفسك، "
            f"يستغرق دقيقتين) حتى نقدر نبدأ الإرسال باسمك:\n{link_url}\n\n"
        )
    closing += "الله يوفقك ويرزقك وظيفة تناسبك قريبًا 🤍"

    await client.send_message(event.chat_id, closing)
    await _notify_admin(f"✅ عميل #{customer_id} أنهى التسجيل عبر بوت مسار.")


# -------------------------------------------------------------------
# قائمة العميل النشط (onboarding مكتمل)
# -------------------------------------------------------------------


async def _send_active_menu(client: TelegramClient, chat_id: int, text: str) -> None:
    await client.send_message(
        chat_id,
        text,
        buttons=[
            [{"text": "📋 حالتي", "callback_data": "menu:status"}],
            [{"text": "📞 تواصل معنا", "callback_data": "menu:contact"}],
        ],
    )


async def _handle_active_menu(
    event: ChatEvent, client: TelegramClient, customer_id: int, state: dict[str, Any]
) -> None:
    step, data = _get_session(event.chat_id)

    if step == "awaiting_contact_message" and not event.is_callback and event.text:
        name = state.get("name") or "غير معروف"
        phone = state.get("phone") or "غير متوفر"
        await _notify_admin(
            f"📨 رسالة من عميل #{customer_id}\n👤 {name}\n📱 {phone}\n\n💬 {event.text}"
        )
        _clear_session(event.chat_id)
        await client.send_message(
            event.chat_id,
            "تم استلام رسالتك ✅ وصلت لفريق مسار، وبنرد عليك أو نتواصل معك قريبًا بإذن الله 🤍",
        )
        return

    if event.is_callback and event.callback_data == "menu:status":
        await client.send_message(event.chat_id, _build_status_text(state))
        return

    if event.is_callback and event.callback_data == "menu:contact":
        _save_session(event.chat_id, "awaiting_contact_message", {})
        await client.send_message(event.chat_id, "اكتب رسالتك وبنوصّلها لفريق مسار مباشرة:")
        return

    await _send_active_menu(
        client, event.chat_id, "أهلًا بك من جديد 🤍 اختر من القائمة:"
    )


def _build_status_text(state: dict[str, Any]) -> str:
    status = state.get("status") or "active"
    if status == "active":
        return "📋 حالة اشتراكك\n\n✅ اشتراكك فعّال حاليًا، الله يوفقك.\n\nإذا احتجت أي شي اضغط \"📞 تواصل معنا\"."
    return f"📋 حالة اشتراكك\n\nحالتك الحالية: {status}.\n\nإذا احتجت أي شي اضغط \"📞 تواصل معنا\"."
