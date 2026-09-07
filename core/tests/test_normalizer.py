"""
اختبارات المطبّع (core/app/collectors/normalizer.py) والمستخرج
(core/app/collectors/field_extractor.py) — 20 عيّنة عربية/إنجليزية حقيقية
الصياغة (مقتبسة من نمط إعلانات وظائف فعلي) حسب معيار قبول B2 بـPLAN.md:
"عينة 50 وظيفة بحقول سنوات/أقدمية/مدينة صحيحة ≥ 90%".

مراجعة B2 (ACCEPT-WITH-FIXES):
    R2/R7: اختبارات انحدار صريحة لمطابقة الأقدمية بحدود كلمة (لا سلسلة فرعية)
        — الحالات الخمس التي حدّدتها المراجعة تحديدًا.
    R5: dedup_key لم يعد يقبل external_key — الصيغة الجديدة تعتمد على
        (شركة، مسمى، مدينة، مسار apply_url بلا سلسلة استعلام)؛ استُبدل
        اختباري external_key القديمين باختبارات تعكس السلوك الجديد.

يُشغّل محليًا بـpytest قبل كل commit (الدليل التنفيذي، القسم "الاختبار قبل
DONE"). لا يحتاج قاعدة بيانات ولا شبكة — دوال نقية فقط.

تشغيل: cd core && python -m pytest tests/test_normalizer.py -v
"""
from __future__ import annotations

import pytest

from app.collectors.field_extractor import (
    extract_cities,
    extract_seniority,
    extract_skills,
    extract_years_required,
    is_saudi_only,
)
from app.collectors.normalizer import company_key, dedup_key, normalize_text


# ---------------------------------------------------------------------------
# 20 عيّنة: (نص الإعلان، سنوات_دنيا_متوقعة, أقدمية_متوقعة, سعودي_فقط_متوقع,
#            مدينة_متوقعة_أو_None, مهارة_يجب_أن_تُستخرج_أو_None)
# ---------------------------------------------------------------------------

FIXTURES: list[tuple[str, int | None, str | None, bool, str | None, str | None]] = [
    # عربي
    ("مطلوب مهندس عمليات كيميائية بخبرة لا تقل عن 5 سنوات في الرياض، يشترط الجنسية السعودية",
     5, None, True, "الرياض", None),
    ("مهندس جودة أول - خبرة 8 سنوات - مصنع في جدة - يفضل من لديه شهادة ISO 9001",
     8, "senior", False, "جدة", "ISO 9001"),
    ("فرصة تدريب (متدرب) لخريجي الهندسة الكيميائية حديثًا في الدمام",
     None, "intern", False, "الدمام", None),
    ("مطلوب مدير مشروع بخبرة 12 سنة في الجبيل، إدارة فرق متعددة",
     12, "manager", False, "الجبيل", None),
    ("مهندس سلامة (HSE) - خبرة من 3 إلى 6 سنوات - موقع العمل ينبع",
     3, None, False, "ينبع", None),
    ("أخصائي مختبر كيميائي - مبتدئ - لا يشترط خبرة سابقة - الخبر",
     None, "entry", False, "الخبر", None),
    ("مهندس صيانة ميكانيكية - خبرة لا تقل عن 4 سنوات - يشترط إجادة PLC وSCADA - الظهران",
     4, None, False, "الظهران", "PLC"),
    ("رئيس قسم الجودة - خبرة أكثر من 15 سنة - مكة المكرمة - سعوديين فقط",
     15, "manager", True, "مكة", None),
    ("مهندس عمليات - خبرة 6 سنوات - إجادة Aspen HYSYS - مطلوب في تبوك",
     6, None, False, "تبوك", "Aspen HYSYS"),
    ("قائد فريق الصيانة - خبرة 10 سنوات - أبها - شهادة Six Sigma ميزة إضافية",
     10, "lead", False, "أبها", "Six Sigma"),
    # إنجليزي
    ("Process Engineer required with 5+ years of experience in Riyadh, Saudi nationals only",
     5, None, True, "Riyadh", None),
    ("Senior Quality Engineer - 7-10 years experience - Jeddah plant - ISO 14001 knowledge required",
     7, "senior", False, "Jeddah", "ISO 14001"),
    ("Internship program for fresh graduate chemical engineers - Dammam - no prior experience required",
     None, "intern", False, "Dammam", None),
    ("Project Manager needed - minimum 12 years experience - Jubail - must lead multidisciplinary teams",
     12, "manager", False, "Jubail", None),
    ("HSE Engineer - 3 to 6 years experience - Yanbu site - HAZOP certification preferred",
     3, None, False, "Yanbu", "HAZOP"),
    ("Entry level lab chemist - Khobar - no experience necessary, fresh graduates welcome",
     None, "entry", False, "Khobar", None),
    ("Maintenance Engineer - 4+ years experience - PLC and SCADA proficiency required - Dhahran",
     4, None, False, "Dhahran", "PLC"),
    ("Head of Quality Department - 15+ years experience - Mecca - Saudi nationals only",
     15, "manager", True, "Mecca", None),
    ("Process Engineer - 6 years experience - Aspen Plus proficiency - Tabuk based role",
     6, None, False, "Tabuk", "Aspen Plus"),
    ("Lead Maintenance Engineer - 10 years experience - Abha - Primavera P6 knowledge a plus",
     10, "lead", False, "Abha", "Primavera P6"),
]


