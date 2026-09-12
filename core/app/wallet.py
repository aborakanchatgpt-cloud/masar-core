"""
Masar Core — B10: نموذج المحفظة/الدفع حسب الاستخدام (رصيد ريالي، لا اشتراك).
راجع migrations/versions/0021_b10_wallet.py لتفصيل المخطّط الكامل والقرارات.

باقة **إضافية** (لا تستبدل الاشتراك/الرصيد الحاليين): العميل يدفع فقط على
التقديمات الفعلية الناجحة — سعر التقديم الواحد يُقرأ من `app_settings`
(مفتاح `wallet_rate_per_application_sar`، نفس نمط `support_whatsapp()`
بـ`telegram_admin_settings.py`) لا مكتوبًا بالكود، حتى يقدر أحمد تعديله
لاحقًا بلا نشر كود جديد. الرصيد بالريال (Decimal) هو مصدر الحقيقة الوحيد —
لا عمود "تقديمات متبقية" منفصل يُخزَّن أبدًا؛ عدد التقديمات المتاح يُحسَب
ديناميكيًا `floor(balance / rate)` عند الحاجة (`applications_affordable`).

هذا الملف هو نقطة الحقيقة الوحيدة لكل حساب/كتابة متعلقة بالمحفظة —
يستورده كل من:
    - core/app/send_builder.py  (سقف يومي للعميل billing_mode='wallet')
    - core/app/sender.py        (الخصم الفعلي عند نجاح الإرسال + إشعار
                                  انخفاض الرصيد)
    - core/app/catalog.py       (تفعيل طلب شحن محفظة بعد تأكيد الأدمن)
    - core/app/telegram_payments.py (عرض السعر للعميل + حساب مبلغ الشحن)
    - core/app/telegram_admin_commands.py (تعديل رصيد يدوي من الأدمن)

**ذرّية الكتابة**: `apply_wallet_delta_conn`/`apply_wallet_delta` يحدّثان
`customers.wallet_balance_sar` ويُدرجان صفّ تدقيق `wallet_transactions`
بنفس المعاملة دومًا (قفل صف `FOR UPDATE` أولًا) — نفس نمط
`customers_api.wallet_credit`/`catalog._grant_wallet_credits` تمامًا، لكن
على الجدولين الجديدين لا `wallets`/`ledger` القديمين (نظامان منفصلان
تمامًا عمدًا — لا تداخل: عميل `billing_mode='wallet'` لا يلمس
`wallets`/`ledger` إطلاقًا، وعميل `billing_mode='subscription'` (الافتراضي)
لا يلمس `wallet_transactions`/`wallet_balance_sar` إطلاقًا).
"""
from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from app.discovery import get_engine

RATE_SETTING_KEY = "wallet_rate_per_application_sar"

# قيمة احتياطية فقط لو غاب الصف المبذور بترحيلة 0021 (نفس فلسفة
# _DEFAULT_SUPPORT_WHATSAPP بـtelegram_admin_settings.py) — السعر الفعلي
# المعتمد دومًا هو ما يقرأه أحمد/يعدّله بـapp_settings.
DEFAULT_RATE_SAR = Decimal("0.235")

WALLET_PRODUCT_CODE = "WALLET"
# P0.6 (ترحيلة 0022): 'bounce_refund' جديد — استرداد تلقائي لخصم تقديم
# ارتدّ (bounced) لعميل billing_mode='wallet'، راجع refund_bounce_conn أدناه.
WALLET_TRANSACTION_TYPES = {"topup", "consumption", "admin_adjustment", "bounce_refund"}


def _parse_rate(value: str | None) -> Decimal:
    if not value:
        return DEFAULT_RATE_SAR
    try:
        rate = Decimal(value)
    except Exception:  # noqa: BLE001 — قيمة تالفة بـapp_settings، fail-safe للافتراضي
        return DEFAULT_RATE_SAR
    return rate if rate > 0 else DEFAULT_RATE_SAR


def per_application_rate_conn(conn: Connection) -> Decimal:
    """يقرأ سعر التقديم الواحد بالريال من `app_settings` ضمن اتصال/معاملة
    **مفتوحة أصلًا** لدى المستدعي (send_builder.py/sender.py/catalog.py —
    كلها تستدعيها من داخل `engine.begin()`/`engine.connect()` قائمة، فلا
    داعي لفتح اتصال جديد)."""
    value = conn.execute(
        text("SELECT value FROM app_settings WHERE key = :k"), {"k": RATE_SETTING_KEY}
    ).scalar()
    return _parse_rate(value)


