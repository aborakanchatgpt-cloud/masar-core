"""اختبارات core/app/skill_gap.py (B4/v2-B6، 12 سبتمبر) — النسخة الإحصائية
الجديدة بلا أي اعتماد على ANTHROPIC_API_KEY (استبدلت النسخة السابقة التي
كانت تستدعي Anthropic Messages API مباشرة — لا اختبارات httpx/API متبقّية
هنا إطلاقًا). تغطّي: تجميع أعلى المهارات مع الاستبعاد والتطبيع، اختيار
الشهادات من ملف YAML مع الاستبعاد وعدم التكرار، اختيار النصيحة الثابتة،
بناء الرسالة النهائية، وبوابة كفاية العيّنة (min جولة بلا قاعدة بيانات
حقيقية عبر محاكاة `_fetch_active_customers_with_profile`/
`_fetch_recent_jobs_by_family`).

تشغيل: cd core && python -m pytest tests/test_skill_gap.py -v
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# skill_gap._data_dir() يقرأ DATA_DIR الافتراضي "/app/data" (مسار الحاوية
# الحي) — بلا هذا السطر، اختبار قراءة certifications_by_family.yaml الفعلي
# يفشل محليًا (نفس نمط tests/test_classify_family.py لـtaxonomy_local.yaml).
os.environ.setdefault("DATA_DIR", str(Path(__file__).resolve().parent.parent.parent / "data"))

from app import skill_gap  # noqa: E402 — بعد ضبط DATA_DIR عمدًا


def test_extract_title_strings_reads_dict_format() -> None:
    titles_json = [{"title": "مهندس برمجيات", "weight": 1.0}, {"title": "مطوّر", "weight": 0.5}, {"weight": 0.2}]
    assert skill_gap._extract_title_strings(titles_json) == ["مهندس برمجيات", "مطوّر"]


def test_extract_title_strings_empty_input() -> None:
    assert skill_gap._extract_title_strings([]) == []
    assert skill_gap._extract_title_strings(None) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# تجميع أعلى المهارات — تكرار تنازلي، استبعاد مهارات العميل الحالية، تطبيع.
# ---------------------------------------------------------------------------


def test_aggregate_top_skills_ranks_by_frequency_descending() -> None:
    jobs = [
        {"skills": ["Python", "SQL"]},
        {"skills": ["Python", "Excel"]},
        {"skills": ["python"]},  # حالة أحرف مختلفة — نفس المهارة بعد التطبيع
        {"skills": ["SQL"]},
    ]
    top = skill_gap._aggregate_top_skills(jobs, exclude_normalized=set(), top_n=5)
    assert top[0] == "Python"  # 3 تكرارات (بعد التطبيع) — الأعلى
    assert "SQL" in top
    assert "Excel" in top


def test_aggregate_top_skills_excludes_customer_existing_skills() -> None:
    jobs = [{"skills": ["Python", "SQL"]}, {"skills": ["Python"]}]
    top = skill_gap._aggregate_top_skills(jobs, exclude_normalized={"python"}, top_n=5)
    assert "Python" not in top
    assert "SQL" in top


def test_aggregate_top_skills_respects_top_n_limit() -> None:
    jobs = [{"skills": [f"skill_{i}"]} for i in range(10)]
    top = skill_gap._aggregate_top_skills(jobs, exclude_normalized=set(), top_n=3)
    assert len(top) == 3


def test_aggregate_top_skills_ignores_null_and_empty_entries() -> None:
    jobs = [{"skills": None}, {"skills": []}, {"skills": ["", "  ", "Java"]}, {}]
    top = skill_gap._aggregate_top_skills(jobs, exclude_normalized=set(), top_n=5)
    assert top == ["Java"]


# ---------------------------------------------------------------------------
# اختيار الشهادات — من ملف certifications_by_family.yaml الفعلي (لا محاكاة
# — يختبر أيضًا أن الملف نفسه صالح وقابل للتحميل والاستخدام).
# ---------------------------------------------------------------------------


def test_load_certifications_by_family_covers_all_family_button_order_codes() -> None:
    from app import telegram_onboarding as ob

    certs_by_family = skill_gap._load_certifications_by_family()
    for code, _label in ob.FAMILY_BUTTON_ORDER:
        assert code in certs_by_family, f"لا شهادات معرّفة للعائلة {code} بملف certifications_by_family.yaml"
        entry = certs_by_family[code]
        assert 2 <= len(entry["certifications"]) <= 3
        assert entry["advice"]


def test_pick_certifications_excludes_existing_and_dedupes_across_families() -> None:
    certs_by_family = {
        "fam_a": {"certifications": [{"name": "PMP", "reason": "سبب أ"}, {"name": "Cert B", "reason": "سبب ب"}]},
        "fam_b": {"certifications": [{"name": "pmp", "reason": "مكررة بحالة أحرف مختلفة"}, {"name": "Cert C", "reason": "سبب ج"}]},
    }
    picked = skill_gap._pick_certifications(
        ["fam_a", "fam_b"], exclude_normalized=set(), certs_by_family=certs_by_family, top_n=2
    )
    names = [c["name"] for c in picked]
    assert names == ["PMP", "Cert B"]  # PMP من fam_a أولاً، لا تكرار من fam_b


def test_pick_certifications_excludes_customer_existing_certs() -> None:
    certs_by_family = {"fam_a": {"certifications": [{"name": "PMP", "reason": "س"}, {"name": "Cert B", "reason": "س"}]}}
    picked = skill_gap._pick_certifications(
        ["fam_a"], exclude_normalized={"pmp"}, certs_by_family=certs_by_family, top_n=2
    )
    assert [c["name"] for c in picked] == ["Cert B"]


def test_pick_advice_uses_first_family_with_advice() -> None:
    certs_by_family = {"fam_a": {"advice": "نصيحة أ"}, "fam_b": {"advice": "نصيحة ب"}}
    assert skill_gap._pick_advice(["fam_a", "fam_b"], certs_by_family) == "نصيحة أ"


def test_pick_advice_falls_back_to_next_family_if_first_missing() -> None:
    certs_by_family = {"fam_a": {}, "fam_b": {"advice": "نصيحة ب"}}
    assert skill_gap._pick_advice(["fam_a", "fam_b"], certs_by_family) == "نصيحة ب"


def test_pick_advice_returns_empty_string_for_unknown_families() -> None:
    assert skill_gap._pick_advice(["unknown_family"], {}) == ""


# ---------------------------------------------------------------------------
# بناء الرسالة النهائية — نفس شكل/نبرة النسخة السابقة حرفيًا.
# ---------------------------------------------------------------------------


def test_build_skill_gap_message_includes_all_sections() -> None:
    parsed = {
        "certifications": [{"name": "PMP", "reason": "يرفع فرصك"}],
        "skills": [{"name": "Python"}, {"name": "SQL"}],
        "advice": "طوّر نفسك باستمرار",
    }
    message = skill_gap._build_skill_gap_message(parsed)
    assert "🌟 نصيحة الجمعة لتطوير مسارك المهني" in message
    assert "PMP" in message and "يرفع فرصك" in message
    assert "Python" in message and "SQL" in message
    assert "طوّر نفسك باستمرار" in message
    assert message.strip().endswith("والله يوفقك ويرزقك 🤍")


def test_build_skill_gap_message_handles_empty_sections() -> None:
    message = skill_gap._build_skill_gap_message({"certifications": [], "skills": [], "advice": ""})
    assert "🎓" not in message
    assert "🛠️" not in message
    assert "📌" not in message


# ---------------------------------------------------------------------------
# الجولة الكاملة — بلا قاعدة بيانات حقيقية (محاكاة الجلب والإرسال بالكامل).
# ---------------------------------------------------------------------------


class _FakeConnCtx:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    def connect(self):
        return _FakeConnCtx()


def test_run_skill_gap_round_sends_for_customer_with_enough_data(monkeypatch: pytest.MonkeyPatch) -> None:
    customers = [
        {
            "id": 1, "name": "سالم", "telegram_chat_id": 111, "families": ["it_software"], "cities": [],
            "profile_skills": ["Python"], "profile_certs": [],
        },
    ]
    monkeypatch.setattr(skill_gap, "_fetch_active_customers_with_profile", lambda conn: customers)
    jobs = [{"city": None, "skills": ["Python", "SQL", "AWS"]} for _ in range(skill_gap.MIN_JOBS_SAMPLE)]
    monkeypatch.setattr(skill_gap, "_fetch_recent_jobs_by_family", lambda conn, families, days: jobs)

    sent_msgs = []
    monkeypatch.setattr(skill_gap, "send_message", lambda chat_id, text, **kw: sent_msgs.append((chat_id, text)))

    result = skill_gap.run_skill_gap_round(engine=_FakeEngine())

    assert result["sent"] == 1
    assert result["skipped_no_chat"] == 0
    assert result["skipped_no_family"] == 0
    assert result["skipped_insufficient_data"] == 0
    assert result["errors"] == 0
    assert len(sent_msgs) == 1
    assert sent_msgs[0][0] == 111
    skills_section = sent_msgs[0][1].split("🛠️")[-1]
    assert "SQL" in skills_section and "AWS" in skills_section
    assert "Python" not in skills_section  # مُستبعَدة (موجودة أصلًا بـprofile_skills)


def test_run_skill_gap_round_skips_customer_without_chat_id(monkeypatch: pytest.MonkeyPatch) -> None:
    customers = [{"id": 2, "telegram_chat_id": None, "families": ["it_software"], "cities": []}]
    monkeypatch.setattr(skill_gap, "_fetch_active_customers_with_profile", lambda conn: customers)
    result = skill_gap.run_skill_gap_round(engine=_FakeEngine())
    assert result["skipped_no_chat"] == 1
    assert result["sent"] == 0


def test_run_skill_gap_round_skips_customer_without_families(monkeypatch: pytest.MonkeyPatch) -> None:
    customers = [{"id": 3, "telegram_chat_id": 222, "families": [], "cities": []}]
    monkeypatch.setattr(skill_gap, "_fetch_active_customers_with_profile", lambda conn: customers)
    result = skill_gap.run_skill_gap_round(engine=_FakeEngine())
    assert result["skipped_no_family"] == 1
    assert result["sent"] == 0


def test_run_skill_gap_round_skips_when_insufficient_job_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    customers = [{"id": 4, "telegram_chat_id": 333, "families": ["it_software"], "cities": []}]
    monkeypatch.setattr(skill_gap, "_fetch_active_customers_with_profile", lambda conn: customers)
    few_jobs = [{"city": None, "skills": ["Python"]} for _ in range(skill_gap.MIN_JOBS_SAMPLE - 1)]
    monkeypatch.setattr(skill_gap, "_fetch_recent_jobs_by_family", lambda conn, families, days: few_jobs)
    sent_msgs = []
    monkeypatch.setattr(skill_gap, "send_message", lambda chat_id, text, **kw: sent_msgs.append((chat_id, text)))

    result = skill_gap.run_skill_gap_round(engine=_FakeEngine())
    assert result["skipped_insufficient_data"] == 1
    assert result["sent"] == 0
    assert sent_msgs == []


def test_run_skill_gap_round_filters_jobs_by_customer_city(monkeypatch: pytest.MonkeyPatch) -> None:
    """مطابقة المدينة تُعاد استخدامها فعليًا من app.matching.city_allowed —
    وظيفة بمدينة غير مختارة من العميل تُستبعَد من العيّنة (وقد تُسقطها تحت
    الحدّ الأدنى)."""
    customers = [
        {
            "id": 5, "telegram_chat_id": 444, "families": ["it_software"], "cities": ["الرياض"],
            "profile_skills": [], "profile_certs": [],
        },
    ]
    monkeypatch.setattr(skill_gap, "_fetch_active_customers_with_profile", lambda conn: customers)
    # نصف الوظائف بمدينة غير مطابقة — تُستبعد فتُسقط العيّنة الفعّالة تحت الحد.
    riyadh_jobs = [{"city": "الرياض", "skills": ["Python"]} for _ in range(5)]
    jeddah_jobs = [{"city": "جدة", "skills": ["SQL"]} for _ in range(10)]
    monkeypatch.setattr(
        skill_gap, "_fetch_recent_jobs_by_family", lambda conn, families, days: riyadh_jobs + jeddah_jobs
    )
    sent_msgs = []
    monkeypatch.setattr(skill_gap, "send_message", lambda chat_id, text, **kw: sent_msgs.append((chat_id, text)))

    result = skill_gap.run_skill_gap_round(engine=_FakeEngine())
    assert result["skipped_insufficient_data"] == 1  # 5 فقط بالرياض، أقل من MIN_JOBS_SAMPLE
    assert sent_msgs == []


def test_run_skill_gap_round_isolates_one_customer_error(monkeypatch: pytest.MonkeyPatch) -> None:
    customers = [{"id": 6, "telegram_chat_id": 555, "families": ["it_software"], "cities": []}]
    monkeypatch.setattr(skill_gap, "_fetch_active_customers_with_profile", lambda conn: customers)

    def _boom(conn, families, days):
        raise RuntimeError("db kaboom")

    monkeypatch.setattr(skill_gap, "_fetch_recent_jobs_by_family", _boom)
    result = skill_gap.run_skill_gap_round(engine=_FakeEngine())
    assert result["errors"] == 1
    assert result["sent"] == 0