@pytest.mark.parametrize(
    "text,expected_years,expected_seniority,expected_saudi_only,expected_city,expected_skill",
    FIXTURES,
)
def test_field_extraction(
    text: str,
    expected_years: int | None,
    expected_seniority: str | None,
    expected_saudi_only: bool,
    expected_city: str | None,
    expected_skill: str | None,
) -> None:
    years_min, _years_max = extract_years_required(text)
    assert years_min == expected_years, f"سنوات: {text!r} → {years_min} (متوقع {expected_years})"

    seniority = extract_seniority(text)
    assert seniority == expected_seniority, f"أقدمية: {text!r} → {seniority} (متوقع {expected_seniority})"

    assert is_saudi_only(text) == expected_saudi_only, f"سعودي فقط: {text!r}"

    if expected_city is not None:
        cities = extract_cities(text)
        assert expected_city in cities, f"مدينة: {text!r} → {cities} (متوقع {expected_city} ضمنها)"

    if expected_skill is not None:
        skills = extract_skills(text)
        assert expected_skill in skills, f"مهارة: {text!r} → {skills} (متوقع {expected_skill} ضمنها)"


def test_years_range_takes_lower_bound() -> None:
    years_min, years_max = extract_years_required("خبرة من 3 إلى 6 سنوات")
    assert years_min == 3
    assert years_max == 6


def test_no_years_mentioned_returns_none() -> None:
    years_min, years_max = extract_years_required("مطلوب مهندس عمليات - بدون ذكر سنوات الخبرة")
    assert years_min is None
    assert years_max is None


# ---------------------------------------------------------------------------
# مراجعة B2 R2/R7 — انحدار مطابقة الأقدمية بحدود كلمة صريحة (لا سلسلة فرعية)
#
# الحالات الخمس المحدَّدة صراحةً بتقرير المراجعة (docs/reports/B2-review.md):
# كانت "intern" تُطابق داخل "internal"/"international" بمطابقة السلسلة
# الفرعية القديمة — الحل: تعابير نمطية بحدود كلمة (\b...\b) مُجمَّعة مسبقًا.
# ---------------------------------------------------------------------------

SENIORITY_REGRESSION_CASES: list[tuple[str, str | None]] = [
    ("International Sales Manager", "manager"),
    ("Internal Auditor", None),
    ("Senior Internal Comms", "senior"),
    ("Intern - Process Engineering", "intern"),
    ("Graduate Trainee", "intern"),
]


@pytest.mark.parametrize("title,expected_seniority", SENIORITY_REGRESSION_CASES)
def test_seniority_word_boundary_regression(title: str, expected_seniority: str | None) -> None:
    """مراجعة B2 R2/R7: extract_seniority(text, title=...) — التمرير عبر
    title صراحةً (كما يفعل discovery.py فعليًا) لضمان تغطية مسار الفحص
    الأول (العنوان) قبل الرجوع للنص الكامل."""
    result = extract_seniority(title, title=title)
    assert result == expected_seniority, (
        f"أقدمية (R7): {title!r} → {result} (متوقع {expected_seniority}) — "
        "احتمال مطابقة سلسلة فرعية بدل حدود كلمة"
    )


def test_seniority_substring_false_positive_regression_within_longer_text() -> None:
    """التأكد أن 'intern' لا يُطابق داخل نص أطول يحتوي 'international'/
    'internal' حتى حين لا تُمرَّر title (المسار الاحتياطي على النص الكامل)."""
    text = "We are hiring for our International Sales division — Internal Auditor role based in Riyadh"
    assert extract_seniority(text) != "intern"


