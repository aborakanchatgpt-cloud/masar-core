"""
جامع Greenhouse — أحد أنظمة ATS الأكثر شيوعًا. يوفّر واجهة JSON عامة بدون
مصادقة لكل شركة تستضيف صفحة وظائفها عبر Greenhouse:

    https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true

board_token عادة هو الاسم الظاهر برابط صفحة الوظائف
(مثال: boards.greenhouse.io/<board_token>).
"""
from __future__ import annotations

import httpx

GREENHOUSE_JOBS_URL = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"


def fetch_jobs(board_token: str, timeout: float = 20.0) -> list[dict]:
    """يرجع قائمة وظائف خام من Greenhouse لشركة واحدة (board_token)."""
    url = GREENHOUSE_JOBS_URL.format(board=board_token)
    response = httpx.get(url, params={"content": "true"}, timeout=timeout)
    response.raise_for_status()
    payload = response.json()

    jobs: list[dict] = []
    for item in payload.get("jobs", []):
        location = item.get("location") or {}
        jobs.append(
            {
                "external_id": str(item.get("id")),
                "title": item.get("title"),
                "url": item.get("absolute_url"),
                "location": location.get("name"),
                "updated_at": item.get("updated_at"),
                "raw": item,
            }
        )
    return jobs
