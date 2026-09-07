"""
اختبارات محرّك المطابقة (core/app/matching.py) — B3، الدليل §3.6/§3.7.

دوال نقية بلا اتصال قاعدة بيانات ولا شبكة (بنفس فلسفة test_normalizer.py/
test_discovery_grouping.py) — ≥25 حالة تغطي كل استبعاد قاطع وحدود كل طبقة
(معيار قبول B3 بالتكليف).

تشغيل: cd core && python -m pytest tests/test_matching.py -v
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import matching
from app.matching import CustomerProfile, JobCandidate, evaluate

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


def _profile(**overrides) -> CustomerProfile:
    base = dict(
        customer_id=1,
        years_exp=6.0,
        seniority=None,
        nationality_saudi=False,
        families=["chem_process"],
        cities=["Riyadh", "Jeddah"],
        titles=[{"title": "Process Engineer", "weight": 1.0}],
        skills=["Aspen HYSYS", "HAZOP"],
        degree_family=[],
    )
    base.update(overrides)
    return CustomerProfile(**base)


def _job(**overrides) -> JobCandidate:
    base = dict(
        job_id=100,
        title="Process Engineer",
        family="chem_process",
        years_min=5,
        seniority=None,
        saudi_only=False,
        city="Riyadh",
        skills=["Aspen HYSYS", "HAZOP"],
        company_name="Zeeco",
        apply_mode="external_form",
        first_seen_at=NOW - timedelta(days=1),
        out_of_region=False,
    )
    base.update(overrides)
    return JobCandidate(**base)


# ---------------------------------------------------------------------------
# 1. حالة أساسية مؤهّلة — تطابق قوي، يجب أن تكون طبقة A
# ---------------------------------------------------------------------------


def test_strong_match_is_qualified_tier_a() -> None:
    result = evaluate(_profile(), _job(), now=NOW)
    assert result.disqualified is False
    assert result.reasons == []
    assert result.tier == "A"
    assert result.score >= matching.TIER_A_THRESHOLD


# ---------------------------------------------------------------------------
# 2-5. الاستبعاد القاطع: سنوات الخبرة
# ---------------------------------------------------------------------------


def test_years_exceeded_disqualifies() -> None:
    result = evaluate(_profile(years_exp=2.0), _job(years_min=6), now=NOW)
    assert result.disqualified is True
    assert "years_exceeded" in result.reasons


def test_years_within_slack_of_one_year_is_allowed() -> None:
    # هامش B3: سنة واحدة (YEARS_SLACK) — عميل بخبرة 4 سنوات لوظيفة تطلب 5
    result = evaluate(_profile(years_exp=4.0), _job(years_min=5), now=NOW)
    assert "years_exceeded" not in result.reasons


def test_years_just_beyond_slack_disqualifies() -> None:
    # 4 سنوات خبرة + هامش 1 = 5 — وظيفة تطلب 6 تتجاوز الهامش بالضبط
    result = evaluate(_profile(years_exp=4.0), _job(years_min=6), now=NOW)
    assert "years_exceeded" in result.reasons


def test_years_min_none_never_disqualifies_on_years() -> None:
    result = evaluate(_profile(years_exp=0.5), _job(years_min=None), now=NOW)
    assert "years_exceeded" not in result.reasons


# ---------------------------------------------------------------------------
# 6-10. الاستبعاد القاطع: الأقدمية (مدير/قيادي لملف مبتدئ والعكس)
# ---------------------------------------------------------------------------


def test_manager_job_disqualifies_junior_profile() -> None:
    result = evaluate(_profile(years_exp=1.0), _job(seniority="manager", years_min=None), now=NOW)
    assert "seniority_too_senior" in result.reasons


def test_lead_job_disqualifies_entry_profile() -> None:
    result = evaluate(_profile(years_exp=2.0), _job(seniority="lead", years_min=None), now=NOW)
    assert "seniority_too_senior" in result.reasons


def test_intern_job_disqualifies_very_senior_profile() -> None:
    result = evaluate(_profile(years_exp=16.0), _job(seniority="intern", years_min=None), now=NOW)
    assert "seniority_too_junior" in result.reasons


def test_senior_profile_applying_to_senior_job_is_fine() -> None:
    result = evaluate(_profile(years_exp=8.0), _job(seniority="senior", years_min=None), now=NOW)
    assert "seniority_too_senior" not in result.reasons
    assert "seniority_too_junior" not in result.reasons


def test_mid_profile_applying_to_manager_job_diff_two_disqualifies() -> None:
    # rank(mid)=2، rank(manager)=4 → فرق 2 → يُستبعد (يبلغ العتبة 2 بالضبط)
    result = evaluate(_profile(years_exp=4.0), _job(seniority="manager", years_min=None), now=NOW)
    assert "seniority_too_senior" in result.reasons


def test_senior_profile_applying_to_manager_job_not_disqualified() -> None:
    # rank(senior)=3، rank(manager)=4 → فرق 1 → دون العتبة، غير مستبعد
    result = evaluate(_profile(years_exp=8.0), _job(seniority="manager", years_min=None), now=NOW)
    assert "seniority_too_senior" not in result.reasons


# ---------------------------------------------------------------------------
# 11-13. الاستبعاد القاطع: الجنسية
# ---------------------------------------------------------------------------


def test_saudi_only_disqualifies_non_saudi_profile() -> None:
    result = evaluate(_profile(nationality_saudi=False), _job(saudi_only=True), now=NOW)
    assert "saudi_only_mismatch" in result.reasons


def test_saudi_only_allows_saudi_profile() -> None:
    result = evaluate(_profile(nationality_saudi=True), _job(saudi_only=True), now=NOW)
    assert "saudi_only_mismatch" not in result.reasons


def test_not_saudi_only_never_disqualifies_on_nationality() -> None:
    result = evaluate(_profile(nationality_saudi=False), _job(saudi_only=False), now=NOW)
    assert "saudi_only_mismatch" not in result.reasons


# ---------------------------------------------------------------------------
# 14-15. الاستبعاد القاطع: خارج النطاق الجغرافي
# ---------------------------------------------------------------------------


def test_out_of_region_job_disqualifies() -> None:
    result = evaluate(_profile(), _job(out_of_region=True), now=NOW)
    assert "out_of_region" in result.reasons
    assert result.tier == "D"


def test_in_region_job_not_disqualified_on_region() -> None:
    result = evaluate(_profile(), _job(out_of_region=False), now=NOW)
    assert "out_of_region" not in result.reasons


# ---------------------------------------------------------------------------
# 16-19. الاستبعاد القاطع: العائلة المهنية (والتوسيع C2)
# ---------------------------------------------------------------------------


def test_family_not_selected_disqualifies_without_widening() -> None:
    result = evaluate(_profile(families=["chem_process"]), _job(family="it_software"), now=NOW, widened_family=False)
    assert "family_not_selected" in result.reasons


def test_family_not_selected_allowed_with_widening() -> None:
    result = evaluate(_profile(families=["chem_process"]), _job(family="it_software"), now=NOW, widened_family=True)
    assert "family_not_selected" not in result.reasons


def test_excluded_family_always_disqualifies_even_when_widened() -> None:
    result = evaluate(
        _profile(families=["chem_process"]), _job(family="sales_excluded"), now=NOW, widened_family=True
    )
    assert "family_excluded" in result.reasons


def test_unclassified_family_disqualifies_without_widening() -> None:
    result = evaluate(_profile(), _job(family=None), now=NOW, widened_family=False)
    assert "family_not_selected" in result.reasons


# ---------------------------------------------------------------------------
# 20-23. الاستبعاد القاطع: المدينة (بلا توسيع جغرافي إطلاقًا)
# ---------------------------------------------------------------------------


def test_city_not_in_customer_cities_disqualifies() -> None:
    result = evaluate(_profile(cities=["Riyadh"]), _job(city="Dammam"), now=NOW)
    assert "city_not_selected" in result.reasons


def test_city_in_customer_cities_allowed() -> None:
    result = evaluate(_profile(cities=["Riyadh", "Dammam"]), _job(city="Dammam"), now=NOW)
    assert "city_not_selected" not in result.reasons


def test_unknown_city_never_disqualifies() -> None:
    result = evaluate(_profile(cities=["Riyadh"]), _job(city=None), now=NOW)
    assert "city_not_selected" not in result.reasons


def test_city_matching_is_case_insensitive() -> None:
    result = evaluate(_profile(cities=["riyadh"]), _job(city="Riyadh"), now=NOW)
    assert "city_not_selected" not in result.reasons


# ---------------------------------------------------------------------------
# 24-26. تبريد الشركة وسقف الأسبوع (تُمرّر جاهزة من المستدعي — planner.py)
# ---------------------------------------------------------------------------


def test_cooldown_active_disqualifies() -> None:
    result = evaluate(_profile(), _job(), now=NOW, cooldown_active=True)
    assert "company_cooldown" in result.reasons


def test_weekly_cap_reached_disqualifies() -> None:
    result = evaluate(_profile(), _job(), now=NOW, weekly_cap_reached=True)
    assert "weekly_company_cap" in result.reasons


def test_no_cooldown_no_cap_not_disqualified_on_those_reasons() -> None:
    result = evaluate(_profile(), _job(), now=NOW, cooldown_active=False, weekly_cap_reached=False)
    assert "company_cooldown" not in result.reasons
    assert "weekly_company_cap" not in result.reasons


# ---------------------------------------------------------------------------
# 27-33. حدود الطبقات (score thresholds) — نتحكم بالدرجة عبر مكوّناتها
# مباشرة باستخدام compute_score/determine_tier لضمان دقة الحدود العددية
# بمعزل عن تفاصيل دوال score_* الفردية (تُختبر أدناه بحالات منفصلة أيضًا).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "score,widened,expected_tier",
    [
        (0.75, False, "A"),
        (0.90, False, "A"),
        (0.749999, False, "B"),
        (0.55, False, "B"),
        (0.74, False, "B"),
        (0.549999, False, "C"),
        (0.40, False, "C"),
        (0.40, True, "C2"),
        (0.55, True, "B"),  # التوسيع لا يغيّر A/B — فقط يحوّل C إلى C2
        (0.399999, False, "D"),
        (0.0, False, "D"),
    ],
)
def test_tier_thresholds(score: float, widened: bool, expected_tier: str) -> None:
    assert matching.determine_tier(score, widened_family=widened) == expected_tier


# ---------------------------------------------------------------------------
# 34-38. دوال الدرجة الفردية 0..1
# ---------------------------------------------------------------------------


def test_score_skills_perfect_overlap_is_one() -> None:
    p = _profile(skills=["Aspen HYSYS", "HAZOP"])
    j = _job(skills=["Aspen HYSYS", "HAZOP"])
    assert matching.score_skills(p, j) == 1.0


def test_score_skills_no_ad_skills_defaults_half() -> None:
    p = _profile(skills=["Aspen HYSYS"])
    j = _job(skills=[])
    assert matching.score_skills(p, j) == 0.5


def test_score_skills_no_overlap_is_zero() -> None:
    p = _profile(skills=["SAP"])
    j = _job(skills=["AutoCAD"])
    assert matching.score_skills(p, j) == 0.0


def test_score_seniority_unknown_job_seniority_is_point_six() -> None:
    assert matching.score_seniority(_profile(years_exp=6.0), _job(seniority=None)) == 0.6


def test_score_seniority_exact_match_is_one() -> None:
    p = _profile(years_exp=8.0)  # rank senior
    j = _job(seniority="senior")
    assert matching.score_seniority(p, j) == 1.0


def test_score_location_known_accepted_city_is_one() -> None:
    p = _profile(cities=["Riyadh"])
    j = _job(city="Riyadh")
    assert matching.score_location(p, j) == 1.0


def test_score_location_unknown_city_is_point_three() -> None:
    p = _profile(cities=["Riyadh"])
    j = _job(city=None)
    assert matching.score_location(p, j) == 0.3


def test_score_recency_fresh_job_is_one() -> None:
    j = _job(first_seen_at=NOW - timedelta(hours=1))
    assert matching.score_recency(j, NOW) == 1.0


def test_score_recency_old_job_floors_at_half() -> None:
    j = _job(first_seen_at=NOW - timedelta(days=30))
    assert matching.score_recency(j, NOW) == 0.5


def test_score_recency_missing_timestamp_defaults_half() -> None:
    j = _job(first_seen_at=None)
    assert matching.score_recency(j, NOW) == 0.5


def test_score_source_quality_agency_name_is_low() -> None:
    j = _job(company_name="Eram Talent", apply_mode="external_form")
    assert matching.score_source_quality(j) == 0.3


def test_score_source_quality_direct_employer_ats_is_high() -> None:
    j = _job(company_name="Aramco", apply_mode="external_form")
    assert matching.score_source_quality(j) == 1.0


# ---------------------------------------------------------------------------
# 39-41. الدرجة الكلية تجمع الأوزان الصحيحة (0.35/0.30/0.15/0.10/0.05/0.05)
# ---------------------------------------------------------------------------


def test_weights_sum_to_one() -> None:
    assert round(sum(matching.WEIGHTS.values()), 6) == 1.0


def test_compute_score_matches_weighted_sum_of_parts() -> None:
    p = _profile()
    j = _job()
    parts = matching.compute_score(p, j, NOW)
    manual_total = sum(parts[k] * matching.WEIGHTS[k] for k in matching.WEIGHTS)
    assert abs(parts["total"] - round(manual_total, 4)) < 1e-6


def test_disqualified_job_still_computes_score_for_explain_endpoint() -> None:
    result = evaluate(_profile(nationality_saudi=False), _job(saudi_only=True), now=NOW)
    assert result.disqualified is True
    assert 0.0 <= result.score <= 1.0
    assert result.tier == "D"


# ---------------------------------------------------------------------------
# 42-43. جودة المطابقة الكاملة: طبقة B ثم استبعاد متعدد الأسباب معًا
# ---------------------------------------------------------------------------


def test_moderate_match_lands_tier_b_or_c() -> None:
    p = _profile(skills=["SAP"], cities=["Riyadh"])
    j = _job(skills=["AutoCAD"], city="Riyadh", seniority="senior")
    result = evaluate(p, j, now=NOW)
    assert result.disqualified is False
    assert result.tier in ("B", "C")


def test_multiple_disqualifiers_all_collected_together() -> None:
    p = _profile(years_exp=1.0, nationality_saudi=False, cities=["Riyadh"], families=["chem_process"])
    j = _job(years_min=10, seniority="manager", saudi_only=True, city="Dammam", family="hse", out_of_region=True)
    result = evaluate(p, j, now=NOW, widened_family=False)
    assert result.disqualified is True
    assert set(result.reasons) >= {
        "years_exceeded",
        "seniority_too_senior",
        "saudi_only_mismatch",
        "city_not_selected",
        "family_not_selected",
        "out_of_region",
    }
