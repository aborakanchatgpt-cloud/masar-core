"""
اختبارات core/app/collectors/rss.py — تنفيذ B2-close. الوحدة كانت بلا أي
اختبار مخصّص سابقًا رغم كونها إحدى وحدتي التغطية السعودية المغلقة المصدر
المطلوبتين بالتكليف (sitemap_jsonld + rss). تُغطّي هنا: تحليل RSS 2.0،
تحليل Atom، استخراج الموقع الاختياري من وسم صريح حين متوفرًا (إضافة
B2-close)، وسلوك عدم وجود موقع صريح (الملاذ الأخير — استخراج لاحق من
العنوان/الوصف بـfield_extractor.py، خارج نطاق هذه الوحدة).

تشغيل: cd core && python -m pytest tests/test_rss_collector.py -v
"""
from __future__ import annotations

import httpx
import pytest

from app.collectors import rss


def _response(text: str) -> httpx.Response:
    return httpx.Response(200, text=text, request=httpx.Request("GET", "https://example.com/jobs.rss"))


_RSS_NO_LOCATION_TAG = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Careers</title>
    <item>
      <title>Mechanical Sales Engineer</title>
      <link>https://example.teamtailor.com/jobs/123-mechanical-sales-engineer</link>
      <guid>123</guid>
      <pubDate>Mon, 08 Sep 2026 10:00:00 GMT</pubDate>
      <description>Headquarter - Jeddah, Al Bawadi, Saudi Arabia</description>
    </item>
  </channel>
</rss>
"""


def test_fetch_jobs_rss_2_0_parses_items(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rss, "http_get", lambda url, **kw: _response(_RSS_NO_LOCATION_TAG))
    jobs = rss.fetch_jobs("https://example.com/jobs.rss")
    assert len(jobs) == 1
    job = jobs[0]
    assert job["title"] == "Mechanical Sales Engineer"
    assert job["url"] == "https://example.teamtailor.com/jobs/123-mechanical-sales-engineer"
    assert job["external_id"] == "123"
    # تنفيذ B2-close: لا وسم <location> صريح بهذا العنصر → الملاذ الأخير None
    # (يُستخرَج لاحقًا من نص الوصف عبر field_extractor.extract_cities/compute_region
    # في discovery.py — خارج نطاق هذه الوحدة، مغطّى باختبارات field_extractor).
    assert job["location"] is None
    assert "description" in job["raw"]


_RSS_WITH_LOCATION_TAG = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Control Engineer</title>
      <link>https://example.com/jobs/456</link>
      <guid>456</guid>
      <location>Riyadh, Saudi Arabia</location>
      <pubDate>Tue, 09 Sep 2026 10:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


def test_fetch_jobs_extracts_explicit_location_tag_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """تنفيذ B2-close: خلاصة تحمل وسم <location> صريحًا لكل عنصر — يُستخرَج
    مباشرة بدل الاعتماد فقط على النص الحر لاحقًا."""
    monkeypatch.setattr(rss, "http_get", lambda url, **kw: _response(_RSS_WITH_LOCATION_TAG))
    jobs = rss.fetch_jobs("https://example.com/jobs.rss")
    assert len(jobs) == 1
    assert jobs[0]["location"] == "Riyadh, Saudi Arabia"


_ATOM_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>atom-1</id>
    <title>Quality Engineer</title>
    <link href="https://example.com/jobs/atom-1"/>
    <updated>2026-09-09T10:00:00Z</updated>
  </entry>
</feed>
"""


def test_fetch_jobs_atom_feed_parses_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rss, "http_get", lambda url, **kw: _response(_ATOM_FEED))
    jobs = rss.fetch_jobs("https://example.com/atom.xml")
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Quality Engineer"
    assert jobs[0]["url"] == "https://example.com/jobs/atom-1"
    assert jobs[0]["external_id"] == "atom-1"


def test_fetch_jobs_empty_channel_returns_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    empty_rss = '<?xml version="1.0"?><rss version="2.0"><channel><title>Empty</title></channel></rss>'
    monkeypatch.setattr(rss, "http_get", lambda url, **kw: _response(empty_rss))
    assert rss.fetch_jobs("https://example.com/jobs.rss") == []


def test_fetch_jobs_unrecognized_root_returns_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """لا <channel> ولا جذر ينتهي بـ"feed" (مستند XML غير متعلق بوظائف
    إطلاقًا) → قائمة فارغة، بلا استثناء."""
    monkeypatch.setattr(rss, "http_get", lambda url, **kw: _response("<root><x>1</x></root>"))
    assert rss.fetch_jobs("https://example.com/not-a-feed.xml") == []
