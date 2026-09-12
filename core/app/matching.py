"""
Masar Core — محرك المطابقة (B3، الدليل §3.6/§3.7).

وحدة **نقية بالكامل بلا اتصال قاعدة بيانات ولا شبكة** عمدًا — بنفس فلسفة
core/app/collectors/normalizer.py وfield_extractor.py: منطق حتمي قابل
للاختبار مباشرة (core/tests/test_matching.py، بلا DB)، يستدعيه
core/app/planner.py (يجلب الصفوف من jobs/customers/profiles ثم يمرّرها هنا)
وcore/app/customers_api.py (نقطة الشرح /admin/matching/explain).

**الاستبعاد القاطع** (check_disqualifiers): يُعيد قائمة أسباب — قائمة فارغة
يعني الوظيفة مؤهّلة. الفحوصات المتعلقة بحالة قاعدة البيانات (تبريد الشركة
60 يومًا، سقف 3 عملاء/أسبوع للشركة) تُمرّر كقيم منطقية جاهزة من المستدعي
(cooldown_active/weekly_cap_reached) حتى تبقى هذه الوحدة نفسها بلا DB.

**الدرجة** (compute_score): 0.35 مسمى + 0.30 مهارات + 0.15 أقدمية +
0.10 موقع + 0.05 حداثة + 0.05 جودة مصدر — كل دالة فرعية 0..1 حتمية بسيطة
(القسم §3.7 من الدليل).

**الطبقات**: A ≥0.75، B ≥0.55، C ≥0.40 (ضمن عائلات/مدن العميل المختارة)،
C2 = نفس عتبة C لكن عبر تمريرة توسيع العائلة (widened_family=True — العميل
لم يخترها لكنها استُخدمت لأن C/B/A لم تكفِ الهدف اليومي)، D لما دون 0.40
(لا تُخطّط ولا تُرسَل أبدًا).

ملاحظة نطاق متعمّدة (تُوثّق أيضًا بـdocs/reports/B3-executor.md): جدول
`jobs` الحالي لا يحمل حقل "الشهادة/التخصص المطلوب" (degree_req) — لم يُستخرج
بعد بـfield_extractor.py (لا يجوز لهذا الملف تعديل core/app/collectors/*). دالة
الاستبعاد القاطع تقبل `job.degree_req` اختياريًا للتوافق المستقبلي، لكنها
لا تُفعّل عمليًا الآن (حقل غير مستخرج = لا استبعاد، بنفس فلسفة الدليل §3.5:
"الحقول غير المستخرجة تبقى NULL ولا تسبب استبعادًا").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache

from app.collectors.normalizer import normalize_text

# ---------------------------------------------------------------------------
# ثوابت
# ---------------------------------------------------------------------------

# مراجعة B2 R12 (مكرّرة هنا عمدًا كنسخة محلية — نفس الاسم بـdiscovery.py
# وdiscovery_api.py — حتى تبقى matching.py مستقلة بلا استيراد من discovery.py):
# عائلات مُستبعدة عمدًا من المطابقة إطلاقًا، حتى بتمريرة التوسيع.
# مراجعة B2b: taxonomy_local.yaml أعاد تسمية sales_excluded → out_of_scope؛
# الاسمان معًا هنا للتوافق الرجعي مع أي صفّ قديم لم يُعِد reclassify تصنيفه.
EXCLUDED_FAMILY_NAMES = {"out_of_scope", "sales_excluded"}

WEIGHTS: dict[str, float] = {
    "title": 0.35,
    "skills": 0.30,
    "seniority": 0.15,
    "location": 0.10,
    "recency": 0.05,
    "source_quality": 0.05,
}

TIER_A_THRESHOLD = 0.75
TIER_B_THRESHOLD = 0.55
TIER_C_THRESHOLD = 0.40

# سنوات الخبرة: نسمح بهامش سنة واحدة فوق سنوات العميل الفعلية (تعليمات B3 —
# الدليل الأصلي القسم 3.6 يذكر هامش سنتين؛ B3 تُشدّد الهامش لسنة واحدة عمدًا
# — موثّق بتقرير B3-executor.md).
YEARS_SLACK = 1

# مستويات الأقدمية — نفس الأسماء الخمسة التي يُنتجها
# core/app/collectors/field_extractor.py:extract_seniority() لحقل jobs.seniority
# (intern/entry/senior/manager/lead)، بإضافة "mid" كافتراض داخلي محايد حين
# لا تُذكر الأقدمية صراحة لا بالوظيفة ولا بالملف الشخصي.
SENIORITY_RANK: dict[str, int] = {
    "intern": 0,
    "entry": 1,
    "mid": 2,
    "senior": 3,
    "manager": 4,
    "lead": 5,
}

# فرق الرتب الذي يُسبّب استبعادًا قاطعًا (لا مجرد خصم بالدرجة):
#   الوظيفة أعلى بكثير من الملف (مثال: manager/director لملف junior) —
#   الدليل الأصلي §3.6 البند 2: "seniority >= 5 (manager+) AND years < 6".
SENIORITY_OVER_DISQUALIFY_DIFF = 2
#   العكس (الملف أعلى بكثير من الوظيفة — مثال: خبرة 15 سنة لوظيفة تدريب) —
#   بند "vice versa" بتكليف B3 صراحة.
SENIORITY_UNDER_DISQUALIFY_DIFF = 3

# مرادفات مختصرات شائعة بمسميات الوظائف (طبقة معجم خفيفة، بديل مؤقت لمعجم
# ESCO الذي لم يُحمّل بعد بالمستودع — data/esco/*.csv غير موجود حتى تاريخ
# B3). كل مجموعة تُعامَل كمترادفات متبادلة عند توسيع التوكنات لحساب Jaccard.
TITLE_SYNONYM_GROUPS: list[set[str]] = [
    {"qa", "quality", "assurance"},
    {"qc", "quality", "control"},
    {"hse", "health", "safety", "environment"},
    {"hr", "human", "resources"},
    {"pm", "project", "manager", "management"},
    {"it", "information", "technology"},
    {"qhse", "quality", "health", "safety", "environment"},
    {"eng", "engineer", "engineering", "مهندس", "هندسة", "هندسي"},
    {"sr", "senior", "أول", "خبير"},
    {"jr", "junior", "مبتدئ"},
    {"mgr", "manager", "مدير"},
    {"tech", "technician", "فني"},
    {"supervisor", "مشرف"},
    {"coordinator", "منسق"},
    {"specialist", "أخصائي"},
    {"operator", "مشغل"},
    {"maintenance", "صيانة"},
    {"process", "عمليات"},
    {"chemical", "كيميائي", "كيميائية"},
    {"quality", "جودة"},
]

# وكالات توظيف معروفة (source_quality أدنى — الدليل §3.7: 0.3 وكالة توظيف)
# — قائمة كلمات دلالية تُطابَق داخل اسم الشركة (لا قائمة شركات حصرية، لأن
# جدول companies الحالي لا يحمل عمود "نوع الشركة" بعد).
AGENCY_NAME_HINTS = [
    "talent", "manpower", "recruit", "recruitment", "staffing", "hiring",
    "consultancy", "consultants", "hr solutions", "employment agency",
]

# مصادر تُعتبر عادةً صفحة توظيف الشركة نفسها مباشرة (ATS خاص بالشركة) —
# نفس مفاتيح core/app/discovery.py:DISPATCH — جودة مصدر 1.0 حين لا يوجد
# مؤشّر اسم وكالة بنفس الوقت. لا تزال تُستخدم بـscore_source_quality (دالة
# تشخيصية مستقلة تخدم /admin/matching/explain) رغم أن apply_mode='external_form'
# أصبح يُستبعَد قطعيًا بـcheck_disqualifiers أدناه (B12.5) — القيمة هنا تبقى
# مفيدة لعرض "لو كان بالإمكان إرساله لكانت جودته كذا" بشاشة التشخيص.
DIRECT_EMPLOYER_APPLY_MODES = {"external_form"}

# B12.5 (راجع claude/masar_build_brief_v4_2026-09-12.md): apply_mode='external_form'
# يعني أن التقديم يتطلّب تعبئة نموذج على موقع الشركة/ATS، لا مجرّد إرسال
# بريد — مسار غير قابل للتنفيذ إطلاقًا بمنصّة تُرسل نيابةً عن العميل من
# بريده فقط. استبعادها هنا (لا فقط تخفيض درجتها) يمنع planner.py من إهدار
# حصة الهدف اليومي على فرص لن تُرسَل أبدًا فعليًا (send_builder.py يكتشف
# غياب apply_email لاحقًا ويُعلّمها 'skipped' — استبعاد مبكر هنا أرخص
# ويُبقي إحصاءات "المخطَّط اليوم" صادقة). التفريغ الكامل (حذف
# DIRECT_EMPLOYER_APPLY_MODES/تعديل score_source_quality) غير مطلوب — تلك
# دالة تشخيصية مستقلة لا تتحكم بالاستبعاد.
EXTERNAL_FORM_APPLY_MODE = "external_form"

# B12.5 (تمريرة التوسيع widened_family=True فقط): تشترط حدًا أدنى لدرجة
# تطابق المسمى الوظيفي (score_title) حتى لا تُقترَح على العميل وظيفة بعائلة
# لم يخترها أصلًا بمسمى لا علاقة له فعليًا بخلفيته (التوسيع مُصمَّم ليلتقط
# مسميات قريبة عبر عائلات مجاورة، لا أي وظيفة بأي مسمى لمجرد إكمال العدد).
WIDENED_MIN_TITLE_SCORE = 0.15

_TOKEN_RE = re.compile(r"[a-z0-9؀-ۿ]+")


# ---------------------------------------------------------------------------
# هياكل بيانات الدخل/الخرج
# ---------------------------------------------------------------------------


@dataclass
class CustomerProfile:
    """تمثيل مبسّط لصف customers+profiles اللازم للمطابقة فقط (ليس كل
    الأعمدة) — planner.py وcustomers_api.py يبنيانه من صفوف SQL فعلية."""

    customer_id: int
    years_exp: float | None
    seniority: str | None  # أحد مفاتيح SENIORITY_RANK أو None
    nationality_saudi: bool
    families: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)
    titles: list[dict] = field(default_factory=list)  # [{"title": str, "weight": float}]
    skills: list[str] = field(default_factory=list)
    degree_family: list[str] = field(default_factory=list)  # للتوافق المستقبلي (§الملاحظة أعلاه)


@dataclass
class JobCandidate:
    """تمثيل مبسّط لصف jobs اللازم للمطابقة فقط."""

    job_id: int
    title: str
    family: str | None
    years_min: int | None
    seniority: str | None
    saudi_only: bool
    city: str | None
    skills: list[str]
    company_name: str | None
    apply_mode: str | None
    first_seen_at: datetime | None
    out_of_region: bool = False
    degree_req: list[str] | None = None  # غير مستخدَم فعليًا اليوم — راجع رأس الملف


@dataclass
class MatchResult:
    job_id: int
    disqualified: bool
    reasons: list[str]
    score: float
    score_parts: dict[str, float]
    tier: str  # 'A'|'B'|'C'|'C2'|'D'
    widened_family: bool = False


# ---------------------------------------------------------------------------
# أدوات تطبيع/تشابه نصوص (مبنية فوق normalize_text الموجودة أصلًا)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=8192)
def _tokenize_cached(text: str) -> frozenset[str]:
    normalized = normalize_text(text)
    if not normalized:
        return frozenset()
    tokens = set(_TOKEN_RE.findall(normalized))
    return frozenset(expand_synonyms(tokens))


def tokenize(text: str | None) -> set[str]:
    """يحوّل نصًا لمجموعة توكنات مطبّعة (يعيد استخدام normalize_text نفسها
    المستخدمة بالفعل بمحرّك الاكتشاف — توحيد عربي + أحرف صغيرة + إزالة
    ترقيم)، ثم يُوسّعها بمرادفات TITLE_SYNONYM_GROUPS.

    B6 (تصليب الحمل، perf-only — matching.py يبقى نقيًا بلا DB): مُخبّأ
    (`lru_cache`) خلف `_tokenize_cached` — planner.py يستدعي هذه الدالة
    مرتين لكل زوج (مرشّح وظيفة × مسمى بملف عميل) داخل حلقة `_select_for_customer`
    التي تعالج آلاف الأزواج لكل عميل بمعيار قبول B3 (1,500×3,000)؛ نفس نص
    العنوان يتكرر آلاف المرّات (نفس مسمّيات الوظائف الشائعة، ونفس عناوين
    ملف نفس العميل عبر كل مرشّح) فالتخبئة توفّر إعادة حساب normalize_text +
    تجزئة regex + توسيع مرادفات بالكامل. يرجع نسخة `set()` جديدة قابلة
    للتعديل من طرف المستدعي (نفس التوقيع القديم بالضبط) — الكائن المُخبّأ
    نفسه (`frozenset`) غير قابل للتعديل عمدًا فلا يتسرّب أي تعديل بين
    استدعاءات مختلفة تتشارك نفس مدخل الكاش."""
    if text is None:
        return set()
    return set(_tokenize_cached(text))


def expand_synonyms(tokens: set[str]) -> set[str]:
    expanded = set(tokens)
    for group in TITLE_SYNONYM_GROUPS:
        if tokens & group:
            expanded |= group
    return expanded


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    union = len(a | b)
    return inter / union if union else 0.0


def normalized_city_set(cities: list[str]) -> set[str]:
    return {normalize_text(c) for c in cities if c}


def city_allowed(job_city: str | None, customer_cities: list[str]) -> bool:
    """موقع الوظيفة غير المعروف (None) دائمًا مسموح (يُخصَم بالدرجة فقط —
    §3.7: 0.3 موقع غير محدد). موقع معروف يجب أن يكون ضمن مدن العميل
    المختارة تحديدًا — لا توسيع جغرافي إطلاقًا (تكليف B3: التوسيع الوحيد
    المدعوم هو توسيع العائلة C2، لا المدينة)."""
    if not job_city:
        return True
    if not customer_cities:
        return False
    return normalize_text(job_city) in normalized_city_set(customer_cities)


# ---------------------------------------------------------------------------
# الأقدمية
# ---------------------------------------------------------------------------


def derive_profile_seniority(profile: CustomerProfile) -> int:
    """يرجّع رتبة الأقدمية المُشتقّة للملف الشخصي (SENIORITY_RANK). يُفضّل
    profile.seniority الصريح (إن وُجد ويطابق أحد المفاتيح المعروفة)، وإلا
    يُشتق من سنوات الخبرة بعتبات معقولة مطابقة لتدرّج الدليل §3.5."""
    if profile.seniority and profile.seniority in SENIORITY_RANK:
        return SENIORITY_RANK[profile.seniority]
    years = profile.years_exp
    if years is None:
        return SENIORITY_RANK["mid"]
    if years < 1:
        return SENIORITY_RANK["intern"]
    if years < 3:
        return SENIORITY_RANK["entry"]
    if years < 6:
        return SENIORITY_RANK["mid"]
    if years < 10:
        return SENIORITY_RANK["senior"]
    if years < 15:
        return SENIORITY_RANK["manager"]
    return SENIORITY_RANK["lead"]


def job_seniority_rank(job_seniority: str | None) -> int | None:
    if job_seniority is None:
        return None
    return SENIORITY_RANK.get(job_seniority)


# ---------------------------------------------------------------------------
# الاستبعاد القاطع
# ---------------------------------------------------------------------------


def check_disqualifiers(
    profile: CustomerProfile,
    job: JobCandidate,
    *,
    widened_family: bool = False,
    cooldown_active: bool = False,
    weekly_cap_reached: bool = False,
) -> list[str]:
    """يرجّع قائمة أسباب الاستبعاد القاطع (فارغة = مؤهّلة). ترتيب الفحوصات
    غير مهم دلاليًا — كل الأسباب المطابقة تُجمَع (لا نتوقف عند أول سبب) حتى
    تخدم نقطة الشرح /admin/matching/explain تفسيرًا كاملًا للمراجع."""
    reasons: list[str] = []

    if job.out_of_region:
        reasons.append("out_of_region")

    # B12.5: نموذج خارجي (لا بريد) — استبعاد قطعي، راجع تعليق
    # EXTERNAL_FORM_APPLY_MODE أعلى الملف.
    if job.apply_mode == EXTERNAL_FORM_APPLY_MODE:
        reasons.append("apply_mode_external_form")

    if job.family in EXCLUDED_FAMILY_NAMES:
        reasons.append("family_excluded")
    elif not widened_family:
        if job.family is None or job.family not in profile.families:
            reasons.append("family_not_selected")

    # B12.5: تمريرة التوسيع فقط — مسمى ضعيف الصلة جدًا حتى مع عائلة موسّعة.
    if widened_family and score_title(profile, job) < WIDENED_MIN_TITLE_SCORE:
        reasons.append("widened_title_too_weak")

    if not city_allowed(job.city, profile.cities):
        reasons.append("city_not_selected")

    if job.years_min is not None and profile.years_exp is not None:
        if job.years_min > profile.years_exp + YEARS_SLACK:
            reasons.append("years_exceeded")

    profile_rank = derive_profile_seniority(profile)
    job_rank = job_seniority_rank(job.seniority)
    if job_rank is not None:
        diff = job_rank - profile_rank
        if diff >= SENIORITY_OVER_DISQUALIFY_DIFF:
            reasons.append("seniority_too_senior")
        elif -diff >= SENIORITY_UNDER_DISQUALIFY_DIFF:
            reasons.append("seniority_too_junior")

    if job.saudi_only and not profile.nationality_saudi:
        reasons.append("saudi_only_mismatch")

    if job.degree_req:
        if profile.degree_family and not (set(job.degree_req) & set(profile.degree_family)):
            reasons.append("degree_mismatch")

    if cooldown_active:
        reasons.append("company_cooldown")

    if weekly_cap_reached:
        reasons.append("weekly_company_cap")

    return reasons


# ---------------------------------------------------------------------------
# الدرجة — كل دالة فرعية 0..1 (الدليل §3.7)
# ---------------------------------------------------------------------------


def score_title(profile: CustomerProfile, job: JobCandidate) -> float:
    job_tokens = tokenize(job.title)
    best = 0.0
    for entry in profile.titles:
        if isinstance(entry, dict):
            title = entry.get("title") or ""
            weight = entry.get("weight")
            weight = float(weight) if isinstance(weight, (int, float)) else 1.0
        else:
            title = str(entry)
            weight = 1.0
        weight = max(0.0, min(1.0, weight))
        overlap = jaccard(job_tokens, tokenize(title))
        best = max(best, weight * overlap)
    if best == 0.0 and job.family and job.family in profile.families:
        best = 0.5
    return min(best, 1.0)


def score_skills(profile: CustomerProfile, job: JobCandidate) -> float:
    if not job.skills:
        return 0.5  # الدليل §3.7: لا مهارات مستخرجة بالإعلان → 0.5
    job_skills = {normalize_text(s) for s in job.skills if s}
    profile_skills = {normalize_text(s) for s in profile.skills if s}
    return jaccard(job_skills, profile_skills)


def score_seniority(profile: CustomerProfile, job: JobCandidate) -> float:
    job_rank = job_seniority_rank(job.seniority)
    if job_rank is None:
        return 0.6  # الدليل §3.7: 0.6 إن كانت مجهولة
    profile_rank = derive_profile_seniority(profile)
    diff = abs(job_rank - profile_rank)
    return max(0.0, 1.0 - 0.25 * diff)


def score_location(profile: CustomerProfile, job: JobCandidate) -> float:
    if not job.city:
        return 0.3  # موقع غير محدد
    if normalize_text(job.city) in normalized_city_set(profile.cities):
        return 1.0
    return 0.0  # لن تصل هذه الحالة عمليًا لوظيفة غير مستبعدة (city_allowed تستبعدها أولًا)


def score_recency(job: JobCandidate, now: datetime) -> float:
    if job.first_seen_at is None:
        return 0.5
    seen = job.first_seen_at
    if seen.tzinfo is None and now.tzinfo is not None:
        seen = seen.replace(tzinfo=now.tzinfo)
    elif seen.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=seen.tzinfo)
    days = (now - seen).total_seconds() / 86400.0
    if days <= 3:
        return 1.0
    if days >= 14:
        return 0.5
    # انحدار خطي بين (3 أيام → 1.0) و(14 يومًا → 0.5)
    return 1.0 - (days - 3) / (14 - 3) * 0.5


def score_source_quality(job: JobCandidate) -> float:
    name = normalize_text(job.company_name)
    if name and any(hint in name for hint in AGENCY_NAME_HINTS):
        return 0.3
    if job.apply_mode in DIRECT_EMPLOYER_APPLY_MODES:
        return 1.0
    return 0.6


def compute_score(profile: CustomerProfile, job: JobCandidate, now: datetime) -> dict[str, float]:
    parts = {
        "title": score_title(profile, job),
        "skills": score_skills(profile, job),
        "seniority": score_seniority(profile, job),
        "location": score_location(profile, job),
        "recency": score_recency(job, now),
        "source_quality": score_source_quality(job),
    }
    total = sum(parts[k] * WEIGHTS[k] for k in WEIGHTS)
    parts["total"] = round(total, 4)
    return parts


# ---------------------------------------------------------------------------
# الطبقات
# ---------------------------------------------------------------------------


def determine_tier(score: float, *, widened_family: bool) -> str:
    if score >= TIER_A_THRESHOLD:
        return "A"
    if score >= TIER_B_THRESHOLD:
        return "B"
    if score >= TIER_C_THRESHOLD:
        return "C2" if widened_family else "C"
    return "D"


# ---------------------------------------------------------------------------
# نقطة الدخول المجمّعة
# ---------------------------------------------------------------------------


def evaluate(
    profile: CustomerProfile,
    job: JobCandidate,
    *,
    now: datetime,
    widened_family: bool = False,
    cooldown_active: bool = False,
    weekly_cap_reached: bool = False,
) -> MatchResult:
    reasons = check_disqualifiers(
        profile,
        job,
        widened_family=widened_family,
        cooldown_active=cooldown_active,
        weekly_cap_reached=weekly_cap_reached,
    )
    parts = compute_score(profile, job, now)
    score = parts["total"]
    disqualified = bool(reasons)
    tier = "D" if disqualified else determine_tier(score, widened_family=widened_family)
    return MatchResult(
        job_id=job.job_id,
        disqualified=disqualified,
        reasons=reasons,
        score=score,
        score_parts=parts,
        tier=tier,
        widened_family=widened_family,
    )
