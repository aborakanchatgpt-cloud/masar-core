"""
جامع Workable — واجهة widget عامة بدون مصادقة (بدون بحث/تصفية، تُرجع كل الوظائف
المنشورة للحساب):

    https://apply.workable.com/api/v1/widget/accounts/{account_shortcode}

account_shortcode هو الاسم الظاهر برابط صفحة الوظائف
(مثال: apply.workable.com/<account_shortcode>).
"""
from __future__ import annotations

import httpx

WORKABLE_WIDGET_URL = "https://apply.workable.com/api/v1/widget/accounts/{account}"


def fetch_jobs(account_shortcode: str, timeout: float = 20.0) -> list[dict]:
    """يرجع قائمة وظائف خام من Workable لحساب واحد (account_shortcode)."""
    url = WORKABLE_WIDGET_URL.format(account=account_shortcode)
    response = httpx.get(url, timeout=timeout)
    response.raise_for_status()
    payload = response.json()

    jobs: list[dict] = []
    for item in payload.get("jobs", []):
        jobs.append(
            {
                "external_id": item.get("shortcode") or item.get("id"),
                "title": item.get("title"),
                "url": item.get("url"),
                "location": item.get("location"),
                "updated_at": item.get("published_on") or item.get("created_at"),
                "raw": item,
            }
        )
    return jobs
