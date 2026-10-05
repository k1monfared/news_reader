"""Best-effort stats providers for the dashboard.

Everything here is optional and failure-tolerant: the dashboard must still
build when Resend or the subscribe-proxy is unreachable, so each function
returns ``None`` instead of raising.

Sources:
  * Resend audience contacts  -> current subscriber count per language.
  * subscribe-proxy /stats    -> cumulative distinct emails ever subscribed
    (the Resend API only exposes the current list, not historical joins).
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)


def audience_count(
    api_key: str, audience_id: str, *, timeout: float = 20.0
) -> int | None:
    """Return the number of contacts in a Resend audience, or None.

    Paginates the contacts endpoint because Resend does not return a total.
    Capped to avoid unbounded pagination on a very large list.
    """
    if not api_key or not audience_id:
        return None
    total = 0
    url = f"https://api.resend.com/audiences/{audience_id}/contacts"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        with httpx.Client(timeout=timeout) as client:
            after: str | None = None
            for _ in range(100):  # 100 pages * 100 contacts = 10k ceiling
                params = {"limit": 100}
                if after:
                    params["after"] = after
                resp = client.get(url, headers=headers, params=params)
                if resp.status_code != 200:
                    logger.warning(
                        f"Resend contacts list failed ({resp.status_code}); "
                        "subscriber count unavailable"
                    )
                    return None
                data = resp.json()
                items = data.get("data") or []
                total += len(items)
                if not data.get("has_more") or not items:
                    return total
                after = items[-1].get("id")
                if not after:
                    return total
    except httpx.HTTPError as exc:
        logger.warning(f"Resend contacts request failed: {exc}")
        return None
    return total


def subscribe_joins(stats_url: str, token: str = "", *, timeout: float = 20.0) -> dict | None:
    """Fetch cumulative subscribe stats from the subscribe-proxy.

    Expects JSON like ``{"distinct_emails": 123, "by_list": {"en": 100, "fa": 23}}``.
    Returns None when unconfigured or unreachable.
    """
    if not stats_url:
        return None
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(stats_url, headers=headers)
            if resp.status_code != 200:
                logger.warning(
                    f"subscribe-proxy stats failed ({resp.status_code}); "
                    "cumulative joins unavailable"
                )
                return None
            data = resp.json()
            return data if isinstance(data, dict) else None
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(f"subscribe-proxy stats request failed: {exc}")
        return None


def configured_subscribe_stats_url(config: object) -> tuple[str, str]:
    """Read the stats URL and token from the metrics config section."""
    metrics = getattr(config, "metrics", None) or {}
    if isinstance(metrics, dict):
        url = metrics.get("subscribe_stats_url", "") or ""
        token = os.environ.get("SUBSCRIBE_STATS_TOKEN", "") or metrics.get(
            "subscribe_stats_token", ""
        )
        return str(url), str(token)
    return "", os.environ.get("SUBSCRIBE_STATS_TOKEN", "")
