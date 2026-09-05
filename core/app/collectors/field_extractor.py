"""
المطبّع الكامل — يستخرج من نص الوظيفة (العنوان + الوصف) الحقول التي يحتاجها
الاستبعاد القاطع بالمرحلة 3 (نفس منطق المرحلة 0 لكن كوحدة قابلة لإعادة
الاستخدام على مستوى قاعدة البيانات بدل موجّه Claude لكل وظيفة):

    سنوات الخبرة المطلوبة، مستوى الأقدمية، شرط الجنسية، التخصص/الشهادة
    المطلوبة، المدن المذكورة، المهارات المذكورة، ونوع التقديم المرجّح.

كل دالة هنا Heuristic (كشف بالكلمات المفتاحية/الأنماط) مصمم ليكون "مرشّح أول"
سريع بدون أي تكلفة API — وليس بديلاً نهائيًا عن مراجعة Claude للحالات الحدّية،
تمامًا كما يقضي الدليل بمراجعة عينة يدوية للتحقق من نسبة الدقة (القسم 9،
معيار قبول المرحلة 2: دقة الحقول المستخرجة ≥ 90% على عينة 50 وظيفة).
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
# مستوى الأقدمية
# ---------------------------------------------------------------------------

_SENIORITY_KEYWORDS: list[tuple[str, list[str]]] = [
    ("intern", ["intern", "internship", "trainee", "متدرب", "تدريب"]),
    ("entry", ["entry level", "junior", "fresh graduate", "حديث التخرج", "مبتدئ"]),
    ("senior", ["senior", "sr.", "خبير", "أول"]),
    ("lead", ["lead ", "principal", "قائد فريق"]),
    ("manager", ["manager", "head of", "director", "مدير", "رئيس قسم"]),
]


def extract_seniority(text: str) -> str | None:
    """يرجع أقرب مستوى أقدمية مطابق، أو None إن لم يُذكر صراحة (يُفترض mid)."""
    if not text:
        return None
    lowered = text.lower()
    for level, keywords in _SENIORITY_KEYWORDS:
        for kw in keywords:
            if kw in lowered:
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
# المهارات (مرتبطة بعائلات taxonomy_local.yaml — قائمة أولية قابلة للتوسيع)
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
