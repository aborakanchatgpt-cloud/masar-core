"""
المطبّع الكامل — يستخرج من نص الوظيفة (العنوان + الوصف) الحقول التي يحتاجها
الاستبعاد القاطع بالمرحلة 3 (نفس منطق المرحلة 0 لكن كوحدة قابلة لإعادة
الاستخدام على مستوى قاعدة البيانات بدل موجّه Claude لكل وظيفة):

    سنوات الخبرة المطلوبة، مستوى الأقدمية، شرط الجنسية، التخصص/الشهادة
    المطلوبة، المدن المذكورة، رمز الدولة/داخل-خارج نطاق الخليج، المهارات
    المذكورة، ونوع التقديم المرجّح.

كل دالة هنا Heuristic (كشف بالكلمات المفتاحية/الأنماط) مصمّم ليكون "مرشّح أول"
سريع بدون أي تكلفة API — وليس بديلاً نهائياً عن مراجعة Claude للحالات الحدّية،
تمامًا كما يقضي الدليل بمراجعة عينة يدوية للتحقق من نسبة الدقة (القسم 9،
معيار قبول المرحلة 2: دقة الحقول المستخرجة ≥ 90% على عينة 50 وظيفة).

مراجعة B2 (docs/reports/B2-review.md) R2: أُصلح خلل مطابقة السلسلة الفرعية
بمستوى الأقدمية (كانت "intern" تُطابق داخل "internal"/"international") —
كل الكلمات المفتاحية هنا الآن تُطابق بحدود كلمة صريحة (`\\b...\\b`) عبر
تعابير نمطية مُجمّعة مسبقًا، لا بحث سلسلة فرعية (`in`).
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# سنوات الخبرة
# ---------------------------------------------------------------------------

_YEARS_RANGE_RE = re.compile(
    r"(\d{1,2})\s*(?:-|to|إلى|–|—)\s*(\d{1,2})\s*(?:years?|yrs?|سن(?:ة|وات))",
    re.IGNORECASE,
)
_YEARS_PLUS_RE = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:years?|yrs?|سن(?:ة|وات))\s*(?:of\s+)?(?:experience|خبرة)?",
    re.IGNORECASE,
)
_YEARS_ARABIC_PREFIX_RE = re.compile(
    r"(?:خبرة|خبره)\s*(?:لا تقل عن|أكثر من|من)?\s*(\d{1,2})",
)


def extract_years_required(text: str) -> tuple[int | None, int | None]:
    """يرجع (الحد الأدنى، الحد الأقصى) لسنوات الخبرة المطلوبة إن وُجدت، وإلا (None, None)."""
    if not text:
        return (None, None)

    range_match = _YEARS_RANGE_RE.search(text)
    if range_match:
        low, high = int(range_match.group(1)), int(range_match.group(2))
        return (min(low, high), max(low, high))

    plus_match = _YEARS_PLUS_RE.search(text)
    if plus_match:
        value = int(plus_match.group(1))
        return (value, None)

    arabic_match = _YEARS_ARABIC_PREFIX_RE.search(text)
    if arabic_match:
        value = int(arabic_match.group(1))
        return (value, None)

    return (None, None)


# ---------------------------------------------------------------------------
# مستوى الأقدمية — مراجعة B2 R2: حدود كلمة صريحة، لا سلسلة فرعية
# ---------------------------------------------------------------------------

_SENIORITY_BUCKETS: list[tuple[str, list[str]]] = [
    (
        "intern",
        [
            "intern", "internship", "trainee", "fresh graduate", "graduate program",
            "co-op", "متدرب", "تدريب", "برنامج تدريب",
        ],
    ),
    ("entry", ["entry level", "junior", "حديث التخرج", "مبتدئ"]),
    ("senior", ["senior", "خبير", "أول"]),
    (
        "manager",
        [
            "manager", "head of", "director", "head", "chief",
            "مدير", "رئيس قسم", "رئيس",
        ],
    ),
    ("lead", ["lead", "principal", "قائد فريق"]),
]


def _compile_word_boundary(keywords: list[str]) -> re.Pattern[str]:
    escaped = sorted((re.escape(kw) for kw in keywords), key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(escaped) + r")\b", re.IGNORECASE)


_SENIORITY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (level, _compile_word_boundary(keywords)) for level, keywords in _SENIORITY_BUCKETS
]


def extract_seniority(text: str, title: str | None = None) -> str | None:
    """يرجع أقرب مستوى أقدمية مطابق بحدود كلمة صريحة، أو None إن لم يُذكر
    صراحة (يُفترض mid). إن مُرّر `title` منفصلاً، يُفحص أولاً وحده قبل النص
    الكامل — إشارة العنوان أوثق من نص وصف طويل قد يحوي كلمات عامة مضلّلة
    (مراجعة B2 R2)."""
    if title:
        for level, pattern in _SENIORITY_PATTERNS:
            if pattern.search(title):
                return level
    if not text:
        return None
    for level, pattern in _SENIORITY_PATTERNS:
        if pattern.search(text):
            return level
    return None


# ---------------------------------------------------------------------------
# شرط الجنسية
# ---------------------------------------------------------------------------

_SAUDI_ONLY_PATTERNS = [
    "saudi nationals only",
    "saudis only",
    "must be saudi",
    "سعودي الجنسية",
    "سعوديين فقط",
    "للسعوديين فقط",
    "يشترط الجنسية السعودية",
]


def is_saudi_only(text: str) -> bool:
    """يكتشف اشتراط الجنسية السعودية الصريح فقط — لا يفترض شيئًا عند غياب الذكر."""
    if not text:
        return False
    lowered = text.lower()
    return any(pattern in lowered for pattern in _SAUDI_ONLY_PATTERNS)


# ---------------------------------------------------------------------------
# المدن (مبدئيًا السعودية/الخليج، تُوسّع لاحقًا حسب مدن Source Curator)
# ---------------------------------------------------------------------------

KNOWN_CITIES = [
    "Riyadh", "Jeddah", "Dammam", "Khobar", "Dhahran", "Yanbu", "Jubail",
    "Mecca", "Medina", "Taif", "Abha", "Tabuk", "Najran",
    "الرياض", "جدة", "الدمام", "الخبر", "الظهران", "ينبع", "الجبيل",
    "مكة", "المدينة", "الطائف", "أبها", "تبوك", "نجران",
    "Dubai", "Abu Dhabi", "Doha", "Manama", "Kuwait City", "Muscat",
]


def extract_cities(text: str) -> list[str]:
    """يرجع كل المدن المعروفة المذكورة صراحة بالنص (بدون تكرار، بترتيب الظهور)."""
    if not text:
        return []
    found: list[str] = []
    for city in KNOWN_CITIES:
        if city in text and city not in found:
            found.append(city)
    return found


# ---------------------------------------------------------------------------
# الدولة/المنطقة — مراجعة B2 R3/R4: تحديد داخل/خارج نطاق الخليج
# ---------------------------------------------------------------------------

GCC_COUNTRY_CODES = {"SA", "AE", "QA", "KW", "BH", "OM"}

_COUNTRY_CODE_SUFFIX_RE = re.compile(r",\s*([A-Za-z]{2})\s*$")

_COUNTRY_NAME_TO_CODE: dict[str, str] = {
    "saudi arabia": "SA", "ksa": "SA", "kingdom of saudi arabia": "SA",
    "السعودية": "SA", "المملكة العربية السعودية": "SA", "المملكة": "SA",
    "united arab emirates": "AE", "uae": "AE", "الإمارات": "AE", "الامارات": "AE",
    "qatar": "QA", "قطر": "QA",
    "kuwait": "KW", "الكويت": "KW",
    "bahrain": "BH", "البحرين": "BH",
    "oman": "OM", "عُمان": "OM", "عمان": "OM",
}

_CITY_TO_COUNTRY: dict[str, str] = {
    "Riyadh": "SA", "Jeddah": "SA", "Dammam": "SA", "Khobar": "SA", "Dhahran": "SA",
    "Yanbu": "SA", "Jubail": "SA", "Mecca": "SA", "Medina": "SA", "Taif": "SA",
    "Abha": "SA", "Tabuk": "SA", "Najran": "SA",
    "الرياض": "SA", "جدة": "SA", "الدمام": "SA", "الخبر": "SA", "الظهران": "SA",
    "ينبع": "SA", "الجبيل": "SA", "مكة": "SA", "المدينة": "SA", "الطائف": "SA",
    "أبها": "SA", "تبوك": "SA", "نجران": "SA",
    "Dubai": "AE", "Abu Dhabi": "AE", "Doha": "QA", "Manama": "BH",
    "Kuwait City": "KW", "Muscat": "OM",
}

_REMOTE_RE = re.compile(r"\bremote\b", re.IGNORECASE)


def extract_country_code(location_text: str | None, extra_text: str | None = None) -> str | None:
    """يستنتج رمز الدولة (ISO حرفين) من نص الموقع الخام أولاً (رمز ملحق،
    اسم دولة صريح، ثم مدينة معروفة)، ثم من نص إضافي (عنوان/وصف) كملاذ أخير.
    يُرجع أي دولة (ليس الخليج فقط) — الفلترة بالخليج تتم بدالة منفصلة."""
    if location_text:
        m = _COUNTRY_CODE_SUFFIX_RE.search(location_text)
        if m:
            return m.group(1).upper()
        lowered = location_text.lower()
        for name, code in _COUNTRY_NAME_TO_CODE.items():
            if name in lowered or name in location_text:
                return code
        for city, code in _CITY_TO_COUNTRY.items():
            if city in location_text:
                return code
    if extra_text:
        lowered_extra = extra_text.lower()
        for name, code in _COUNTRY_NAME_TO_CODE.items():
            if name in lowered_extra or name in extra_text:
                return code
        for city, code in _CITY_TO_COUNTRY.items():
            if city in extra_text:
                return code
    return None


def compute_region(location_text: str | None, extra_text: str | None = None) -> tuple[str | None, bool]:
    """يرجع (country_code_أو_None, out_of_region). القاعدة المحافظة (مراجعة
    B2 R3/R4): كل دولة غير معروفة (None) أو غير خليجية = خارج النطاق، إلا
    لو ذُكرت كلمة "remote" صراحة مع دولة خليجية بنفس النص ("Remote - Saudi
    Arabia" مثلاً)."""
    code = extract_country_code(location_text, extra_text)
    if code in GCC_COUNTRY_CODES:
        return code, False
    combined = " ".join(filter(None, [location_text, extra_text]))
    if combined and _REMOTE_RE.search(combined):
        remote_code = extract_country_code(combined)
        if remote_code in GCC_COUNTRY_CODES:
            return remote_code, False
    return code, True


# ---------------------------------------------------------------------------
# المهارات (مرتبطة بعوائل taxonomy_local.yaml — قائمة أولية قابلة للتوسع)
# ---------------------------------------------------------------------------

KNOWN_SKILLS = [
    "SAP", "AutoCAD", "Aspen HYSYS", "Aspen Plus", "MATLAB", "Six Sigma",
    "ISO 9001", "ISO 14001", "HAZOP", "PLC", "SCADA", "Primavera P6",
    "Process Safety Management", "Root Cause Analysis", "Lean Manufacturing",
]


def extract_skills(text: str) -> list[str]:
    """يرجع كل المهارات المعروفة المذكورة صراحة بالنص (مطابقة غير حساسة لحالة الأحرف)."""
    if not text:
        return []
    lowered = text.lower()
    found: list[str] = []
    for skill in KNOWN_SKILLS:
        if skill.lower() in lowered and skill not in found:
            found.append(skill)
    return found


# ---------------------------------------------------------------------------
# نوع التقديم المرجّح (يحدد مسار الإرسال لاحقًا بالمرحلة 4)
# ---------------------------------------------------------------------------

def classify_application_type(job_url: str | None, source_type: str | None) -> str:
    """تصنيف أولي لنوع التقديم بناءً على مصدر الوظيفة — يُستخدم لتحديد مسار
    الإرسال لاحقًا (بريد مباشر، أو نموذج خارجي عبر ATS)."""
    ats_form_sources = {
        "greenhouse", "lever", "smartrecruiters", "workable", "recruitee",
        "ashby",
    }
    if source_type in ats_form_sources:
        return "external_form"
    if source_type in {"rss", "alert_mail"}:
        return "email"
    return "unknown"
