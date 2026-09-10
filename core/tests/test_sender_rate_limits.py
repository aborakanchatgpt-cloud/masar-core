"""اختبارات وحدة (بلا قاعدة بيانات ولا SMTP) لمحدِّدات الوتيرة/المقاييس
الجديدة بـcore/app/sender.py (B6، docs/reports/B6-executor.md بند 4:
"per-mailbox rate limits... global tokens/sec cap... metrics counters")."""
from __future__ import annotations

import time

from app import sender


def test_classify_error_buckets():
    assert sender._classify_error("SMTPAuthenticationError: 535 bad") == "auth"
    assert sender._classify_error("timed out") == "timeout"
    assert sender._classify_error("Connection refused") == "connection"
    assert sender._classify_error("SMTPRecipientsRefused") == "recipient_refused"
    assert sender._classify_error("لا يوجد صندوق بريد صالح لهذا العميل") == "no_mailbox"
    assert sender._classify_error("something odd") == "other"


def test_throttle_mailbox_first_call_no_wait():
    cid = -1001  # معرّف وهمي مستقل عن أي اختبار آخر (لا يتشارك مفتاحًا حقيقيًا)
    sender._mailbox_last_sent_monotonic.pop(cid, None)
    hit = sender._throttle_mailbox(cid)
    assert hit is False


def test_throttle_mailbox_second_call_waits_and_records_hit(monkeypatch):
    cid = -1002
    sender._mailbox_last_sent_monotonic.pop(cid, None)
    # فاصل أدنى صغير جدًا (10ms) حتى لا يُبطئ الاختبار فعليًا — نتحقق من
    # *السلوك* (انتظار + عدّاد) لا من القيمة الحقيقية 6 ثوانٍ.
    monkeypatch.setattr(sender, "MAILBOX_MIN_INTERVAL_SECONDS", 0.05)
    before = sender.get_runtime_metrics()["mailbox_rate_limit_hits"]
    sender._throttle_mailbox(cid)  # الأولى: بلا انتظار، تحجز الفتحة القادمة
    t0 = time.monotonic()
    hit = sender._throttle_mailbox(cid)  # الثانية فورًا: يجب أن تنتظر
    elapsed = time.monotonic() - t0
    assert hit is True
    assert elapsed >= 0.04
    after = sender.get_runtime_metrics()["mailbox_rate_limit_hits"]
    assert after == before + 1


def test_throttle_mailbox_disabled_when_interval_zero(monkeypatch):
    monkeypatch.setattr(sender, "MAILBOX_MIN_INTERVAL_SECONDS", 0)
    assert sender._throttle_mailbox(-1003) is False


def test_get_runtime_metrics_snapshot_is_copy():
    snap1 = sender.get_runtime_metrics()
    snap1["sent_total"] = 999999
    snap2 = sender.get_runtime_metrics()
    assert snap2["sent_total"] != 999999
