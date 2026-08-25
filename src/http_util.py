"""HTTP helpers: rate limiting + retry with exponential backoff."""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests

log = logging.getLogger("http")


class RateLimiter:
    def __init__(self, per_sec: float):
        self.min_interval = 1.0 / per_sec if per_sec > 0 else 0.0
        self._last = 0.0

    def wait(self):
        if self.min_interval <= 0:
            return
        now = time.monotonic()
        delta = now - self._last
        if delta < self.min_interval:
            time.sleep(self.min_interval - delta)
        self._last = time.monotonic()


def request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    *,
    limiter: Optional[RateLimiter] = None,
    max_retries: int = 4,
    **kwargs,
) -> requests.Response:
    """Issue an HTTP request, retrying on 429/5xx with exponential backoff."""
    attempt = 0
    while True:
        if limiter:
            limiter.wait()
        try:
            resp = session.request(method, url, timeout=60, **kwargs)
        except requests.RequestException as exc:
            if attempt >= max_retries:
                raise
            backoff = min(2 ** attempt, 30)
            log.warning("Network error %s; retry in %ss (%d/%d)", exc, backoff, attempt + 1, max_retries)
            time.sleep(backoff)
            attempt += 1
            continue

        if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
            retry_after = resp.headers.get("Retry-After")
            backoff = float(retry_after) if retry_after and retry_after.isdigit() else min(2 ** attempt, 30)
            log.warning("HTTP %s on %s; retry in %ss (%d/%d)",
                        resp.status_code, url, backoff, attempt + 1, max_retries)
            time.sleep(backoff)
            attempt += 1
            continue
        return resp
