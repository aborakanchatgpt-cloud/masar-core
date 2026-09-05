"""
جامع تنبيهات البريد (alert_mail) — على الأرجح المصدر الأهم فعليًا للسوق
السعودي/الخليجي: أغلب الشركات الكبرى (Aramco وSABIC وشركات المياه/التحلية
وغيرها) لا تستخدم أنظمة ATS الحديثة ذات الواجهة العامة (Greenhouse/Lever/
Ashby/...) — تلك تغلب عليها شركات تقنية عالمية. الوظائف الفعلية بالسوق
المحلي تُنشر غالبًا عبر لوحات مثل Bayt وGulfTalent وLinkedIn وIndeed، وأفضل
طريقة موثوقة لجمعها دون كسر شروط أي موقع هي الاشتراك بتنبيهات البريد
الإلكتروني الرسمية من تلك اللوحات نفسها وقراءتها عبر IMAP — تمامًا كما يطلب
مستخدم بشري تنبيهات البريد لنفسه.

هذا الجامع "إطار عمل" جاهز للتوصيل (IMAP + استخراج روابط الوظائف من HTML
البريد بأنماط عناوين معروفة)، وليس اكتمالًا نهائيًا — دقة الاستخراج التفصيلي
(العنوان/الشركة/المدينة من كل قالب بريد) تحتاج معايرة على عيّنة حقيقية من
كل مزوّد بمجرد ربط صندوق بريد فعلي (لا تتوفر عيّنات حقيقية أثناء البناء بدون
صندوق بريد نشط)، فالتفاصيل الدقيقة لقالب HTML لكل مزوّد قد تتغيّر بمرور الوقت.
"""
from __future__ import annotations

import email
import imaplib
import re
from dataclasses import dataclass
from email.message import Message

# أنماط روابط الوظائف المعروفة لكل مزوّد — تُستخدم لالتقاط رابط الوظيفة الفعلي
# من داخل HTML الرسالة، بغض النظر عن تصميم القالب المرئي (الذي يتغيّر كثيرًا).
_JOB_LINK_PATTERNS: dict[str, re.Pattern] = {
    "bayt": re.compile(r"https?://(?:www\.)?bayt\.com/[a-z]{2}/[a-z-]+/jobs/[^\s\"'<>]+", re.IGNORECASE),
    "gulftalent": re.compile(r"https?://(?:www\.)?gulftalent\.com/[a-z-]+/jobs/[^\s\"'<>]+", re.IGNORECASE),
    "linkedin": re.compile(r"https?://(?:www\.)?linkedin\.com/jobs/view/[^\s\"'<>]+", re.IGNORECASE),
    "indeed": re.compile(r"https?://[a-z]{2}\.indeed\.com/(?:rc/clk|viewjob)[^\s\"'<>]*", re.IGNORECASE),
    "naukrigulf": re.compile(r"https?://(?:www\.)?naukrigulf\.com/[^\s\"'<>]*job[^\s\"'<>]*", re.IGNORECASE),
}

# عناوين المرسِل الرسمية لكل مزوّد (لفلترة البحث IMAP) — تُعدَّل حسب ما يصل فعليًا
_SENDER_DOMAINS: dict[str, str] = {
    "bayt": "bayt.com",
    "gulftalent": "gulftalent.com",
    "linkedin": "jobs-noreply@linkedin.com",
    "indeed": "indeed.com",
    "naukrigulf": "naukrigulf.com",
}


@dataclass
class MailAlertJob:
    provider: str
    url: str
    title: str | None = None


def _extract_links(html_body: str, provider: str) -> list[str]:
    pattern = _JOB_LINK_PATTERNS.get(provider)
    if not pattern:
        return []
    seen: list[str] = []
    for match in pattern.findall(html_body):
        # تنظيف بسيط: قطع أي HTML entity متبقٍ بنهاية الرابط
        cleaned = match.split("&quot;")[0].split("&amp;utm")[0]
        if cleaned not in seen:
            seen.append(cleaned)
    return seen


def _get_html_body(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", errors="ignore")
        return ""
    if msg.get_content_type() == "text/html":
        payload = msg.get_payload(decode=True)
        return payload.decode(msg.get_content_charset() or "utf-8", errors="ignore") if payload else ""
    return ""


def fetch_jobs_from_mailbox(
    imap_host: str,
    imap_user: str,
    imap_password: str,
    provider: str,
    mailbox: str = "INBOX",
    limit: int = 50,
) -> list[dict]:
    """يقرأ آخر رسائل غير مقروءة من مزوّد تنبيهات معيّن ويستخرج روابط الوظائف منها.

    ملاحظة أمنية: كلمة مرور IMAP تُمرَّر هنا كمعامل عادي مقصود — الاستدعاء
    الفعلي يقرأها من متغيّرات بيئة الخادم (.env) وقت التشغيل فقط، ولا تُكتب
    أبدًا بالكود أو تُمرَّر عبر أي جلسة Claude.
    """
    sender_filter = _SENDER_DOMAINS.get(provider)
    if not sender_filter:
        raise ValueError(f"مزوّد غير مدعوم: {provider}")

    jobs: list[dict] = []
    with imaplib.IMAP4_SSL(imap_host) as imap:
        imap.login(imap_user, imap_password)
        imap.select(mailbox)
        status, data = imap.search(None, f'(UNSEEN FROM "{sender_filter}")')
        if status != "OK":
            return jobs

        message_ids = data[0].split()[-limit:]
        for msg_id in message_ids:
            status, msg_data = imap.fetch(msg_id, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)
            html_body = _get_html_body(msg)
            for link in _extract_links(html_body, provider):
                jobs.append(
                    {
                        "external_id": link,
                        "title": None,  # يحتاج استخراج إضافي من نص الرابط المجاور بالقالب الفعلي لكل مزوّد
                        "url": link,
                        "location": None,
                        "updated_at": msg.get("Date"),
                        "raw": {"provider": provider, "subject": msg.get("Subject")},
                    }
                )
    return jobs
