"""
اختبارات core/app/collectors/sitemap_jsonld.py — تنفيذ B2-close (تغطية سعودية
مغلقة المصدر بلا كشط، البند المتروك من B2b): يوسّع الوحدة لفهم sitemap.xml
حقيقي (لا صفحة وظائف مفردة فقط كما كان سابقًا). دوال نقية بمحاكاة http_get
عبر monkeypatch — بلا شبكة فعلية ولا قاعدة بيانات.

تشغيل: cd core && python -m pytest tests/test_sitemap_jsonld.py -v
"""
from __future__ import annotations

import httpx
import pytest

from app.collectors import sitemap_jsonld


def _response(text: str, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, text=text, request=httpx.Request("GET", "https://example.com/"))


def _allow_all_robots(monkeypatch: pytest.MonkeyPatch) -> None:
    """يعطّل جلب robots.txt الحقيقي — كل الاختبارات هنا تفترض سماحًا (سلوك
    urllib.robotparser الافتراضي حين لا يوجد robots.txt أصلًا)، والحالة
    التي تختبر الحجب صراحةً تُعرِّف monkeypatch خاصًا بها بدل هذا."""
    monkeypatch.setattr(sitemap_jsonld, "_robots_parser", lambda url, timeout: None)


_SINGLE_PAGE_HTML = """
<html><head>
<script type="application/ld+json">
{"@type": "JobPosting", "title": "Process Engineer", "identifier": "PE-1",
 "url": "https://example.com/careers/process-engineer",
 "jobLocation": {"address": {"addressLocality": "Riyadh", "addressCountry": "SA"}},
 "datePosted": "2026-09-01"}
</script>
</head><body></body></html>
"""


def test_fetch_jobs_single_html_page_unchanged_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    """توافق رجعي: صفحة HTML مفردة (لا sitemap) تبقى تعمل كالسابق تمامًا —
    كل مصادر sitemap_jsonld الحالية بـdata/sources_seed.csv تشير لصفحات
    مفردة، ويجب ألا يكسر هذا التعديل أيًا منها."""
    _allow_all_robots(monkeypatch)
    monkeypatch.setattr(sitemap_jsonld, "http_get", lambda url, **kw: _response(_SINGLE_PAGE_HTML))

    jobs = sitemap_jsonld.fetch_jobs("https://example.com/careers/process-engineer")
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Process Engineer"
    assert jobs[0]["location"] == "Riyadh, SA"


_URLSET_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/careers/jobs/process-engineer</loc></url>
  <url><loc>https://example.com/careers/jobs/quality-engineer</loc></url>
  <url><loc>https://example.com/about-us</loc></url>
</urlset>
"""

_JOB_PAGE_A = """
<script type="application/ld+json">
{"@type": "JobPosting", "title": "Process Engineer", "identifier": "A1",
 "jobLocation": {"address": {"addressLocality": "Jeddah", "addressCountry": "SA"}}}
</script>
"""

_JOB_PAGE_B = """
<script type="application/ld+json">
{"@type": "JobPosting", "title": "Quality Engineer", "identifier": "B1",
 "jobLocation": {"address": {"addressLocality": "Dammam", "addressCountry": "SA"}}}
</script>
"""


def test_fetch_jobs_real_sitemap_xml_crawls_job_pages_and_aggregates(monkeypatch: pytest.MonkeyPatch) -> None:
    """جوهر تنفيذ B2-close: sitemap.xml حقيقي (جذر <urlset>) — يُنتقى منه
    رابطا الوظائف فقط (يُستبعد /about-us بلا كلمة مفتاحية متعلقة بوظيفة)،
    ثم تُقرأ كل صفحة ويُجمَّع JSON-LD منها."""
    _allow_all_robots(monkeypatch)

    def fake_get(url: str, **kw):
        if url == "https://example.com/sitemap.xml":
            return _response(_URLSET_XML)
        if url == "https://example.com/careers/jobs/process-engineer":
            return _response(_JOB_PAGE_A)
        if url == "https://example.com/careers/jobs/quality-engineer":
            return _response(_JOB_PAGE_B)
        raise AssertionError(f"لم يُتوقَّع جلب: {url}")

    monkeypatch.setattr(sitemap_jsonld, "http_get", fake_get)

    jobs = sitemap_jsonld.fetch_jobs("https://example.com/sitemap.xml")
    titles = {j["title"] for j in jobs}
    assert titles == {"Process Engineer", "Quality Engineer"}
    # الصفحة غير المتعلقة بوظيفة (about-us) لم تُجلَب إطلاقًا (لا AssertionError)


_SITEMAPINDEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap-jobs.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sitemap-blog.xml</loc></sitemap>
</sitemapindex>
"""

