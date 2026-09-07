"""
جامع SmartRecruiters — واجهة JSON عامة بدون مصادقة:

    https://api.smartrecruiters.com/v1/companies/{company_id}/postings

company_id هو المعرّف الظاهر بصفحة الوظائف العامة لدى SmartRecruiters
(غالبًا نفس اسم الشركة كما يظهر بروابط careers.smartrecruiters.com/<company_id>
أو jobs.smartrecruiters.com/<company_id>).

ملاحظة: صفحة توثيق SmartRecruiters نفسها محجوبة عن الزحف الآلي (robots.txt)،
لكن واجهة الـ API البرمجية نفسها مخصصة للاستهلاك المباشر (موثّقة كواجهة
عامة بدون مصادقة بعدة مصادر مستقلة) وليست الصفحة الوثائقية ذاتها.

ملاحظة B2 (إصلاح): معاينة فعلية أظهرت أن بعض شركات SmartRecruiters ترجع قيمة مختلفة
لـ "location" أو "ref" (مثلاً nested بصيغة غير متوقعة أو سلسلة فارغة) بدلاً من dict،
مما كان يفشّل جميع وظائف الشركة بخطأ 'str' object has no attribute 'get' عند أول
عنصر غير متوقّع. الحل هنا: تحقق isinstance(..., dict) قبل استدعاء .get() على أي
حقل فرعي محتمل، بدل افتراض شكله دومًا — وظيفة واحدة مشوّهة لا تُسقط المصدر كاملاً.
"""
from __future__ import annotations

import httpx

SMARTRECRUITERS_POSTINGS_URL = "https://api.smartrecruiters.com/v1/companies/{company}/postings"
_HEADERS = {"User-Agent": "MasarCoreBot/0.1 (+contact via masar)"}


def fetch_jobs(company_id: str, timeout: float = 20.0) -> list[dict]:
    """يرجع قائمة وظائف خام من SmartRecruiters لشركة واحدة (company_id)."""
    url = SMARTRECRUITERS_POSTINGS_URL.format(company=company_id)
    response = httpx.get(url, timeout=timeout, headers=_HEADERS)
    response.raise_for_status()
    payload = response.json()

    jobs: list[dict] = []
    for item in payload.get("content", []):
        if not isinstance(item, dict):
            continue
        location = item.get("location")
        location = location if isinstance(location, dict) else {}
        ref = item.get("ref")
        ref = ref if isinstance(ref, dict) else {}
        apply_url = ref.get("jobAd") or item.get("applyUrl")
        if not isinstance(apply_url, str):
            apply_url = None
        jobs.append(
            {
                "external_id": item.get("id"),
                "title": item.get("name"),
                "url": apply_url,
                "location": ", ".join(
                    part for part in [location.get("city"), location.get("country")]
                    if isinstance(part, str) and part
                )
                or None,
                "updated_at": item.get("releasedDate"),
                "raw": item,
            }
        )
    return jobs
