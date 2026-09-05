"""
جامع Ashby — واجهة JSON عامة بدون مصادقة (تم التحقق منها فعليًا خلال البناء):

    https://api.ashbyhq.com/posting-api/job-board/{job_board_name}

job_board_name هو الاسم الظاهر برابط صفحة الوظائف (jobs.ashbyhq.com/<name>).
"""
from __future__ import annotations

import httpx

ASHBY_JOB_BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/{board}"


def fetch_jobs(job_board_name: str, timeout: float = 20.0) -> list[dict]:
    """يرجع قائمة وظائف خام من Ashby للوحة وظائف واحدة (job_board_name)."""
    url = ASHBY_JOB_BOARD_URL.format(board=job_board_name)
    response = httpx.get(url, timeout=timeout)
    response.raise_for_status()
    payload = response.json()

    jobs: list[dict] = []
    for item in payload.get("jobs", []):
        jobs.append(
            {
                "external_id": item.get("id"),
                "title": item.get("title"),
                "url": item.get("jobUrl") or item.get("applyUrl"),
                "location": item.get("location"),
                "updated_at": item.get("publishedAt"),
                "raw": item,
            }
        )
    return jobs
