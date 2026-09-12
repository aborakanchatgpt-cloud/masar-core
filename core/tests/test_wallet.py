"""اختبارات نقية (بلا قاعدة بيانات) لـcore/app/wallet.py — B10: نموذج
المحفظة/الدفع حسب الاستخدام. يغطي فقط الدوال الحسابية الصرفة
(applications_affordable/amount_for_count/_parse_rate) — الكتابة الفعلية
(apply_wallet_delta*/consume_application_conn) تحتاج قاعدة بيانات حقيقية
وتُختبر بـtest_wallet_db.py (يُتخطّى تلقائيًا بلا Postgres محلي)."""
from __future__ import annotations

from decimal import Decimal

from app import wallet


# ---------------------------------------------------------------------------
# per_application_rate / _parse_rate
# ---------------------------------------------------------------------------


def test_parse_rate_default_when_missing():
    assert wallet._parse_rate(None) == wallet.DEFAULT_RATE_SAR
    assert wallet._parse_rate("") == wallet.DEFAULT_RATE_SAR


def test_parse_rate_valid_value():
    assert wallet._parse_rate("0.5") == Decimal("0.5")
    assert wallet._parse_rate("1") == Decimal("1")


def test_parse_rate_falls_back_on_garbage_or_non_positive():
    assert wallet._parse_rate("not-a-number") == wallet.DEFAULT_RATE_SAR
    assert wallet._parse_rate("0") == wallet.DEFAULT_RATE_SAR
    assert wallet._parse_rate("-1") == wallet.DEFAULT_RATE_SAR


# ---------------------------------------------------------------------------
# applications_affordable — floor(balance / rate)، لا تقريب لأعلى أبدًا
# ---------------------------------------------------------------------------


def test_applications_affordable_exact_multiple():
    # 10 / 0.235 = 42.55... → 42 تقديمًا (لا 43، لا تقريب لأعلى)
    assert wallet.applications_affordable(Decimal("10"), Decimal("0.235")) == 42


def test_applications_affordable_zero_balance():
    assert wallet.applications_affordable(Decimal("0"), Decimal("0.235")) == 0


def test_applications_affordable_negative_balance():
    assert wallet.applications_affordable(Decimal("-5"), Decimal("0.235")) == 0


def test_applications_affordable_zero_or_negative_rate_is_safe():
    assert wallet.applications_affordable(Decimal("10"), Decimal("0")) == 0
    assert wallet.applications_affordable(Decimal("10"), Decimal("-1")) == 0


def test_applications_affordable_partial_leftover_stays_unused_not_lost():
    # المبلغ المتبقي غير الكافي لتقديم كامل يبقى بالمحفظة (لا يُفقد ولا
    # يُقرّب) — راجع docstring الملف: "الباقي الذي لا يكفي لإرسال تقديم
    # يبقى بالمحفظة يُستخدَم لاحقًا".
    rate = Decimal("0.235")
    balance = Decimal("1.000")
    affordable = wallet.applications_affordable(balance, rate)
    assert affordable == 4  # 4 * 0.235 = 0.940
    leftover = balance - (affordable * rate)
    assert leftover == Decimal("0.060")
    assert leftover < rate  # لا يكفي تقديمًا خامسًا، يبقى كما هو


# ---------------------------------------------------------------------------
# amount_for_count — مبلغ فعلي يُطلَب تحويله، مُقرّب لأقرب هلالة
# ---------------------------------------------------------------------------


def test_amount_for_count_rounds_to_nearest_halala():
    # 10 * 0.235 = 2.35 بالضبط
    assert wallet.amount_for_count(10, Decimal("0.235")) == Decimal("2.35")
    # 3 * 0.235 = 0.705 → يُقرّب لأقرب هلالة (ROUND_HALF_UP) = 0.71
    assert wallet.amount_for_count(3, Decimal("0.235")) == Decimal("0.71")


def test_amount_for_count_zero_or_negative_count_is_zero():
    assert wallet.amount_for_count(0, Decimal("0.235")) == Decimal("0.00")
    assert wallet.amount_for_count(-5, Decimal("0.235")) == Decimal("0.00")


def test_amount_for_count_large_count():
    assert wallet.amount_for_count(100, Decimal("0.235")) == Decimal("23.50")


# ---------------------------------------------------------------------------
# build_packages_message (telegram_payments.py) — دالة نقية بمعامل wallet_rate
# صريح، بلا أي استدعاء لقاعدة بيانات (راجع تعليق التصميم بالملف نفسه).
# ---------------------------------------------------------------------------


def test_build_packages_message_wallet_topup_shows_rate_without_db():
    from app import telegram_payments as pay

    products = [
        {"code": "WALLET", "name_ar": "💰 ادفع حسب الاستخدام (محفظة)", "kind": "wallet_topup",
         "price_sar": None, "days": None, "applications_included": None},
    ]
    msg = pay.build_packages_message(products, wallet_rate=Decimal("0.235"))
    assert "0.235" in msg
    assert "💰 ادفع حسب الاستخدام (محفظة)" in msg


def test_build_packages_buttons_includes_wallet_option():
    from app import telegram_payments as pay

    products = [
        {"code": "WALLET", "name_ar": "💰 ادفع حسب الاستخدام (محفظة)", "kind": "wallet_topup",
         "price_sar": None, "days": None, "applications_included": None},
    ]
    buttons = pay.build_packages_buttons(products)
    flat = [b for row in buttons for b in row]
    assert any(b["callback_data"] == "pkg:WALLET" for b in flat)
