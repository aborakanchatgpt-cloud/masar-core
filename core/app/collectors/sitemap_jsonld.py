"""
جامع عام (fallback) — يقرأ صفحة وظائف أي موقع ويستخرج بيانات schema.org
JobPosting المُضمّنة كـ JSON-LD (ممارسة SEO شائعة جدًا لظهور الوظائف بـ Google
Jobs، ويعمل مع أغلب أنظمة ATS تقريبًا — بما فيها الشركات التي تستضيف صفحات
وظائفها على Teamtailor أو BambooHR أو Workday أو صفحة مخصّصة، حين لا تتوفر
واجهة JSON عامة موثّقة لتلك المنصة تحديدًا).

هذا الجامع هو الحل المعتمد بالدليل لمثل هذه الحالات (بدل جامع مخصص لكل نظام
لا يوفّر API عام)، وهو أيضًا مصدر جيد لاكتشاف روابط وظائف إضافية عبر أي موقع
شركة عادي غير مرتبط بأي ATS معروف.
"""
from __future__ import annotations

import json
import re

import httpx

_JSONLD_SCRIPT_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def _iter_jobposting_nodes(data):
    """يمشي على أي بنية JSON-LD (كائن مفرد، قائمة، أو @graph) ويُرجع عقد JobPosting فقط."""
    if isinstance(data, list):
        for item in data:
            yield from _iter_jobposting_nodes(item)
        return
    if not isinstance(data, dict):
        return

    node_type = data.get("@type")
    type_names = node_type if isinstance(node_type, list) else [node_type]
    if any(str(t).lower() == "jobposting" for t in type_names if t):
        yield data

    graph = data.get("@graph")
    if graph:
        yield from _iter_jobposting_nodes(graph)


def _extract_location(job_location) -> str | None:
    if isinstance(job_location, list):
        job_location = job_location[0] if job_location else None
    if not isinstance(job_location, dict):
        return None
    address = job_location.get("address")
    if isinstance(address, dict):
        parts = [address.get("addressLocality"), address.get("addressCountry")]
        joined = ", ".join(p for p in parts if p)
        return joined or None
    return None


def fetch_jobs(career_page_url: str, timeout: float = 20.0) -> list[dict]:
    """يجلب صفحة وظائف واحدة ويستخرج كل عقد JobPosting (schema.org) الموجودة بها."""
    response = httpx.get(
        career_page_url,
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0 (compatible; MasarCoreBot/0.1)"},
        follow_redirects=True,
    )
    response.raise_for_status()
    html = response.text

    jobs: list[dict] = []
    for raw_block in _JSONLD_SCRIPT_RE.findall(html):
        try:
            data = json.loads(raw_block.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        for node in _iter_jobposting_nodes(data):
            hiring_org = node.get("hiringOrganization") or {}
            jobs.append(
                {
                    "external_id": node.get("identifier", {}).get("value")
                    if isinstance(node.get("identifier"), dict)
                    else node.get("identifier"),
                    "title": node.get("title"),
                    "url": node.get("url") or career_page_url,
                    "location": _extract_location(node.get("jobLocation")),
                    "updated_at": node.get("datePosted"),
                    "company_name_hint": hiring_org.get("name") if isinstance(hiring_org, dict) else None,
                    "raw": node,
                }
            )
    return jobs