_SUB_URLSET_JOBS = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/careers/jobs/hse-specialist</loc></url>
</urlset>
"""

_SUB_URLSET_BLOG = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/blog/post-1</loc></url>
  <url><loc>https://example.com/blog/post-2</loc></url>
</urlset>
"""

_JOB_PAGE_C = """
<script type="application/ld+json">
{"@type": "JobPosting", "title": "HSE Specialist", "identifier": "C1",
 "jobLocation": {"address": {"addressLocality": "Jubail", "addressCountry": "SA"}}}
</script>
"""


def test_fetch_jobs_sitemapindex_follows_sub_sitemaps_one_level(monkeypatch: pytest.MonkeyPatch) -> None:
    """sitemapindex (ملف يشير لعدة sitemaps فرعية) يُتَّبع بعمق واحد — نمط
    شائع لمواقع الشركات الكبرى (sitemap.xml عام يشير لـsitemap-jobs.xml
    منفصل)."""
    _allow_all_robots(monkeypatch)

    def fake_get(url: str, **kw):
        if url == "https://example.com/sitemap-index.xml":
            return _response(_SITEMAPINDEX_XML)
        if url == "https://example.com/sitemap-jobs.xml":
            return _response(_SUB_URLSET_JOBS)
        if url == "https://example.com/sitemap-blog.xml":
            return _response(_SUB_URLSET_BLOG)
        if url == "https://example.com/careers/jobs/hse-specialist":
            return _response(_JOB_PAGE_C)
        raise AssertionError(f"لم يُتوقَّع جلب: {url}")

    monkeypatch.setattr(sitemap_jsonld, "http_get", fake_get)

    jobs = sitemap_jsonld.fetch_jobs("https://example.com/sitemap-index.xml")
    assert len(jobs) == 1
    assert jobs[0]["title"] == "HSE Specialist"
    # كلا الـsitemaps الفرعيين (jobs وblog) يُفحصان فعليًا لأن sitemapindex لا
    # يعرف مسبقًا أيهما متعلق بالوظائف — لكن روابط blog.xml لا تحمل أي كلمة
    # مفتاحية متعلقة بوظيفة، فيُستبعدان بالترجيح النهائي بعد الدمج (يوجد
    # رابط آخر مطابق بالمجموعة الكلية)، ولا تُجلَب صفحاتهما الفردية إطلاقًا
    # (لولا ذلك لرُفع AssertionError من fake_get أعلاه).


def test_fetch_jobs_respects_robots_disallow(monkeypatch: pytest.MonkeyPatch) -> None:
    """robots.txt يمنع الجلب صراحةً → ValueError، بلا أي محاولة جلب أخرى."""
    from urllib.robotparser import RobotFileParser

    parser = RobotFileParser()
    parser.parse(["User-agent: *", "Disallow: /careers/"])
    monkeypatch.setattr(sitemap_jsonld, "_robots_parser", lambda url, timeout: parser)

    def fail_get(url: str, **kw):
        raise AssertionError("لا يجب أي جلب حين يمنع robots.txt")

    monkeypatch.setattr(sitemap_jsonld, "http_get", fail_get)

    with pytest.raises(ValueError, match="robots.txt"):
        sitemap_jsonld.fetch_jobs("https://example.com/careers/jobs/process-engineer")


def test_is_sitemap_xml_detects_root_tag_not_extension() -> None:
    """الكشف يعتمد محتوى الاستجابة الفعلي (جذر <urlset>/<sitemapindex>) لا
    امتداد الرابط — بعض المواقع تخدم sitemap بمسار بلا .xml ظاهر."""
    assert sitemap_jsonld._is_sitemap_xml(_URLSET_XML) is True
    assert sitemap_jsonld._is_sitemap_xml(_SITEMAPINDEX_XML) is True
    assert sitemap_jsonld._is_sitemap_xml(_SINGLE_PAGE_HTML) is False


def test_select_job_like_urls_falls_back_to_all_when_no_hint_matches() -> None:
    """sitemap صغير بلا أي كلمة مفتاحية متعلقة بوظيفة بأي رابط — الملاذ
    الأخير: إرجاع الكل بدل قائمة فارغة (أفضل من تفويت مصدر فعلي)."""
    urls = ["https://example.com/a", "https://example.com/b"]
    assert sitemap_jsonld._select_job_like_urls(urls, limit=10) == urls


def test_select_job_like_urls_caps_at_limit() -> None:
    urls = [f"https://example.com/careers/job-{i}" for i in range(100)]
    result = sitemap_jsonld._select_job_like_urls(urls, limit=5)
    assert len(result) == 5
