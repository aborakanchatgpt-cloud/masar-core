"""اختبارات `discovery.classify_family()`/`discovery._normalize_for_match()`
(مراجعة B2b) — دوال نقية بلا اتصال قاعدة بيانات، تُشغّل محليًا بـpytest.
تغطي: تطبيع عربي (تشكيل/همزات/تاء مربوطة)، تطبيع إنجليزي (Sr./Jr.، أرقام
رومانية لاحقة، "&"، شرطات مائلة)، عنوان-أولاً-ثم-وصف كملاذ أخير، وحالات
out_of_scope (يجب أن تُصنّف كـ"out_of_scope" لا أن تبقى None).

تشغيل: cd core && python -m pytest tests/test_classify_family.py -v
"""
from __future__ import annotations

import os
from pathlib import Path

# discovery._data_dir() يقرأ DATA_DIR الافتراضي "/app/data" (مسار الحاوية
# الحيّة) — غير موجود محليًا. نضبطه لمجلد data/ الحقيقي بالمستودع *قبل* أول
# استدعاء لـclassify_family (الذي يبني كاش المعجم مرة واحدة فقط للعملية)، حتى
# يعمل هذا الملف بمفرده أو ضمن كامل الحزمة بلا الاعتماد على ترتيب
# pytest للملفات ولا على متغيّرات بيئة خارجية.
os.environ.setdefault("DATA_DIR", str(Path(__file__).resolve().parent.parent.parent / "data"))

from app import discovery  # noqa: E402 — بعد ضبط DATA_DIR عمدًا
from app.discovery import EXCLUDED_FAMILY_NAMES, _normalize_for_match, classify_family  # noqa: E402

discovery._families_cache = None
discovery._family_patterns_cache = None


def test_english_title_direct_match() -> None:
    assert classify_family("Process Engineer") == "chem_process"


def test_arabic_title_direct_match() -> None:
    assert classify_family("مهندس عمليات") == "chem_process"


def test_arabic_hamza_ta_marbuta_variants_still_match() -> None:
    """الكلمة المفتاحية بالمعجم "مهندس سلامة" (بهمزة قطع طبيعية) — عنوان
    حقيقي بصيغة إملائية مختلفة قليلًا (تاء مربوطة/همزة مغايرة) يجب أن
    يُطابَق أيضًا بعد التطبيع، لا فقط الصيغة الحرفية الموجودة بالمعجم."""
    # "السلامه" بتاء مربوطة مكتوبة بديلة + "أخصائي" بهمزة قطع صريحة
    assert classify_family("أخصائي HSE") == "hse"
    assert classify_family("اخصائي HSE") == "hse"  # بلا همزة إطلاقًا — نفس التصنيف بعد التطبيع


def test_seniority_prefix_sr_jr_does_not_block_match() -> None:
    assert classify_family("Sr. Process Engineer") == "chem_process"
    assert classify_family("Jr Process Engineer") == "chem_process"
    assert classify_family("Senior Process Engineer") == "chem_process"


def test_roman_numeral_suffix_does_not_block_match() -> None:
    assert classify_family("Process Engineer II") == "chem_process"
    assert classify_family("Process Engineer - III") == "chem_process"


def test_ampersand_normalized_to_and() -> None:
    """"Food & Beverage Supervisor" يجب أن يُطابق hospitality رغم أن الكلمة
    المفتاحية بالمعجم مكتوبة "food and beverage" (بـ"and" لا "&")."""
    assert classify_family("Food & Beverage Supervisor - Italian Restaurant") == "hospitality"


def test_slash_and_punctuation_do_not_block_boundary_match() -> None:
    assert classify_family("Waiter/Waitress") == "hospitality"
    assert classify_family("Technician, SCADA") == "maintenance_ops"


def test_out_of_scope_sales_titles_classified_not_none() -> None:
    """عنوان مبيعات/تطوير أعمال يجب أن يُصنّف "out_of_scope" صراحة — لا
    None — حتى يُحسب "مصنّف" بمقياس family_classified_pct_in_region مع
    استثنائه من family_real_pct_in_region (discovery_api.py)."""
    for title in [
        "Sales Executive",
        "Senior Sales Manager",
        "Field Sales Consultant - Classifieds",
        "Enterprise Account Executive",
        "Telesales Agent",
        "Sales Engineer - Overhead Cranes",
    ]:
        fam = classify_family(title)
        assert fam == "out_of_scope", f"{title!r} -> {fam!r}"
        assert fam in EXCLUDED_FAMILY_NAMES


def test_title_first_then_description_fallback() -> None:
    """عنوان لا يُطابق شيئًا بمفرده، لكن الوصف يحمل كلمة مفتاحية واضحة —
    يجب أن يُصنّف عبر الوصف كملاذ أخير (لا أن يبقى None)."""
    fam = classify_family(
        "Opportunity #4471",
        description="We are hiring a process engineer to join our Jubail plant team.",
    )
    assert fam == "chem_process"


def test_title_match_wins_over_conflicting_description() -> None:
    """حين يُطابق العنوان وحده، لا داعي لفحص الوصف إطلاقًا — النتيجة تعتمد
    العنوان فقط (مراجعة B2b: عنوان-أولاً-ثم-وصف، لا دمج الاثنين معًا)."""
    fam = classify_family(
        "Process Engineer",
        description="Also handles sales executive duties occasionally.",
    )
    assert fam == "chem_process"


def test_no_match_returns_none() -> None:
    assert classify_family("Zzqx Freelance Notion Expert Gig", description="") is None


def test_empty_title_and_description_returns_none() -> None:
    assert classify_family("", description="") is None
    assert classify_family(None, description=None) is None


def test_normalize_for_match_folds_diacritics_and_alef_variants() -> None:
    assert _normalize_for_match("أَحْمَد") == _normalize_for_match("احمد")


def test_normalize_for_match_folds_ta_marbuta_and_alef_maqsura() -> None:
    assert _normalize_for_match("السلامة") == _normalize_for_match("السلامه")
    assert _normalize_for_match("مستشفى") == _normalize_for_match("مستشفي")