def per_application_rate(engine: Engine | None = None) -> Decimal:
    """نقطة القراءة المستقلة (تفتح اتصالها الخاص) — لطبقة تيليفرام
    (telegram_payments.py) التي لا معاملة مفتوحة لديها وقت العرض للعميل."""
    engine = engine or get_engine()
    with engine.connect() as conn:
        return per_application_rate_conn(conn)


def applications_affordable(balance: Decimal, rate: Decimal) -> int:
    """كم تقديمًا يغطّي هذا الرصيد؟ floor(balance / rate) — لا تقريب لأعلى
    أبدًا (قرار أحمد: لا يُسمح للعميل بتقديم لا يملك رصيدًا كافيًا له)."""
    if rate is None or rate <= 0 or balance is None or balance <= 0:
        return 0
    return int((balance / rate).to_integral_value(rounding=ROUND_FLOOR))


def amount_for_count(count: int, rate: Decimal) -> Decimal:
    """يحوّل عدد تقديمات مطلوب (المسار الأول: "أبي N تقديم") لمبلغ ريال
    فعلي يُطلب من العميل تحويله بنكيًا — يُقرّب لأقرب هللة (0.01) بخلاف
    رصيد المحفظة الداخلي (الذي يبقى بدقّة كاملة بلا تقريب): هذا مبلغ حقيقي
    يُكتب على إيصال تحويل بنكي، والبنوك لا تتعامل بكسور الهللة."""
    if count is None or count <= 0:
        return Decimal("0.00")
    raw = Decimal(count) * rate
    return raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def apply_wallet_delta_conn(
    conn: Connection,
    customer_id: int,
    amount: Decimal,
    type_: str,
    *,
    reason: str | None = None,
    created_by: str | None = None,
    application_id: int | None = None,
) -> Decimal:
    """يحدّث `customers.wallet_balance_sar` ويُدرج صفّ `wallet_transactions`
    **بنفس المعاملة المفتوحة أصلًا** لدى المستدعي — قفل صف `FOR UPDATE`
    أولًا (نفس نمط `customers_api.wallet_credit`)، فيُدرأ سباق بين خصمين
    متزامنين (مثال: خصم استهلاك من sender.py وتعديل يدوي من الأدمن بنفس
    اللحظة تقريبًا). `amount` موجب للإضافة (شحن/تعديل ائتمان/استرداد ارتداد)
    أو سالب للخصم (استهلاك/تعديل مدين). `application_id` (P0.6، ترحيلة
    0022) يُسجّل فقط لحركتي 'consumption'/'bounce_refund' — يربط الحركة
    بتطبيق مُرسَل محدّد حتى يستطيع `refund_bounce_conn` إيجاد الخصم الأصلي
    عند ارتداد لاحق. يرجع الرصيد الجديد.

    P0.6: **فشل مغلق على رصيد سالب** — أي خصم يُنزل الرصيد تحت الصفر يُرفَض
    بـ`ValueError("insufficient_wallet_balance")` قبل أي كتابة (يطابق قيد
    قاعدة البيانات `ck_customers_wallet_balance_nonneg` بترحيلة 0022 —
    دفاع بالعمق على مستوى التطبيق أيضًا، لا اعتمادًا على القيد وحده)."""
    if type_ not in WALLET_TRANSACTION_TYPES:
        raise ValueError(f"نوع حركة محفظة غير معروف: {type_}")

    row = conn.execute(
        text("SELECT wallet_balance_sar FROM customers WHERE id = :id FOR UPDATE"),
        {"id": customer_id},
    ).first()
    if row is None:
        raise ValueError(f"عميل غير موجود: {customer_id}")

    new_balance = row[0] + amount
    if new_balance < 0:
        raise ValueError("insufficient_wallet_balance")
    conn.execute(
        text("UPDATE customers SET wallet_balance_sar = :b, updated_at = now() WHERE id = :id"),
        {"b": new_balance, "id": customer_id},
    )
    conn.execute(
        text(
            """
            INSERT INTO wallet_transactions
                (customer_id, amount, type, reason, application_id, created_at, created_by)
            VALUES (:cid, :amount, :type, :reason, :application_id, now(), :created_by)
            """
        ),
        {
            "cid": customer_id,
            "amount": amount,
            "type": type_,
            "reason": reason,
            "application_id": application_id,
            "created_by": created_by,
        },
    )
    return new_balance


