"""اختبارات core/app/skill_gap.py (B8: بديل ركفلو n8n السابق
job-bot-weekly-skill-gap-analysis-friday__VcTFiyUdB7FQDsmw.json). تُغطّي:
استخراج المسميات من تنسيق profiles.titles JSONB، تحليل رد Anthropic (بما
فيه رد فاسد/غير JSON)، بناء رسالة تيليجرام النهائية، وبوابة الأمان (تخطّي
كامل بلا ANTHROPIC_API_KEY). كل استدعاء httpx (Anthropic وتيليجرام) مُحاكى
بالكامل — بلا أي اتصال شبكة حقيقي، بلا قاعدة بيانات حقيقية.

تشغيل: cd core && python -m pytest tests/test_skill_gap.py -v
"""
from __future__ import annotations

import httpx
import pytest

from app import skill_gap


def test_extract_title_strings_reads_dict_format() -> None:
    titles_json = [{"title": "مهندس برمجيات", "weight": 1.0}, {"title": "مطوّر", "weight": 0.5}, {"weight": 0.2}]
    assert skill_gap._extract_title_strings(titles_json) == ["مهندس برمجيات", "مطوّر"]


def test_extract_title_strings_empty_input() -> None:
    assert skill_gap._extract_title_strings([]) == []
    assert skill_gap._extract_title_strings(None) == []  # type: ignore[arg-type]


def test_parse_skill_gap_response_valid_json() -> None:
    raw = 'مقدمة عشوائية\n{"certifications": [{"name": "PMP", "reason": "سبب"}], "skills": [{"name": "Python"}], "advice": "نصيحة"}\nذيل'
    parsed = skill_gap._parse_skill_gap_response(raw)
    assert parsed["certifications"] == [{"name": "PMP", "reason": "سبب"}]
    assert parsed["skills"] == [{"name": "Python"}]
    assert parsed["advice"] == "نصيحة"


def test_parse_skill_gap_response_invalid_json_returns_empty_lists() -> None:
    parsed = skill_gap._parse_skill_gap_response("ليس JSON على الإطلاق")
    assert parsed == {"certifications": [], "skills": [], "advice": ""}


def test_parse_skill_gap_response_non_dict_json_returns_empty_lists() -> None:
    parsed = skill_gap._parse_skill_gap_response("[1, 2, 3]")
    assert parsed == {"certifications": [], "skills": [], "advice": ""}


def test_build_skill_gap_message_includes_all_sections() -> None:
    parsed = {
        "certifications": [{"name": "PMP", "reason": "يرفع فرصك"}],
        "skills": [{"name": "Python", "reason": "مطلوبة"}, {"name": "SQL"}],
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


def test_run_skill_gap_round_skips_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = skill_gap.run_skill_gap_round(engine=object())
    assert result == {"ok": True, "skipped": "no_api_key"}


def test_run_skill_gap_round_sends_message_for_eligible_customer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "TESTKEY")

    customers = [
        {
            "id": 1,
            "name": "سالم",
            "telegram_chat_id": 111,
            "families": ["تقنية المعلومات"],
            "cv_text": "خبرة 5 سنوات بايثون",
            "titles": [{"title": "مطوّر", "weight": 1.0}],
        },
        {"id": 2, "name": "بلا تيليجرام", "telegram_chat_id": None, "families": [], "cv_text": "شيء", "titles": []},
        {"id": 3, "name": "بلا سيرة", "telegram_chat_id": 222, "families": [], "cv_text": None, "titles": []},
    ]
    monkeypatch.setattr(skill_gap, "_fetch_active_customers_with_profile", lambda conn: customers)

    anthropic_calls = []

    def fake_anthropic_post(url, *, headers, json, timeout):
        anthropic_calls.append({"url": url, "headers": headers, "json": json})
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": '{"certifications": [], "skills": [], "advice": "استمر"}'}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(skill_gap.httpx, "post", fake_anthropic_post)

    sent_msgs = []
    monkeypatch.setattr(skill_gap, "send_message", lambda chat_id, text, **kw: sent_msgs.append((chat_id, text)))

    class _FakeConnCtx:
        def __enter__(self):
            return None

        def __exit__(self, *exc):
            return False

    class _FakeEngine:
        def connect(self):
            return _FakeConnCtx()

    result = skill_gap.run_skill_gap_round(engine=_FakeEngine())

    assert result["customers_active_with_profile"] == 3
    assert result["sent"] == 1
    assert result["skipped_no_chat"] == 1
    assert result["skipped_no_cv"] == 1
    assert result["errors"] == 0
    assert len(anthropic_calls) == 1
    assert anthropic_calls[0]["headers"]["x-api-key"] == "TESTKEY"
    assert "سالم" in anthropic_calls[0]["json"]["messages"][0]["content"]
    assert len(sent_msgs) == 1
    assert sent_msgs[0][0] == 111
    assert "استمر" in sent_msgs[0][1]
