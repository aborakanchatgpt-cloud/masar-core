"""
عميل HTTP مشترك لكل الجوامع (core/app/collectors/*) — مراجعة B2
(docs/reports/B2-review.md) R8/R9:

    R8: ترويسة User-Agent موحّدة تحمل رابط تواصل فعليًا (بدل نص عام
        "+contact via masar" غير القابل للنقر/المراسلة).
    R9: تحديد معدّل صريح لكل مضيف (≥1 ثانية بين طلبين لنفس المضيف)،
        وإعادة محاولة بتراجع أُسّي عند 429/أخطاء 5xx (حتى 3 محاولات).

جولة الجامع الكاملة متسلسلة أصلًا (مصدر واحد بالمرة، core/app/discovery.py،
لا تزامن) فقاموس بسيط لآخر وقت طلب لكل مضيف كافٍ تمامًا — لا حاجة لقفل
متعدد الخيوط أو Redis.
"""
from __future__ import annotations

import time
from urllib.parse import urlparse

import httpx

USER_AGENT = (
    "MasarCoreBot/0.1 "
    "(+https://github.com/aborakanchatgpt-cloud/masar-core; contact: admin via Telegram)"
)
DEFAULT_HEADERS = {"User-Agent": USER_AGENT}

MIN_HOST_INTERVAL_SECONDS = 1.0
MAX_RETRIES = 3
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

_last_request_at: dict[str, float] = {}


def _throttle(host: str) -> None:
    """ينام إن لزم حتى يمرّ ≥MIN_HOST_INTERVAL_SECONDS منذ آخر طلب لنفس المضيف."""
    last = _last_request_at.get(host)
    now = time.monotonic()
    if last is not None:
        wait = MIN_HOST_INTERVAL_SECONDS - (now - last)
        if wait > 0:
            time.sleep(wait)
    _last_request_at[host] = time.monotonic()


def get(
    url: str,
    *,
    timeout: float = 20.0,
    params: dict | None = None,
    headers: dict | None = None,
    follow_redirects: bool = False,
) -> httpx.Response:
    """GET بتحديد معدّل لكل مضيف + تراجع أُسّي (1s، 2s) عند 429/5xx أو خطأ
    شبكة، حتى 3 محاولات إجمالًا. يرفع آخر استثناء إن فشلت كل المحاولات
    (يُعامَل كفشل مصدر واحد بجولة discovery.py، بلا إسقاط الجولة كاملة)."""
    host = urlparse(url).netloc
    merged_headers = {**DEFAULT_HEADERS, **(headers or {})}

    response: httpx.Response | None = None
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        _throttle(host)
        try:
            response = httpx.get(
                url,
                params=params,
                timeout=timeout,
                headers=merged_headers,
                follow_redirects=follow_redirects,
            )
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                time.sleep(2 ** (attempt - 1))
                continue
            raise
        if response.status_code in _RETRYABLE_STATUS and attempt < MAX_RETRIES:
            time.sleep(2 ** (attempt - 1))
            continue
        return response

    if last_exc:
        raise last_exc
    assert response is not None  # pragma: no cover — لا يصل هنا فعليًا
    return response
