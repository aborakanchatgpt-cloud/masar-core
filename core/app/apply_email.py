"""
Masar Core — اختيار بريد التقديم الإلكتروني (B4، الدليل: "إرسال بالبريد
فقط من صندوق العميل").

جدول `jobs` الحالي (B2) لا يحمل عمود apply_email مستخرَجًا صراحة، وجدول
`companies` لا يحمل عمود careers_email بعد — هذا الملف مسؤول B4 حصرًا (لا
يعدّل core/app/collectors/field_extractor.py ولا discovery.py، ملك B2/B3):
يستخرج بريد التقديم مباشرة من نص الإعلان المخزّن أصلًا (description_snippet
أو raw_json)، بترتيب أولوية صارم يمنع مطلقًا مخاطبة صناديق عامة قد لا تصل
لمسؤول التوظيف الفعلي أو قد تُصعِّد شكوى (support@/sales@/...).

أولوية الاختيار (select_apply_email):
    1. عنوان "careers/jobs/hr/recruit..." مذكور صراحة داخل نص الإعلان
    2. أي عنوان آخر مذكور صراحة داخل نص الإعلان (posted) — طالما ليس مصنّفًا
       "محظورًا" (sales@/support@/info@/contact@/...)
    3. دليل الشركة (company_directory_email) — لا يزال placeholder معطّلًا
       فعليًا (companies.careers_email غير موجود بعد — راجع التوثيق أدناه)
    4. عنوان "عام" (info@/contact@) — يُقبل كملاذ أخير فقط
    5. لا شيء (None) — لا يُرسَل، تُسجَّل كفرصة متخطّاة (send_builder.py)

عناوين محظورة تمامًا (BANNED_PREFIXES) لا تُستخدم أبدًا حتى كملاذ أخير —
صناديق دعم/مبيعات عامة لا علاقة لها بالتوظيف، ومخاطبتها تخاطر بإرباك الشركة
أو كشف طبيعة الأتمتة (الدليل: "لا تكشف الأتمتة أبدًا للجهة الموظِّفة").
"""
from __future__ import annotations

import json
import re

# عناوين تدل على قسم التوظيف مباشرة — أولوية الاختيار الأولى.
CAREERS_PREFIXES: tuple[str, ...] = (
    "careers",
    "career",
    "jobs",
    "job",
    "recruitment",
    "recruiting",
    "hr",
    "talent",
    "hiring",
    "vacancies",
    "توظيف",
)

# عناوين "عامة" مقبولة كملاذ أخير فقط (لا كأولوية) — قد تصل فعليًا لمن
# يوجّهها لقسم التوظيف، لكنها ليست مخصّصة له.
GENERIC_PREFIXES: tuple[str, ...] = (
    "info",
    "contact",
)

# عناوين محظورة تمامًا — لا تُستخدم أبدًا مهما كانت الحالة (دعم/مبيعات/
# فواتير/إلخ لا علاقة لها بالتوظيف إطلاقًا).
BANNED_PREFIXES: tuple[str, ...] = (
    "support",
    "sales",
    "billing",
    "noreply",
    "no-reply",
    "donotreply",
    "marketing",
    "help",
    "admin",
    "webmaster",
    "postmaster",
    "abuse",
    "security",
    "press",
    "media",
    "legal",
    "privacy",
)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def classify_email(address: str | None) -> str | None:
    """يصنّف عنوان بريد واحد: 'banned'|'careers'|'generic'|'posted'|None
    (None فقط لعنوان فارغ/غير صالح). المطابقة على الجزء المحلي (قبل @) بحدود
    فاصل صريح (مطابقة تامة، أو متبوعة بـ./-/_) — يمنع تطابقًا زائفًا مثل
    'supportive@x.com' يُصنَّف خطأً 'banned' لمجرد احتوائه بادئة 'support'."""
    if not address or "@" not in address:
        return None
    local = address.split("@", 1)[0].lower()

    def _matches(prefix: str) -> bool:
        return local == prefix or local.startswith(prefix + ".") or local.startswith(prefix + "-") or local.startswith(prefix + "_")

    if any(_matches(p) for p in BANNED_PREFIXES):
        return "banned"
    if any(_matches(p) for p in CAREERS_PREFIXES):
        return "careers"
    if any(_matches(p) for p in GENERIC_PREFIXES):
        return "generic"
    return "posted"


def extract_emails(*texts) -> list[str]:
    """يستخرج كل عناوين البريد الفريدة المذكورة صراحة ضمن أي عدد من النصوص
    (بترتيب الظهور)، بلا تكرار (case-insensitive على الاسم الكامل). أي دخل
    ليس نصًا (مثال: raw_json قد يصل كـdict مُفكَّك مسبقًا من JSONB عبر
    psycopg) يُحوَّل لنص JSON أولًا بدل أن يفشل .findall()."""
    seen: set[str] = set()
    found: list[str] = []
    for text in texts:
        if not text:
            continue
        if not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=False, default=str)
        for match in _EMAIL_RE.findall(text):
            key = match.lower()
            if key not in seen:
                seen.add(key)
                found.append(match)
    return found


def company_directory_email(company_row: dict | None) -> str | None:
    """دليل بريد الشركة الموثوق (لو توفّر مصدر مستقل غير نص الإعلان نفسه) —
    **لا يزال placeholder معطّلًا فعليًا**: جدول `companies` الحالي (ترحيل
    0001-0004، ملك B2/B3) لا يحمل عمود careers_email بعد. هذه الدالة جاهزة
    لتفعيلها فور إضافة ذلك العمود مستقبلًا (بترحيل منفصل من فريق B2/B3، لا
    يجوز لـB4 إضافته هنا) — إرجاعها None دائمًا الآن مقصود وموثَّق، لا خطأ."""
    if not company_row:
        return None
    careers_email = company_row.get("careers_email") if isinstance(company_row, dict) else None
    if careers_email and classify_email(careers_email) != "banned":
        return careers_email
    return None


def select_apply_email(
    *,
    description_snippet: str | None,
    raw_json_text: str | dict | None = None,
    company_row: dict | None = None,
) -> tuple[str | None, str | None]:
    """يختار بريد التقديم النهائي حسب أولوية صارمة، ويرجع (address, email_class)
    حيث email_class أحد 'careers'|'posted'|'generic'|None (لا 'banned' أبدًا —
    عناوين محظورة تُستبعد من الاعتبار كليًا قبل أي مقارنة أولوية).

    الأولوية:
        1. careers-in-ad  (عنوان قسم توظيف مذكور صراحة بالإعلان)
        2. posted-in-ad   (أي عنوان آخر مذكور صراحة، ليس محظورًا ولا عامًا)
        3. company-directory (معطّل حاليًا — راجع company_directory_email)
        4. generic-in-ad  (info@/contact@ فقط كملاذ أخير)
        5. None
    """
    candidates = extract_emails(description_snippet, raw_json_text)
    classified = [(addr, classify_email(addr)) for addr in candidates]
    classified = [(addr, cls) for addr, cls in classified if cls != "banned"]

    for addr, cls in classified:
        if cls == "careers":
            return addr, "careers"

    for addr, cls in classified:
        if cls == "posted":
            return addr, "posted"

    directory_addr = company_directory_email(company_row)
    if directory_addr:
        return directory_addr, "careers"

    for addr, cls in classified:
        if cls == "generic":
            return addr, "generic"

    return None, None
