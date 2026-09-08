"""اختبارات core/app/pacing.py (B4، الدليل: "نافذة الإرسال الأحد-الخميس
08:00-16:30 بتوقيت الرياض"، "وتيرة إحماء تدريجية 6/12/16 ثم 22 كحد أقصى"،
"هدف يومي 17 (حتى 22)") — وحدة نقية بالكامل، بلا اتصال قاعدة بيانات ولا
شبكة، مذكورة صراحة بتوثيق pacing.py:7 كـ"قابلة للاختبار المباشر".

تصحيح Medium 1 بمراجعة B4 الأوفلاين (docs/reports/B4-offline-review.md):
هذا الملف كان مفقودًا فعليًا رغم أن الوثيقة الداخلية تدّعي وجوده.

تشغيل: cd core && python -m pytest tests/test_pacing.py -v
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app import pacing


# ---------------------------------------------------------------------------
# 1. تحويل التوقيت ذهابًا وإيابًا (round-trip) — الفخ الموثَّق صراحة بأعلى
#    الملف: تخزين مباشر لساعة مُزاحة كـUTC سيُفسَّر خطأً بفارق 3 ساعات.
# ---------------------------------------------------------------------------


def test_to_riyadh_naive_adds_three_hours():
    utc_dt = datetime(2026, 9, 8, 5, 0, tzinfo=timezone.utc)  # ثلاثاء
    riyadh = pacing.to_riyadh_naive(utc_dt)
    assert riyadh == datetime(2026, 9, 8, 8, 0)
    assert riyadh.tzinfo is None


def test_to_riyadh_naive_assumes_utc_when_naive_input():
    naive_utc = datetime(2026, 9, 8, 5, 0)  # بلا tzinfo — يُفترض UTC
    assert pacing.to_riyadh_naive(naive_utc) == datetime(2026, 9, 8, 8, 0)


def test_riyadh_naive_to_utc_subtracts_three_hours():
    riyadh_naive = datetime(2026, 9, 8, 8, 0)
    utc_dt = pacing.riyadh_naive_to_utc(riyadh_naive)
    assert utc_dt == datetime(2026, 9, 8, 5, 0, tzinfo=timezone.utc)
    assert utc_dt.tzinfo == timezone.utc


def test_riyadh_naive_to_utc_rejects_aware_input():
    aware = datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        pacing.riyadh_naive_to_utc(aware)


@pytest.mark.parametrize(
    "utc_dt",
    [
        datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 8, 12, 30, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 23, 59, tzinfo=timezone.utc),
        datetime(2026, 12, 31, 21, 0, tzinfo=timezone.utc),
    ],
)
def test_round_trip_utc_riyadh_utc(utc_dt):
    """UTC → رياض ساذج → UTC يجب أن يرجع لنفس اللحظة تمامًا (لا فقدان بيانات)."""
    riyadh_naive = pacing.to_riyadh_naive(utc_dt)
    back_to_utc = pacing.riyadh_naive_to_utc(riyadh_naive)
    assert back_to_utc == utc_dt


# ---------------------------------------------------------------------------
# 2. نافذة الإرسال: الأحد-الخميس 08:00-16:30 الرياض، حدّان شاملان، تشمل
#    حالة الحدّ الفاصل (edge times) ويوم الجمعة صراحة (يوم عطلة، ليس ضمن
#    SEND_WEEKDAYS).
# ---------------------------------------------------------------------------


def _riyadh(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm)


# 2026-09-08 الثلاثاء (weekday()=1) — يوم إرسال. 2026-09-11 الجمعة (weekday()=4) — عطلة.
@pytest.mark.parametrize(
    "dt, expected",
    [
        # الحدّ الأدنى للنافذة (شامل)
        (_riyadh(2026, 9, 8, 8, 0), True),
        # لحظة واحدة قبل بداية النافذة
        (_riyadh(2026, 9, 8, 7, 59), False),
        # الحدّ الأقصى للنافذة (شامل)
        (_riyadh(2026, 9, 8, 16, 30), True),
        # لحظة واحدة بعد نهاية النافذة
        (_riyadh(2026, 9, 8, 16, 31), False),
        # منتصف النافذة
        (_riyadh(2026, 9, 8, 12, 0), True),
        # منتصف الليل (خارج النافذة تمامًا)
        (_riyadh(2026, 9, 8, 0, 0), False),
    ],
)
def test_is_in_window_edge_times_on_send_day(dt, expected):
    assert pacing.is_in_window(dt) is expected


@pytest.mark.parametrize(
    "day",
    [
        date(2026, 9, 6),  # الأحد
        date(2026, 9, 7),  # الاثنين
        date(2026, 9, 8),  # الثلاثاء
        date(2026, 9, 9),  # الأربعاء
        date(2026, 9, 10),  # الخميس
    ],
)
def test_is_send_weekday_true_for_sun_to_thu(day):
    assert pacing.is_send_weekday(day) is True


@pytest.mark.parametrize(
    "day",
    [
        date(2026, 9, 11),  # الجمعة
        date(2026, 9, 12),  # السبت
    ],
)
def test_is_send_weekday_false_for_fri_sat(day):
    assert pacing.is_send_weekday(day) is False


def test_is_in_window_false_on_friday_even_within_hours():
    """جمعة، 2026-09-11 — الساعة 12:00 (وسط نافذة الساعات لو كانت يوم إرسال)
    لكن اليوم نفسه ليس ضمن SEND_WEEKDAYS، فيجب أن ترجع False دومًا."""
    friday_noon = _riyadh(2026, 9, 11, 12, 0)
    assert pacing.is_send_weekday(friday_noon.date()) is False
    assert pacing.is_in_window(friday_noon) is False


def test_is_in_window_false_on_friday_at_exact_window_start():
    """حتى عند حدّ بداية النافذة بالضبط (08:00) — الجمعة تبقى False."""
    assert pacing.is_in_window(_riyadh(2026, 9, 11, 8, 0)) is False


def test_is_in_window_false_on_saturday():
    assert pacing.is_in_window(_riyadh(2026, 9, 12, 10, 0)) is False


def test_next_window_bounds_same_day_when_before_window_end():
    now = _riyadh(2026, 9, 8, 10, 0)  # ثلاثاء، ضمن النافذة
    start, end = pacing.next_window_bounds(now)
    assert start == datetime(2026, 9, 8, 8, 0)
    assert end == datetime(2026, 9, 8, 16, 30)


def test_next_window_bounds_skips_to_next_send_day_after_window_end():
    """خميس بعد نهاية النافذة (16:30) → الجمعة/السبت عطلة → يقفز للأحد التالي."""
    now = _riyadh(2026, 9, 10, 17, 0)  # خميس بعد النافذة
    start, end = pacing.next_window_bounds(now)
    assert start.date() == date(2026, 9, 13)  # الأحد التالي
    assert start.time() == pacing.WINDOW_START
    assert end.time() == pacing.WINDOW_END


def test_next_window_bounds_skips_friday_saturday():
    now = _riyadh(2026, 9, 11, 9, 0)  # جمعة
    start, _ = pacing.next_window_bounds(now)
    assert start.date() == date(2026, 9, 13)  # الأحد (يتخطّى الجمعة والسبت)


# ---------------------------------------------------------------------------
# 3. وتيرة الإحماء التدريجية: 6/12/16 ثم 22 كحد أقصى (_RAMP_TIERS بحدود دقيقة)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "age_days, expected_cap",
    [
        (0, 6),  # يوم 1 (يوم الإنشاء نفسه)
        (1, 6),  # يوم 2 — آخر يوم بالمرحلة الأولى (6)
        (2, 12),  # يوم 3 — أول يوم بالمرحلة الثانية (12)
        (4, 12),  # يوم 5 — آخر يوم بالمرحلة الثانية (12)
        (5, 16),  # يوم 6 — أول يوم بالمرحلة الثالثة (16)
        (8, 16),  # يوم 9 — آخر يوم بالمرحلة الثالثة (16)
        (9, 22),  # يوم 10 — أول يوم بالحد الأقصى (MAX_DAILY)
        (30, 22),  # صندوق قديم مستقر — يبقى عند الحد الأقصى
    ],
)
def test_ramp_cap_tier_boundaries(age_days, expected_cap):
    today = date(2026, 9, 20)
    created_riyadh_date = today - timedelta(days=age_days)
    # created_at يُخزَّن UTC — نحوّل تاريخ الإنشاء (رياض) إلى UTC مطابق حتى
    # لا يزيح age_days المحسوب داخل ramp_cap بفارق ساعات التحويل.
    created_utc = pacing.riyadh_naive_to_utc(datetime.combine(created_riyadh_date, pacing.WINDOW_START))
    assert pacing.ramp_cap(created_utc, today) == expected_cap


def test_ramp_cap_none_created_at_treated_as_mature():
    """صندوق بلا تاريخ إنشاء معروف (None) يُعامَل كصندوق قديم مستقر
    (MAX_DAILY) — موثَّق صراحة بتعليق الدالة (الحذر بعدم تقييد بالخطأ)."""
    assert pacing.ramp_cap(None, date(2026, 9, 20)) == pacing.MAX_DAILY


def test_ramp_cap_future_created_at_clamped_to_zero_age():
    """created_at بالمستقبل (ساعة خادم غير متزامنة مثلًا) — age_days سالب
    يُقصّ لصفر بدل رقم سالب يكسر منطق المقارنة."""
    today = date(2026, 9, 20)
    future_utc = pacing.riyadh_naive_to_utc(datetime.combine(today + timedelta(days=5), pacing.WINDOW_START))
    assert pacing.ramp_cap(future_utc, today) == 6


# ---------------------------------------------------------------------------
# 4. الهدف اليومي: DEFAULT_TARGET_DAILY=17، MAX_DAILY=22 (17-22 بالتكليف)
# ---------------------------------------------------------------------------


def test_default_target_daily_is_17():
    assert pacing.DEFAULT_TARGET_DAILY == 17


def test_max_daily_is_22():
    assert pacing.MAX_DAILY == 22


def test_default_target_within_17_22_range():
    assert 17 <= pacing.DEFAULT_TARGET_DAILY <= pacing.MAX_DAILY == 22


# ---------------------------------------------------------------------------
# 5. next_slot: تباعد [MIN_GAP_MINUTES, MAX_GAP_MINUTES]، None عند تجاوز
#    نهاية النافذة، وقصّ لبداية النافذة إن كان previous_slot قبلها.
# ---------------------------------------------------------------------------


def test_next_slot_within_gap_bounds():
    rng = pacing.deterministic_rng("seed-1")
    previous = datetime(2026, 9, 8, 8, 0)
    window_end = datetime(2026, 9, 8, 16, 30)
    slot = pacing.next_slot(previous, datetime(2026, 9, 8, 8, 0), window_end, rng)
    assert slot is not None
    gap_minutes = (slot - previous).total_seconds() / 60
    assert pacing.MIN_GAP_MINUTES <= gap_minutes <= pacing.MAX_GAP_MINUTES


def test_next_slot_none_when_past_window_end():
    rng = pacing.deterministic_rng("seed-2")
    previous = datetime(2026, 9, 8, 16, 25)  # قريب جدًا من النهاية
    window_end = datetime(2026, 9, 8, 16, 30)
    # فارق أدنى 8 دقائق يتجاوز حتمًا نهاية النافذة من 16:25
    slot = pacing.next_slot(previous, datetime(2026, 9, 8, 8, 0), window_end, rng)
    assert slot is None


def test_next_slot_clamped_to_window_start():
    rng = pacing.deterministic_rng("seed-3")
    previous = datetime(2026, 9, 8, 4, 0)  # قبل بداية النافذة بكثير
    window_start = datetime(2026, 9, 8, 8, 0)
    window_end = datetime(2026, 9, 8, 16, 30)
    slot = pacing.next_slot(previous, window_start, window_end, rng)
    assert slot == window_start


def test_deterministic_rng_same_seed_same_sequence():
    rng1 = pacing.deterministic_rng("customer-42:2026-09-08")
    rng2 = pacing.deterministic_rng("customer-42:2026-09-08")
    assert [rng1.random() for _ in range(5)] == [rng2.random() for _ in range(5)]


def test_deterministic_rng_different_seed_different_sequence():
    rng1 = pacing.deterministic_rng("customer-42:2026-09-08")
    rng2 = pacing.deterministic_rng("customer-43:2026-09-08")
    assert [rng1.random() for _ in range(5)] != [rng2.random() for _ in range(5)]
