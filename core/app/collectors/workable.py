"""
جامع Workable — واجهة widget عامة بدون مصادقة (بدون بحث/تصفية، تُرجع كل الوظائف
المنشورة للحساب):

    https://apply.workable.com/api/v1/widget/accounts/{account_shortcode}

account_shortcode هو الاسم الظاهر برابط صفحة الوظائف
(مثال: apply.workable.com/<account_shortcode>).

مراجعة B2 (docs/reports/B2-review.md) R1: حمولة widget's لا تحوي مفتاح
"location" إطلاقًا (كان الكود القديم يقرأ `item.get("location")` فيرجع None
دائمًا — أصاب 2082 وظيفة/15 مصدرًا). الحقول الفعلية المؤكَّدة من raw_json:
`city`, `state`/`region`, `country` مباشرة على عنصر الوظيفة، وأحيانًا
`telecommuting: true` بدل موقع فعلي (عمل عن بُعد).
"""
from __future__ import annotations

from app.collectors.http_client import get as http_get

WORKABLE_WIDGET_URL = "https://apply.workable.com/api/v1/widget/accounts/{account}"


def _build_location(item: dict) -> str | None:
    """يبني نص الموقع من حقول Workable الفعلية (city/region/state/country)،
    أو 'Remote' إن كانت telecommuting=true بلا مدينة محدَّدة."""
    parts = [
        item.get("city"),
        item.get("region") or item.get("state"),
        item.get("country"),
    ]
    text = ", ".join(p for p in parts if isinstance(p, str) and p.strip())
    if text:
        return text
    if item.get("telecommuting"):
        return "Remote"
    return None


def fetch_jobs(account_shortcode: str, timeout: float = 20.0) -> list[dict]:
    """يرجع قائمة وظائف خام من Workable لحساب واحد (account_shortcode)."""
    url = WORKABLE_WIDGET_URL.format(account=account_shortcode)
    response = http_get(url, timeout=timeout)
    response.raise_for_status()
    payload = response.json()

    jobs: list[dict] = []
    for item in payload.get("jobs", []):
        jobs.append(
            {
                "external_id": item.get("shortcode") or item.get("id"),
                "title": item.get("title"),
                "url": item.get("url"),
                "location": _build_location(item),
                "updated_at": item.get("published_on") or item.get("created_at"),
                "raw": item,
            }
        )
    return jobs
