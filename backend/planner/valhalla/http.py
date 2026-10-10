"""Transport: bounded-retry HTTP calls against Valhalla."""

from __future__ import annotations

import time as _time

import httpx

from core import constants
from core.config import settings
from core.errors import UpstreamUnavailable


def request_with_retry(
    method: str,
    url: str,
    *,
    params: dict,
    timeout: float,
    retries: int | None = None,
) -> dict:
    """GET with bounded retries on transient failures.

    Exhausted retries raise UpstreamUnavailable so main.py can return 503.
    """
    if retries is None:
        retries = constants.VALHALLA_MAX_RETRIES
    last_exc: Exception | None = None
    for attempt in range(1, retries + 2):
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.request(method, url, params=params)
                if r.status_code >= 400:
                    raise httpx.HTTPStatusError(
                        f"server error: {r.text[:300]}", request=r.request, response=r
                    )
                return r.json()
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.HTTPStatusError) as e:
            last_exc = e
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status is not None and 400 <= status < 500 and status != 429:
                break
            if attempt > retries:
                break
            _time.sleep(0.5 * attempt)
    raise UpstreamUnavailable(f"valhalla {method} {url} failed after retries: {last_exc}")


def ping(timeout: float = 2.0) -> bool:
    """Cheap liveness check: GET /status. Used by /health."""
    try:
        request_with_retry(
            "GET",
            f"{settings.VALHALLA_URL.rstrip('/')}/status",
            params={},
            timeout=timeout,
        )
        return True
    except Exception:
        return False
