"""اختبارات دوال التصنيف النقية بـcore/app/inbox.py (B4، الدليل: "الارتدادات
لا تُحتسب، القيمة تُستردّ؛ اكتشاف الردود/المقابلات") — بلا IMAP ولا قاعدة
بيانات، مذكورة صراحة بتوثيق inbox.py:15 كـ"قابلة للاختبار المباشر".

تصحيح Medium 1 بمراجعة B4 الأوفلاين (docs/reports/B4-offline-review.md):
هذا الملف كان مفقودًا فعليًا رغم أن الوثيقة الداخلية تدّعي وجوده. يغطي
عيّنات عربية وإنجليزية (ارتداد/رد/مقابلة) وأولوية التصنيف الصارمة
(bounce > interview > reply > other) وdوال استخراج مساعدة (extract_original_message_id).

تشغيل: cd core && python -m pytest tests/test_inbox_classifier.py -v
"""
from __future__ import annotations

import pytest

from app.inbox import (
    classify_kind,
    extract_original_message_id,
    is_bounce,
    is_interview_mention,
)


# ---------------------------------------------------------------------------
# 1. is_bounce — إنجليزي وعربي، عبر عنوان المرسِل أو الموضوع
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "from_addr, subject",
    [
        ("Mail Delivery Subsystem <mailer-daemon@google.com>", "Delivery Status Notification (Failure)"),
        ("MAILER-DAEMON@example.com", "Undelivered Mail Returned to Sender"),
        ("postmaster@example.com", "failure notice"),
        ("noreply@company.com", "رسالتك لم تصل"),
        ("noreply@company.com", "فشل التسليم"),
        ("noreply@company.com", "تعذر التسليم"),
        ("noreply@company.com", "فشل الإرسال"),
        ("Mail Delivery System <MAiler-Daemon@host.com>", "أي موضوع عادي"),  # عنوان يكفي وحده
    ],
)
def test_is_bounce_true_english_and_arabic_samples(from_addr, subject):
    assert is_bounce(from_addr, subject) is True


@pytest.mark.parametrize(
    "from_addr, subject",
    [
        ("hr@company.com", "شكرًا لتقديمك — سنراجع طلبك"),
        ("recruiter@company.com", "Thanks for applying"),
        (None, None),
        ("hr@company.com", None),
        (None, "طلب استلمناه"),
    ],
)
def test_is_bounce_false_normal_mail(from_addr, subject):
    assert is_bounce(from_addr, subject) is False


def test_is_bounce_subject_alone_is_sufficient():
    """عنوان مرسِل عادي، لكن الموضوع نمط ارتداد صريح — يكفي وحده."""
    assert is_bounce("random@company.com", "Delivery Status Notification (Failure)") is True


# ---------------------------------------------------------------------------
# 2. is_interview_mention — إنجليزي وعربي، عبر الموضوع أو المقتطف
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "subject, snippet",
    [
        ("Interview Invitation", None),
        ("Let's schedule a call", None),
        (None, "We would like to schedule an interview with you next week."),
        ("موعد مقابلة", None),
        ("دعوة لحضور مقابلة", None),
        (None, "نود دعوتك لحضور مقابلة يوم الأحد القادم"),
        ("طلبك", "تم قبول طلبك وسنحدد موعد مقابلة قريبًا"),
    ],
)
def test_is_interview_mention_true_english_and_arabic(subject, snippet):
    assert is_interview_mention(subject, snippet) is True


@pytest.mark.parametrize(
    "subject, snippet",
    [
        ("Thanks for applying", "We received your application."),
        ("شكرًا لتقديمك", "استلمنا طلبك وسنتواصل معك"),
        (None, None),
    ],
)
def test_is_interview_mention_false(subject, snippet):
    assert is_interview_mention(subject, snippet) is False


# ---------------------------------------------------------------------------
# 3. classify_kind — أولوية صارمة bounce > interview > reply > other
# ---------------------------------------------------------------------------


