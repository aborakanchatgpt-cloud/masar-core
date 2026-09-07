"""
جامع عام (fallback) — يقرأ صفحة وظائف أي موقع ويستخرج بيانات schema.org
JobPosting المُضمّنة كـ JSON-LD (ممارسة SEO شائعة جدًا لظهور الوظائف بـ Google
Jobs، ويعمل مع أغلب أنظمة ATS تقريبًا — بما فيها الشركات التي تستضيف صفحات
وظائفها على Teamtailor أو BambooHR أو Workday أو صفحة مخصّصة، حين لا تتوفر
واجهة JSON عامة موثّقة لتلك المنصة تحديدًا).

هذا الجامع هو الحل المعتمد بالدليل لمثل هذه الحالات (بدل جامع مخصص لكل نظام
لا يوفّر API عام)، وهو أيضًا مصدر جيد لاكتشاف روابط وظائف إضافية عبر أي موقع
شركة عادي غير مرتبط بأي ATS معروف.

B2: يحترم robots.txt فعليًا قبل الجلب (Disallow لمسار الصفحة المطلوبة تحت
 User-agent: * أو المطابق لاسمنا) — إن مُنع الجلب يرفع ValueError بدل الجلب.
يستخدم http_client المشترك (مراجعة B2 R8/R9: User-Agent موحّد + تحديد معدّل
لكل مضيف + تراجع أُسّي عند 429/5xx).
"""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from app.collectors.http_client import USER_AGENT, get as http_get

_JSONLD_SCRIPT_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def _robots_allow(url: str, timeout: float) -> bool:
    """يتحقق من robots.txt للنطاق قبل الجلب. أي فشل بجلب/تحليل robots.txt
    نفسه (لا يوجد، أو خطأ شبكة) يُعامل كسماح ضمني (سلوك urllib.robotparser
    القياسي)، لا كحجب — لا نمنع الجلب لمجرد تعذّر قراءة الملف."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        response = http_get(robots_url, timeout=timeout)
        if response.status_code >= 400:
            return True
        parser.parse(response.text.splitlines())
    except Exception:  # noqa: BLE001 — تعذّر قراءة robots.txt لا يعني حجبًا
        return True
    return parser.can_fetch(USER_AGENT, url)


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
    if not _robots_allow(career_page_url, timeout):
        raise ValueError(f"robots.txt يمنع الجلب: {career_page_url}")

    response = http_get(career_page_url, timeout=timeout, follow_redirects=True)
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
            job_url = node.get("url") or career_page_url
            jobs.append(
                {
                    "external_id": node.get("identifier", {}).get("value")
                    if isinstance(node.get("identifier"), dict)
                    else node.get("identifier"),
                    "title": node.get("title"),
                    "url": urljoin(career_page_url, job_url) if job_url else career_page_url,
                    "location": _extract_location(node.get("jobLocation")),
                    "updated_at": node.get("datePosted"),
                    "company_name_hint": hiring_org.get("name") if isinstance(hiring_org, dict) else None,
                    "raw": node,
                }
            )
    return jobs
