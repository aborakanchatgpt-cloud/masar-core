"""
اختبارات scripts/verify_sources.py — تنفيذ B2-close. دوال القراءة/الفحص
النقية (بلا شبكة فعلية، بمحاكاة discovery.validate_source و_robots_allow)
— يضمن أن أداة التحقّق تعمل صحيحًا (تُشغَّل فعليًا ضد الشبكة الحقيقية من
بيئة نشر Core الحيّة التي تملك وصولًا كاملًا للإنترنت؛ بيئة تنفيذ هذه
الجلسة تحديدًا خلف بروكسي صادر مقيّد بقائمة سماح لا يشملها — راجع
docs/reports/B2-close-executor.md §2 لتفاصيل هذا القيد البيئي وكيف جرى
التحقّق الفعلي من المصادر الجديدة عبر WebFetch بدلًا من تشغيل هذا السكربت
مباشرة من هذه الجلسة تحديدًا).

تشغيل: cd core && python -m pytest tests/test_verify_sources.py -v
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import verify_sources  # noqa: E402


def _write_csv(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "candidates.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["company", "type", "url", "country", "terms_note"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def test_read_candidates_skips_incomplete_rows(tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path,
        [
            {"company": "Acme", "type": "rss", "url": "https://acme.com/jobs.rss", "country": "SA", "terms_note": "x"},
            {"company": "", "type": "rss", "url": "https://x.com/jobs.rss", "country": "", "terms_note": ""},
            {"company": "NoUrl", "type": "rss", "url": "", "country": "", "terms_note": ""},
        ],
    )
    rows = verify_sources.read_candidates(path)
    assert len(rows) == 1
    assert rows[0]["company"] == "Acme"


def test_verify_one_unsupported_type_returns_error_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"robots": False, "get": False}
    monkeypatch.setattr(verify_sources, "_robots_allow", lambda url: called.__setitem__("robots", True) or (True, "ok"))
    monkeypatch.setattr(verify_sources, "http_get", lambda *a, **kw: called.__setitem__("get", True))

    row = {"company": "X", "type": "not_a_real_type", "url": "https://x.com/jobs", "country": "SA", "terms_note": ""}
    result = verify_sources.verify_one(row)
    assert result["error"] is not None
    assert "غير مدعوم" in result["error"]
    # نوع غير مدعوم يُرفض فورًا — بلا أي محاولة شبكة (لا robots.txt ولا GET).
    assert called == {"robots": False, "get": False}


def test_verify_one_respects_robots_disallow_and_skips_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """اشتراط التكليف: احترام robots.txt صارم — مصدر ممنوع لا يُجلَب محتواه
    إطلاقًا (لا GET، لا discovery.validate_source)."""
    monkeypatch.setattr(verify_sources, "_robots_allow", lambda url: (False, "ممنوع صراحة بـrobots.txt"))

    def fail_get(*a, **kw):
        raise AssertionError("لا يجب أي جلب حين يمنع robots.txt")

    monkeypatch.setattr(verify_sources, "http_get", fail_get)
    monkeypatch.setattr(
        verify_sources.discovery,
        "validate_source",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("لا يجب استدعاء validate_source")),
    )

    row = {"company": "Blocked Co", "type": "rss", "url": "https://blocked.com/jobs.rss", "country": "SA", "terms_note": ""}
    result = verify_sources.verify_one(row)
    assert result["robots_allowed"] is False
    assert result["jobs_found"] is None
    assert "robots.txt" in result["error"]


def test_verify_one_success_path_reports_job_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify_sources, "_robots_allow", lambda url: (True, "سماح ضمني"))

    class FakeResponse:
        status_code = 200

    monkeypatch.setattr(verify_sources, "http_get", lambda *a, **kw: FakeResponse())
    monkeypatch.setattr(verify_sources.discovery, "validate_source", lambda source_type, url, timeout=None: 7)

    row = {"company": "Working Co", "type": "rss", "url": "https://working.com/jobs.rss", "country": "SA", "terms_note": ""}
    result = verify_sources.verify_one(row)
    assert result["error"] is None
    assert result["http_status"] == 200
    assert result["jobs_found"] == 7
    assert result["robots_allowed"] is True


def test_verify_one_network_error_during_probe_reports_error_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify_sources, "_robots_allow", lambda url: (True, "سماح ضمني"))

    class FakeResponse:
        status_code = 200

    monkeypatch.setattr(verify_sources, "http_get", lambda *a, **kw: FakeResponse())

    def raise_error(*a, **kw):
        raise RuntimeError("انقطاع شبكة محاكى")

    monkeypatch.setattr(verify_sources.discovery, "validate_source", raise_error)

    row = {"company": "Flaky Co", "type": "greenhouse", "url": "https://flaky.com/jobs", "country": "SA", "terms_note": ""}
    result = verify_sources.verify_one(row)
    assert result["jobs_found"] is None
    assert "انقطاع شبكة محاكى" in result["error"]
