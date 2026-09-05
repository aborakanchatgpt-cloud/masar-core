"""
جامع Lever — نظام ATS شائع آخر بواجهة JSON عامة بدون مصادقة:

    https://api.lever.co/v0/postings/{company_slug}?mode=json

company_slug هو الاسم الظاهر برابط صفحة الوظائف
(مثال: jobs.lever.co/<company_slug>).
"""
from __future__ import annotations

import httpx

LEVER_POSTINGS_URL = "https://api.lever.co/v0/postings/{company}"


def fetch_jobs(company_slug: str, timeout: float = 20.0) -> list[dict]:
    """يرجع قائمة وظائف خام من Lever لشركة واحدة (company_slug)."""
    url = LEVER_POSTINGS_URL.format(company=company_slug)
    response = httpx.get(url, params={"mode": "json"}, timeout=timeout)
    response.raise_for_status()
    payload = response.json()

    jobs: list[dict] = []
    for item in payload:
        categories = item.get("categories") or {}
        jobs.append(
            {
                "external_id": item.get("id"),
                "title": item.get("text"),
                "url": item.get("hostedUrl"),
                "location": categories.get("location"),
                "updated_at": item.get("createdAt"),
                "raw": item,
            }
        )
    return jobs
