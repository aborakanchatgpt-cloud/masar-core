"""اختبارات وحدة (بلا IMAP ولا قاعدة بيانات) لدوال التقسيم/التراجع الجديدة
بـcore/app/inbox.py (B6، docs/reports/B6-executor.md بند 5: "partition mail
links across scheduler ticks... skip links in error state with backoff")."""
from __future__ import annotations

from app.inbox import backoff_minutes, current_partition, INBOX_BACKOFF_MAX_MINUTES, INBOX_TICK_SECONDS


def test_current_partition_disabled_when_count_le_1():
    assert current_partition(1, now_ts=12345.0) == 0
    assert current_partition(0, now_ts=12345.0) == 0


def test_current_partition_deterministic_same_tick():
    now = 1_000_000.0
    assert current_partition(15, now_ts=now) == current_partition(15, now_ts=now + 1)


def test_current_partition_rotates_across_ticks():
    base = 1_000_000.0
    p0 = current_partition(15, now_ts=base)
    p1 = current_partition(15, now_ts=base + INBOX_TICK_SECONDS)
    # جولة كاملة كل 15 تكة — قسم واحد لاحقًا يجب أن يختلف (أو يلفّ لنفس
    # القيمة فقط بعد count تكة كاملة، لا تكة واحدة).
    assert p1 == (p0 + 1) % 15


def test_current_partition_full_cycle_covers_all_partitions():
    base = 2_000_000.0
    seen = {current_partition(15, now_ts=base + i * INBOX_TICK_SECONDS) for i in range(15)}
    assert seen == set(range(15))


def test_backoff_minutes_zero_when_no_errors():
    assert backoff_minutes(0) == 0


def test_backoff_minutes_doubles_then_caps():
    assert backoff_minutes(1) == 2
    assert backoff_minutes(2) == 4
    assert backoff_minutes(3) == 8
    # يجب ألا يتجاوز السقف مهما كبر عدّاد الأخطاء.
    assert backoff_minutes(20) == INBOX_BACKOFF_MAX_MINUTES
