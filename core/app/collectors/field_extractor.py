"""
المطبّع الكامل — يستخرج من نص الوظيفة (العنوان + الوصف) الحقول التي يحتاجها
الاستبعاد القاطع بالمرحلة 3 (نفس منطق المرحلة 0 لكن كوحدة قابلة لإعادة
الاستخدام على مستوى قاعدة البيانات بدل موجّه Claude لكل وظيفة):

    سنوات الخبرة المطلوبة، مستوى الأقدمية، شرط الجنسية، التخصص/الشهادة
    المطلوبة، المدن المذكورة، رمز الدولة/داخل-خارج نطاق الخليج، المهارات
    المذكورة، ونوع التقديم المرجّح.

كل دالة هنا Heuristic (كشف بالكلمات المفتاحية/الأنماط) مصمم ليكون "مرشّح أول"
سريع بدون أي تكلفة API — وليس بديلاً نهائيًا عن مراجعة Claude للحالات الحدّية،
تمامًا كما يقضي الدليل بمراجعة عينة يدوية للتحقق من نسبة الدقة (القسم 9،
معيار قبول المرحلة 2: دقة الحقول المستخرجة ≥ 90% على عينة 50 وظيفة).

مراجعة B2 (docs/reports/B2-review.md) R2: أُصلح خلل مطابقة السلسلة الفرعية
بمستوى الأقدمية (كانت "intern" تُطابِق داخل "internal"/"international") —
كل الكلمات المفتاحية هنا الآن تُطابَق بحدود كلمة صريحة (`\\b...\\b`) عبر
تعابير نمطية مُجمَّعة مسبقًا، لا بحث سلسلة فرعية (`in`).

مراجعة B2 R11 (docs/reports/B2-review-2.md): `extract_country_code()` كان
يلجأ لفحص نص إضافي (عنوان+أول 300 حرف من الوصف) كلما لم يطابق location_text
دولة خليجية معروفة — **بلا شرط أن يكون location_text فارغًا أصلًا**. هذا
سمح لذكر عرَضي بالوصف (فقرة "نغطي السعودية والإمارات وقطر..." بوكالات توظيف
عن مناطق عملها العامة، أو لاحقة عنوان "(Saudi Arabia)" تصف "العميل" لا مكان
العمل الفعلي) بتصنيف وظيفة مقرّها الحقيقي سنغافورة/رومانيا كـ"داخل الخليج"
زورًا (17.1% من الصفوف "داخل النطاق" تبيّن تلوّثها هكذا). **الإصلاح: النص
الإضافي (عنوان/وصف) لا يُستخدم إطلاقًا إلا حين location_text فارغًا تمامًا
(None أو سلسلة فارغة)** — وجود location_text ولو لم يُطابق أي دولة خليجية
معروفة يُعتبر إشارة بنيوية كافية بذاتها (مكان غير خليجي)، ولا يجوز لنص وصفي
أن يتجاوزها.
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

# كل مجموعة: (اسم المستوى، قائمة كلمات/عبارات تُطابَق بحدود كلمة كاملة).
# الترتيب مقصود: "intern" قبل "entry" قبل "senior" قبل "manager" قبل "lead" —
# "manager" قبل "lead" عمدًا (تعليق أصلي محفوظ): "lead" تُستخدم غالبًا كفعل
# بوصف الوظيفة ("must lead a team") وليست دائمًا مسمّى وظيفيًا؛ حين يظهر
# "manager"/"director" بنفس النص فهي الإشارة الأصدق لمستوى الأقدمية الفعلي.
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
    """يبني تعبيرًا نمطيًا واحدًا لكل قائمة كلمات، بحدود كلمة صريحة على كل
    عنصر (`\\bكلمة\\b`) — يمنع مطابقة "intern" داخل "internal"/"international"،
    أو "head" داخل "headquarters"، إلخ. الترتيب بالطول تنازليًا احتياطًا
    (لا يؤثر عمليًا لأن التناوب يبحث عن أول تطابق، لكن ممارسة سليمة)."""
    escaped = sorted((re.escape(kw) for kw in keywords), key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(escaped) + r")\b", re.IGNORECASE)


_SENIORITY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (level, _compile_word_boundary(keywords)) for level, keywords in _SENIORITY_BUCKETS
]


def extract_seniority(text: str, title: str | None = None) -> str | None:
    """يرجع أقرب مستوى أقدمية مطابق بحدود كلمة صريحة، أو None إن لم يُذكر
    صراحة (يُفترض mid). إن مُرِّر `title` منفصلًا، يُفحَص أولًا وحده قبل النص
    الكامل — إشارة العنوان أوثق من نص وصف طويل قد يحوي كلمات عامة مضلِّلة
    (مراجعة B2 R2).

    مراجعة B2 R13 (docs/reports/B2-review-2.md): حين لا يطابق العنوان أي
    مستوى (فالعنوان صريح لكن غير مغطّى بالمعجم، مثال: "Vice President,
    Internal Audit")، كان الرجوع للنص الكامل يُعيد أول تطابق بترتيب القائمة
    (intern أولاً) — فإن حوى الوصف كلمة "internship" ضمن نص توضيحي/EEO عام
    ("this is a full-time role, not an internship") بينما يحوي أيضًا إشارة
    أقوى صريحة لمستوى أعلى (senior/manager/lead) بنفس النص، كان "intern"
    يفوز زورًا لمجرد ترتيبه الأول بالقائمة. الإصلاح: بفرع النص الكامل فقط،
    اجمع كل المستويات المطابقة؛ إن كان "intern" الوحيد المطابق أعده كما هو
    (إعلان تدريب فعلي)، وإلا رجّح أقوى إشارة أخرى موجودة (بترتيب القائمة
    الأصلي بعد استبعاد intern) — لا يفوز "intern" أبدًا حين تتعايش معه أي
    إشارة أخرى بنفس النص. فرع العنوان أعلاه غير متأثر (نادرًا ما يحوي العنوان
    القصير أكثر من مستوى واحد فعليًا، ولا حالة R13 تمسّه)."""
    if title:
        for level, pattern in _SENIORITY_PATTERNS:
            if pattern.search(title):
                return level
    if not text:
        return None
    matched = {level for level, pattern in _SENIORITY_PATTERNS if pattern.search(text)}
    if not matched:
        return None
    if matched == {"intern"}:
        return "intern"
    for level, _pattern in _SENIORITY_PATTERNS:
        if level != "intern" and level in matched:
            return level
    return "intern"


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
# الدولة/المنطقة — مراجعة B2 R3/R4، ثم R11: تحديد داخل/خارج نطاق الخليج
# ---------------------------------------------------------------------------

GCC_COUNTRY_CODES = {"SA", "AE", "QA", "KW", "BH", "OM"}

# نمط شائع جدًا بمخرجات Greenhouse/SmartRecruiters/Workable: "City, xx" حيث xx
# رمز دولة ISO حرفين بآخر النص (مثال: "Ras Al-Khaimah, ae"، "Bentonville, us").
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

# اكتشاف إضافي (غير موثَّق صراحةً بـR11 لكنه اكتُشف أثناء كتابة اختبارات
# الانحدار الخاصة به): المطابقة السابقة كانت `name in lowered` — سلسلة فرعية
# بلا حدود كلمة — وهذا يُنتج تطابقًا كاذبًا صريحًا: "romania".find("oman") لا
# يُعيد -1 لأن "oman" سلسلة فرعية حرفية داخل "r-oman-ia"! هذا كان على الأرجح
# السبب الفعلي (لا مجرد رجوع extra_text) وراء المثال الذي رصدته المراجعة
# نفسها (Elastic بموقع "Romania" و country_code="OM") — الخلل يقع في فرع
# location_text مباشرة، لا في فرع extra_text فقط. الإصلاح: كل اسم دولة/مدينة
# يُقارَن الآن بحدود كلمة صريحة (`\b...\b`) عبر أنماط مُجمَّعة مسبقًا (نفس
# أسلوب _SENIORITY_PATTERNS أعلاه)، مرتّبة بالطول تنازليًا حتى تُطابق العبارة
# الأطول أولًا ("kingdom of saudi arabia" قبل "saudi arabia" مثلًا، وإن لم
# يكن يؤثر عمليًا هنا لأن أول تطابق فقط هو المستخدَم دومًا بالحلقة).
_COUNTRY_NAME_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (code, re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE))
    for name, code in sorted(_COUNTRY_NAME_TO_CODE.items(), key=lambda kv: len(kv[0]), reverse=True)
]
_CITY_NAME_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (code, re.compile(r"\b" + re.escape(city) + r"\b", re.IGNORECASE))
    for city, code in sorted(_CITY_TO_COUNTRY.items(), key=lambda kv: len(kv[0]), reverse=True)
]


def extract_country_code(location_text: str | None, extra_text: str | None = None) -> str | None:
    """يستنتج رمز الدولة (ISO حرفين) من نص الموقع الخام (الحقل البنيوي)
    حصرًا حين متوفرًا. مراجعة B2 R11: `extra_text` (عنوان/وصف) لا يُستخدم
    **إلا حين location_text فارغًا تمامًا (None أو سلسلة فارغة)** — إن كان
    location_text موجودًا ولم يُطابق أي دولة/مدينة خليجية معروفة، تُعاد None
    مباشرة (يعني: مكان معروف لكنه غير خليجي) بلا أي محاولة لتجاوزه بذكر
    عرَضي بالوصف. مطابقة أسماء الدول/المدن بحدود كلمة صريحة (`\\b...\\b`) لا
    سلسلة فرعية — تمنع تطابقًا كاذبًا مثل "oman" داخل "Romania". يُرجع أي
    دولة (ليس الخليج فقط عبر الرمز الملحق) — الفلترة بالخليج تتم بدالة
    `compute_region` المنفصلة."""
    if location_text:
        m = _COUNTRY_CODE_SUFFIX_RE.search(location_text)
        if m:
            return m.group(1).upper()
        for code, pattern in _COUNTRY_NAME_PATTERNS:
            if pattern.search(location_text):
                return code
        for code, pattern in _CITY_NAME_PATTERNS:
            if pattern.search(location_text):
                return code
        # location_text موجود لكن لا يطابق أي إشارة خليجية معروفة — إشارة
        # بنيوية كافية بذاتها (R11): لا نلجأ لـextra_text إطلاقًا.
        return None
    if extra_text:
        for code, pattern in _COUNTRY_NAME_PATTERNS:
            if pattern.search(extra_text):
                return code
        for code, pattern in _CITY_NAME_PATTERNS:
            if pattern.search(extra_text):
                return code
    return None


def compute_region(location_text: str | None, extra_text: str | None = None) -> tuple[str | None, bool]:
    """يرجع (country_code_أو_None, out_of_region). القاعدة المحافظة (مراجعة
    B2 R3/R4، مُحكَمة أكثر بـR11): كل دولة غير معروفة (None) أو غير خليجية
    = خارج النطاق. الاستثناء الوحيد ("Remote - Saudi Arabia" مثلًا) يُفحَص
    ضمن location_text نفسه فقط حين متوفرًا — لا ضمن الوصف — حتى لا تُفعِّل
    كلمة "remote" بالوصف مع ذكر عرَضي لدولة خليجية بفقرة أخرى الاستثناءَ
    زورًا. extra_text (عنوان/وصف) لا يُستخدم بأي مسار هنا إلا حين
    location_text فارغًا تمامًا (يشمل مسار "remote" أيضًا)."""
    code = extract_country_code(location_text, extra_text)
    if code in GCC_COUNTRY_CODES:
        return code, False

    if location_text:
        if _REMOTE_RE.search(location_text):
            remote_code = extract_country_code(location_text)
            if remote_code in GCC_COUNTRY_CODES:
                return remote_code, False
        return code, True

    # location_text فارغ تمامًا — الملاذ الأخير الوحيد لاستخدام extra_text.
    if extra_text and _REMOTE_RE.search(extra_text):
        remote_code = extract_country_code(None, extra_text)
        if remote_code in GCC_COUNTRY_CODES:
            return remote_code, False
    return code, True


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
