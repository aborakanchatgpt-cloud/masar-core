"""
جامع RSS/Atom عام — بعض الشركات وبعض لوحات الوظائف تنشر خلاصة RSS/Atom لوظائفها
الجديدة. يعمل هذا الجامع بدون أي مكتبة خارجية (xml.etree من المكتبة القياسية)
لتجنّب إضافة تبعية جديدة لأمر شائع نسبيًا.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx

_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def _text(el, tag, namespaces=None) -> str | None:
    found = el.find(tag, namespaces) if namespaces else el.find(tag)
    return found.text.strip() if found is not None and found.text else None


def fetch_jobs(feed_url: str, timeout: float = 20.0) -> list[dict]:
    """يجلب خلاصة RSS أو Atom واحدة ويحوّل كل عنصر لصيغة وظيفة موحّدة."""
    response = httpx.get(feed_url, timeout=timeout)
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
                    "location": None,
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
