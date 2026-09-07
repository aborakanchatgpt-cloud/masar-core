"""
اختبارات `discovery._group_raw_jobs_by_identity()` — مراجعة B2 الثانية R10
(docs/reports/B2-review-2.md): تجميع raw_jobs المجلوبة بنفس الجولة حسب هوية
الإعلان الحقيقية (apply_url) *قبل* أي إدراج بقاعدة البيانات، بدل صف
منفصل لكل مدينة "مرشّحة" يذكرها المصدر لنفس apply_url فعليًا.

دالة نقية بلا اتصال قاعدة بيانات ولا شبكة — تُشغّل محليًا بـpytest.

تشغيل: cd core && python -m pytest tests/test_discovery_grouping.py -v
"""
from __future__ import annotations

from app.discovery import _group_raw_jobs_by_identity


def _workable_multi_location_fixture() -> list[dict]:
    """يحاكي نمط Eram Talent الفعلي على Workable (موثّق بـB2-review-2.md §2):
    نفس الوظيفة (نفس apply_url) تتكرر 6 مرات ضمن استجابة جلب واحدة، مرة
    لكل مدينة "مرشّحة" يذكرها المصدر — بعضها حتى خارج السعودية بالكامل (Doha)،
    رغم أن العنوان يحمل لاحقة "(Saudi Arabia)" ثابتة لكل النسخ."""
    shared_url = "https://apply.workable.com/eram-talent/j/05EAA33D35"
    cities = ["Jeddah", "Riyadh", "Tabuk", "Madinah", "Al Bahah", "Al Khobar"]
    return [
        {
            "title": "IT Asset Management Analyst (Saudi Arabia)",
            "location": city,
            "url": shared_url,
            "external_id": f"05EAA33D35-{i}",
            "raw": {"description": "Client engagement across the GCC region."},
        }
        for i, city in enumerate(cities)
    ]


def test_multi_location_workable_fixture_collapses_to_one_group() -> None:
    """R10 — الاختبار المطلوب صراحةً بالتكليف: نفس apply_url مكرر 6 مرات
    بمدن مختلفة يجب أن يُنتج مجموعة واحدة فقط، لا 6 مجموعات."""
    raw_jobs = _workable_multi_location_fixture()
    grouped = _group_raw_jobs_by_identity(raw_jobs, "Eram Talent", source_id=7)

    assert len(grouped) == 1
    entry = grouped[0]
    assert entry["title"] == "IT Asset Management Analyst (Saudi Arabia)"
    assert entry["apply_url"] == "https://apply.workable.com/eram-talent/j/05EAA33D35"
    # كل المدن الست مذكورة، بدون تكرار، بترتيب الظهور
    assert entry["locations"] == ["Jeddah", "Riyadh", "Tabuk", "Madinah", "Al Bahah", "Al Khobar"]


def test_multi_location_fixture_with_repeated_city_does_not_duplicate_location() -> None:
    """تكرار نفس المدينة حرفيًا مرتين ضمن نفس apply_url لا يُضاف مرتين لقائمة
    locations (مجموعة فريدة، لا قائمة خامة)."""
    shared_url = "https://apply.workable.com/hudson-manpower/j/06954AB530"
    raw_jobs = [
        {"title": "Maintenance Engineer-Biomedical (Saudi Arabia)", "location": "Jeddah", "url": shared_url, "raw": {}},
        {"title": "Maintenance Engineer-Biomedical (Saudi Arabia)", "location": "Jeddah", "url": shared_url, "raw": {}},
        {"title": "Maintenance Engineer-Biomedical (Saudi Arabia)", "location": "Lusail, Qatar", "url": shared_url, "raw": {}},
    ]
    grouped = _group_raw_jobs_by_identity(raw_jobs, "Hudson Manpower", source_id=3)
    assert len(grouped) == 1
    assert grouped[0]["locations"] == ["Jeddah", "Lusail, Qatar"]


def test_distinct_apply_urls_stay_separate_groups() -> None:
    """اختلاف apply_url فعليًا (وظيفتان مختلفتان حقًا بنفس الشركة والمسمى
    والمدينة، مثال Fuku بمدن Workable مستقلة) يجب ألا يُجمّعا معًا."""
    raw_jobs = [
        {"title": "Creative Director", "location": "Riyadh", "url": "https://apply.workable.com/fuku/j/AAA111", "raw": {}},
        {"title": "Creative Director", "location": "Riyadh", "url": "https://apply.workable.com/fuku/j/BBB222", "raw": {}},
    ]
    grouped = _group_raw_jobs_by_identity(raw_jobs, "Fuku", source_id=11)
    assert len(grouped) == 2
    assert {g["apply_url"] for g in grouped} == {
        "https://apply.workable.com/fuku/j/AAA111",
        "https://apply.workable.com/fuku/j/BBB222",
    }


def test_no_apply_url_groups_still_collapse_by_company_title_location() -> None:
    """بلا apply_url إطلاقًا (بعض مصادر SmartRecruiters)، يبقى (شركة، مسمى،
    موقع) وحده كافيًا لتجميع تكرار محتوى حقيقي — هذا هو الملاذ الأخير
    بـdedup_key (مراجعة B2 R5)."""
    raw_jobs = [
        {"title": "Process Engineer, Sr.", "location": "Dammam", "url": None, "raw": {}},
        {"title": "process engineer sr", "location": "dammam", "url": "", "raw": {}},
    ]
    grouped = _group_raw_jobs_by_identity(raw_jobs, "Zeeco", source_id=5)
    assert len(grouped) == 1


def test_no_apply_url_different_location_stays_separate() -> None:
    """بلا apply_url، اختلاف الموقع الخام فعليًا يُنتج مجموعتين منفصلتين — الملاذ
    الأخير لا يزال يستخدم الموقع كجزء من الهوية."""
    raw_jobs = [
        {"title": "Process Engineer", "location": "Dammam", "url": None, "raw": {}},
        {"title": "Process Engineer", "location": "Riyadh", "url": None, "raw": {}},
    ]
    grouped = _group_raw_jobs_by_identity(raw_jobs, "Zeeco", source_id=5)
    assert len(grouped) == 2


def test_entries_without_title_are_skipped() -> None:
    raw_jobs = [
        {"title": "", "location": "Riyadh", "url": "https://x.com/j/1", "raw": {}},
        {"title": "   ", "location": "Riyadh", "url": "https://x.com/j/2", "raw": {}},
        {"title": "Valid Title", "location": "Riyadh", "url": "https://x.com/j/3", "raw": {}},
    ]
    grouped = _group_raw_jobs_by_identity(raw_jobs, "Acme", source_id=1)
    assert len(grouped) == 1
    assert grouped[0]["title"] == "Valid Title"
