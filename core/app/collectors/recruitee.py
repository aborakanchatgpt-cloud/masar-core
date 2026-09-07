"""
جامع Recruitee — واجهة JSON عامة بدون مصادقة:

    https://{company}.recruitee.com/api/offers/

company هو الاسم الظاهر بنطاق صفحة الوظائف الفرعي (subdomain).
"""
from __future__ import annotations

import httpx

RECRUITEE_OFFERS_URL = "https://{company}.recruitee.com/api/offers/"
_HEADERS = {"User-Agent": "MasarCoreBot/0.1 (+contact via masar)"}


def fetch_jobs(company_subdomain: str, timeout: float = 20.0) -> list[dict]:
    """يرجع قائمة وظائف خام من Recruitee لشركة واحدة (company_subdomain)."""
    url = RECRUITEE_OFFERS_URL.format(company=company_subdomain)
    response = httpx.get(url, timeout=timeout, headers=_HEADERS)
    response.raise_for_status()
    payload = response.json()

    jobs: list[dict] = []
    for item in payload.get("offers", []):
        jobs.append(
            {
                "external_id": item.get("id") or item.get("slug"),
                "title": item.get("title"),
                "url": item.get("careers_url"),
                "location": item.get("location") or item.get("city"),
                "updated_at": item.get("published_at") or item.get("updated_at"),
                "raw": item,
            }
        )
    return jobs
