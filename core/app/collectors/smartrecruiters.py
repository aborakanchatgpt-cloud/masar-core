"""
جامع SmartRecruiters — واجهة JSON عامة بدون مصادقة:

    https://api.smartrecruiters.com/v1/companies/{company_id}/postings

company_id هو المعرّف الظاهر بصفحة الوظائف العامة لدى SmartRecruiters
(غالبًا نفس اسم الشركة كما يظهر بروابط careers.smartrecruiters.com/<company_id>).

ملاحظة: صفحة توثيق SmartRecruiters نفسها محجوبة عن الزحف الآلي (robots.txt)،
لكن واجهة الـ API البرمجية نفسها مخصصة للاستهلاك المباشر (موثّقة كواجهة
عامة بدون مصادقة بعدة مصادر مستقلة) وليست الصفحة الوثائقية ذاتها.
"""
from __future__ import annotations

import httpx

SMARTRECRUITERS_POSTINGS_URL = "https://api.smartrecruiters.com/v1/companies/{company}/postings"


def fetch_jobs(company_id: str, timeout: float = 20.0) -> list[dict]:
    """يرجع قائمة وظائف خام من SmartRecruiters لشركة واحدة (company_id)."""
    url = SMARTRECRUITERS_POSTINGS_URL.format(company=company_id)
    response = httpx.get(url, timeout=timeout)
    response.raise_for_status()
    payload = response.json()

    jobs: list[dict] = []
    for item in payload.get("content", []):
        location = item.get("location") or {}
        jobs.append(
            {
                "external_id": item.get("id"),
                "title": item.get("name"),
                "url": (item.get("ref") or {}).get("jobAd") or item.get("applyUrl"),
                "location": ", ".join(
                    part for part in [location.get("city"), location.get("country")] if part
                )
                or None,
                "updated_at": item.get("releasedDate"),
                "raw": item,
            }
        )
    return jobs