def apply_wallet_delta(
    engine: Engine,
    customer_id: int,
    amount: Decimal,
    type_: str,
    *,
    reason: str | None = None,
    created_by: str | None = None,
) -> Decimal:
    """نسخة مستقلة (تفتح معاملتها الخاصة) — لأي مستدعٍ بلا معاملة مفتوحة
    أصلًا (تعديل رصيد يدوي من أمر تيليجرام الأدمن)."""
    with engine.begin() as conn:
        return apply_wallet_delta_conn(conn, customer_id, amount, type_, reason=reason, created_by=created_by)


def get_wallet_balance(customer_id: int, engine: Engine | None = None) -> Decimal:
    engine = engine or get_engine()
    with engine.connect() as conn:
        value = conn.execute(
            text("SELECT wallet_balance_sar FROM customers WHERE id = :id"), {"id": customer_id}
        ).scalar()
    return value if value is not None else Decimal("0")


def consume_application_conn(conn: Connection, customer_id: int, application_id: int | None = None) -> dict:
    """B10 — نقطة الخصم الوحيدة، عند الإرسال الفعلي الناجح **فقط** (تُستدعى
    من `sender._mark_success` بعد نجاح SMTP فعليًا مباشرة — نفس النقطة
    الحرفية التي تُدرج صفّ `applications` — لا مكان آخر بالكود يخصم من
    `wallet_balance_sar` إطلاقًا). المستدعي مسؤول عن التحقّق من
    `billing_mode == 'wallet'` قبل الاستدعاء (بوابة صريحة، لا تخمين هنا).

    خلاف نظام `wallets`/`ledger` القديم (خصم وقت بناء الطابور + استرداد
    عند فشل نهائي): هنا لا خصم إطلاقًا إلا بعد تأكيد الإرسال فعليًا. لكن
    **الارتداد لاحقًا** (البريد يرتدّ بعد قبول SMTP الأولي — يُكتشَف لاحقًا
    عبر inbox.py) يستدعي استردادًا تلقائيًا (P0.6، راجع `refund_bounce_conn`
    أدناه) — لذا نُمرّر `application_id` هنا (إن وُجد) ليكون قابلًا للربط
    عند ذلك الاسترداد لاحقًا."""
    rate = per_application_rate_conn(conn)
    new_balance = apply_wallet_delta_conn(conn, customer_id, -rate, "consumption", application_id=application_id)
    return {"new_balance": new_balance, "rate": rate, "low_balance": new_balance < rate}


def refund_bounce_conn(conn: Connection, application_id: int) -> dict | None:
    """P0.6: يُستدعى من `inbox.py` عند تعليم تطبيق عميل `billing_mode='wallet'`
    كـ`bounced` — يبحث عن صفّ الخصم الأصلي (`consumption`) المرتبط بهذا
    `application_id` ويُعيد **نفس المبلغ** لمحفظة العميل كحركة `bounce_refund`.

    **Idempotent**: الفهرس الفريد الجزئي `(application_id) WHERE
    type='bounce_refund'` (ترحيلة 0022) يمنع استردادًا مضاعفًا لنفس
    application_id (مثال: إعادة معالجة نفس بريد ارتداد بالخطأ) — يُستخدَم
    savepoint (`begin_nested`) لالتقاط `IntegrityError` بأمان بلا إفساد أي
    معاملة أكبر مفتوحة لدى المستدعي، ويُتجاهل الاسترداد المكرر بصمت (يرجع
    None، لا استثناء). يرجع None أيضًا إن لم يوجد خصم أصلي مسجّل لهذا
    `application_id` (عميل غير محفظة، أو تطبيق سابق لإضافة هذا العمود)."""
    consumption = conn.execute(
        text(
            "SELECT customer_id, amount FROM wallet_transactions "
            "WHERE application_id = :aid AND type = 'consumption' LIMIT 1"
        ),
        {"aid": application_id},
    ).mappings().first()
    if consumption is None:
        return None

    refund_amount = -consumption["amount"]  # amount الأصلي سالب (خصم) → الاسترداد موجب
    savepoint = conn.begin_nested()
    try:
        new_balance = apply_wallet_delta_conn(
            conn,
            consumption["customer_id"],
            refund_amount,
            "bounce_refund",
            reason="ارتداد رسالة — استرداد تلقائي",
            application_id=application_id,
        )
    except IntegrityError:
        savepoint.rollback()
        return None
    savepoint.commit()
    return {"customer_id": consumption["customer_id"], "amount": refund_amount, "new_balance": new_balance}