def test_classify_kind_bounce_english():
    assert classify_kind(
        from_addr="mailer-daemon@google.com",
        subject="Delivery Status Notification (Failure)",
        snippet="Your message wasn't delivered",
        has_reply_reference=True,  # حتى مع مرجع رد — الارتداد يتغلّب
    ) == "bounce"


def test_classify_kind_bounce_arabic():
    assert classify_kind(
        from_addr="postmaster@example.com",
        subject="فشل التسليم",
        snippet=None,
        has_reply_reference=False,
    ) == "bounce"


def test_classify_kind_interview_english():
    assert classify_kind(
        from_addr="hr@company.com",
        subject="Interview Invitation — Process Engineer",
        snippet="We would like to schedule a call",
        has_reply_reference=False,
    ) == "interview"


def test_classify_kind_interview_arabic():
    assert classify_kind(
        from_addr="hr@company.sa",
        subject="دعوة لحضور مقابلة",
        snippet=None,
        has_reply_reference=False,
    ) == "interview"


def test_classify_kind_reply_when_only_reply_reference():
    assert classify_kind(
        from_addr="hr@company.com",
        subject="Re: Your application",
        snippet="Thanks for reaching out, we'll review your CV.",
        has_reply_reference=True,
    ) == "reply"


def test_classify_kind_other_when_nothing_matches():
    assert classify_kind(
        from_addr="newsletter@company.com",
        subject="نشرتنا الشهرية",
        snippet="أخبار الشركة هذا الشهر",
        has_reply_reference=False,
    ) == "other"


def test_classify_kind_bounce_beats_interview_even_if_body_mentions_interview():
    """جسم رسالة DSN قد يتضمّن نسخة من رسالتنا الأصلية (تذكر "مقابلة" مثلًا)
    — الارتداد يجب أن يتغلّب دومًا (موثَّق صراحة بتعليق classify_kind)."""
    assert classify_kind(
        from_addr="mailer-daemon@google.com",
        subject="Undelivered Mail Returned to Sender",
        snippet="...نتطلع لمقابلتك قريبًا إن شاء الله...",  # جزء من رسالتنا الأصلية المُرفَقة بالـDSN
        has_reply_reference=True,
    ) == "bounce"


def test_classify_kind_interview_beats_reply():
    assert classify_kind(
        from_addr="hr@company.com",
        subject="Re: schedule an interview",
        snippet=None,
        has_reply_reference=True,
    ) == "interview"


# ---------------------------------------------------------------------------
# 4. extract_original_message_id — آخر تطابق (Message-ID الأصلي المتداخل)
# ---------------------------------------------------------------------------


def test_extract_original_message_id_none_when_absent():
    assert extract_original_message_id(None) is None
    assert extract_original_message_id("لا يوجد أي شيء هنا") is None


def test_extract_original_message_id_single_match():
    raw = "Headers...\nMessage-ID: <abc123@masar.local>\nBody..."
    assert extract_original_message_id(raw) == "<abc123@masar.local>"


def test_extract_original_message_id_returns_last_match_for_nested_dsn():
    """رسالة DSN نمطية: Message-ID الخاص بالـDSN نفسه أولًا، ثم رسالتنا
    الأصلية الفاشلة متداخلة لاحقًا — آخر تطابق هو الأصح (موثَّق بتعليق الدالة)."""
    raw = (
        "Message-ID: <dsn-outer@mta.google.com>\n"
        "Content-Type: multipart/report\n"
        "...\n"
        "--- forwarded message ---\n"
        "Message-ID: <masar-original-42@masar.local>\n"
        "Subject: طلب توظيف"
    )
    assert extract_original_message_id(raw) == "<masar-original-42@masar.local>"


def test_extract_original_message_id_case_insensitive():
    raw = "message-id: <lower-case@masar.local>"
    assert extract_original_message_id(raw) == "<lower-case@masar.local>"