# ---------------------------------------------------------------------------
# normalize_text / company_key / dedup_key
# ---------------------------------------------------------------------------


def test_normalize_text_unifies_arabic_letter_variants() -> None:
    # أ/إ/آ→ا، ة→ه، ى→ي — "شركة" vs "شركه"، "أرامكو" vs "ارامكو"
    assert normalize_text("شركة") == normalize_text("شركه")
    assert normalize_text("أرامكو") == normalize_text("ارامكو")
    assert normalize_text("مستشفى") == normalize_text("مستشفي")


def test_normalize_text_strips_punctuation_and_case() -> None:
    assert normalize_text("Process Engineer, Sr.") == normalize_text("process engineer sr")


def test_company_key_strips_common_suffixes() -> None:
    assert company_key("Zeeco Inc") == company_key("Zeeco")
    assert company_key("شركة أرامكو السعودية") == company_key("ارامكو السعودية")


def test_dedup_key_same_job_same_key_regardless_of_formatting() -> None:
    key_a = dedup_key("Zeeco Inc", "Process Engineer, Sr.", "Dammam")
    key_b = dedup_key("Zeeco", "process engineer sr", "dammam")
    assert key_a == key_b


def test_dedup_key_different_city_different_key() -> None:
    key_riyadh = dedup_key("Zeeco", "Process Engineer", "Riyadh")
    key_dammam = dedup_key("Zeeco", "Process Engineer", "Dammam")
    assert key_riyadh != key_dammam


# --- مراجعة B2 R5: dedup_key الجديد (apply_url بدل external_key) ---


def test_dedup_key_ignores_apply_url_query_string() -> None:
    """اختلاف معاملات تتبّع (?utm_source=...) بين جلبتين لنفس الرابط لا يجب
    أن يُنتج مفتاحًا مختلفًا — نأخذ مسار الرابط فقط."""
    key_a = dedup_key("Zeeco", "Process Engineer", "Dammam", "https://x.com/jobs/123?utm_source=linkedin")
    key_b = dedup_key("Zeeco", "Process Engineer", "Dammam", "https://x.com/jobs/123?utm_source=twitter&ref=x")
    assert key_a == key_b


def test_dedup_key_different_apply_url_path_different_key() -> None:
    """نفس (شركة، مسمى، مدينة) لكن مسار رابط مختلف فعليًا → وظيفتان مختلفتان
    (مثال واقعي: Fuku تنشر نفس المسمى بمدن مختلفة عبر روابط Workable مستقلة) —
    apply_url يُميّز بينهما حتى لو تطابقت الحقول الثلاثة الأخرى تمامًا."""
    key_a = dedup_key("Fuku", "Creative Director", "Riyadh", "https://apply.workable.com/fuku/j/AAA111")
    key_b = dedup_key("Fuku", "Creative Director", "Riyadh", "https://apply.workable.com/fuku/j/BBB222")
    assert key_a != key_b


def test_dedup_key_no_apply_url_still_collapses_true_content_duplicates() -> None:
    """بلا apply_url إطلاقًا (بعض المصادر لا ترجع رابطًا موثوقًا)، يبقى
    (شركة، مسمى، مدينة) وحده كافيًا لكشف تكرار محتوى حقيقي — هذا هو صلب
    إصلاح R5 (كان الاعتماد السابق على معرّف المنصّة فقط لا يكشف هذه الحالة)."""
    key_a = dedup_key("Zeeco", "Process Engineer", "Dammam", None)
    key_b = dedup_key("Zeeco Inc", "process engineer", "dammam", None)
    assert key_a == key_b


def test_dedup_key_length_within_jobs_column_limit() -> None:
    # عمود jobs.dedup_key هو varchar(600) بالترحيل 0001 — sha1 hex (40 حرفًا)
    # ثابت الطول دائمًا (مراجعة B2 R5)، ضمن الحد بمسافة واسعة أيًا كان طول
    # المدخلات.
    key = dedup_key(
        "شركة طويلة جدًا " * 20,
        "مسمى وظيفي طويل جدًا " * 20,
        "الرياض",
        "https://example.com/" + ("a" * 1000),
    )
    assert len(key) <= 600
    assert len(key) == 40  # sha1 hex digest length


def test_dedup_key_deterministic() -> None:
    """نفس المدخلات تمامًا → نفس المفتاح دائمًا (بلا عشوائية بالتنفيذ)."""
    args = ("Aramco", "Process Engineer", "Dhahran", "https://x.com/jobs/1")
    assert dedup_key(*args) == dedup_key(*args)
