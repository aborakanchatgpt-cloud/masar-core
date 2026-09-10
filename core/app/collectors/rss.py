"""
جامع RSS/Atom عام — بعض الشركات وبعض لوحات الوظائف تنشر خلاصة RSS/Atom لوظائفها
الجديدة. يعمل هذا الجامع بدون أي مكتبة خارجية (xml.etree من المكتبة القياسية)
لتجنّب إضافة تبعية جديدة لأمر شائع نسبيًا.

تنفيذ B2-close (تغطية سعودية مغلقة المصدر بلا كشط): هذه الوحدة كانت أصلًا
عامة بالكامل — أي مصدر `sources.source_type='rss'` يشير لأي خلاصة RSS/Atom
عامة (لأي شركة تنشرها، عبر أي منصّة توظيف تدعمها مثل Teamtailor
`{company}.teamtailor.com/jobs.rss` الموثَّقة رسميًا) يعمل معها بلا أي
تعديل — تحقّق فعلي بـ`docs/reports/B2-close-executor.md` §2 ضد خلاصات
Teamtailor حيّة فعلية (Samir Group بجدة، Chalhoub Group). الإضافة الوحيدة
هنا: بعض خلاصات RSS للوظائف (نمط شائع لدى بعض منصّات ATS، وإن لم نرصده صراحة
بعيّنة Teamtailor المُتحقَّقة) تحمل حقل موقع صريح ضمن عنصر `<item>` (وسم
مخصّص كـ`<location>` أو `<job:location>`) بدل الاعتماد فقط على استخراج المدينة
من نص العنوان/الوصف لاحقًا بـfield_extractor.py — `_extract_item_location()`
تحاول عدة أسماء وسوم شائعة أولًا، وترجع `None` إن لم يوجد أي منها (السلوك
السابق، بلا تغيير) ليبقى الاستخراج من العنوان/الوصف الملاذ الأخير كالمعتاد.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from app.collectors.http_client import get as http_get

_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

# تنفيذ B2-close: أسماء وسوم موقع شائعة ببعض خلاصات RSS للوظائف (لا معيار
# رسمي واحد بين منصّات ATS) — تُجرَّب بالترتيب، أول وسم موجود وغير فارغ يُستخدم.
_LOCATION_TAG_CANDIDATES = ("location", "job:location", "geo:location", "region")


def _text(el, tag, namespaces=None) -> str | None:
    found = el.find(tag, namespaces) if namespaces else el.find(tag)
    return found.text.strip() if found is not None and found.text else None


def _extract_item_location(item) -> str | None:
    for tag in _LOCATION_TAG_CANDIDATES:
        value = _text(item, tag)
        if value:
            return value
    return None


def fetch_jobs(feed_url: str, timeout: float = 20.0) -> list[dict]:
    """يجلب خلاصة RSS أو Atom واحدة ويحوّل كل عنصر لصيغة وظيفة موحّدة."""
    response = http_get(feed_url, timeout=timeout)
    response.raise_for_status()
    root = ET.fromstring(response.content)

    jobs: list[dict] = []

    # RSS 2.0: <rss><channel><item>...
    channel = root.find("channel")
    if channel is not None:
        for item in channel.findall("item"):
            jobs.append(
                {
                    "external_id": _text(item, "guid"),
                    "title": _text(item, "title"),
                    "url": _text(item, "link"),
                    "location": _extract_item_location(item),
                    "updated_at": _text(item, "pubDate"),
                    "raw": {child.tag: (child.text or "") for child in item},
                }
            )
        return jobs

    # Atom: <feed><entry>...
    if root.tag.endswith("feed"):
        for entry in root.findall("atom:entry", _ATOM_NS):
            link_el = entry.find("atom:link", _ATOM_NS)
            jobs.append(
                {
                    "external_id": _text(entry, "atom:id", _ATOM_NS),
                    "title": _text(entry, "atom:title", _ATOM_NS),
                    "url": link_el.get("href") if link_el is not None else None,
                    "location": None,
                    "updated_at": _text(entry, "atom:updated", _ATOM_NS),
                    "raw": {},
                }
            )
        return jobs

    return jobs
