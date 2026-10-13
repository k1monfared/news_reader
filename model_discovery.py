"""Resolve the free-model backstop from the live catalog at run start.

The pipeline's primary model is a paid one. This module discovers the free
models that currently answer on the Go catalog, so the backstop never goes
stale. ``refresh_models_file`` writes the result to ``models.json``, the same
file ``AuditedLLMClient`` reads, and leaves it untouched when discovery finds
nothing so the last known good set survives a catalog outage.
"""

from __future__ import annotations

import logging

from scripts.probe_models import classify, discover, probe_chat
from scripts.select_models import write_models_json

logger = logging.getLogger(__name__)


def discover_free_models(
    base_url: str,
    api_key: str,
    session_id: str = "",
    *,
    timeout_s: int = 10,
) -> list[dict]:
    """Probe the catalog's free models and return the ones that answer.

    Returns ``[{"id": ..., "base_url": ...}]`` for free models that returned
    non-empty content on ``/chat/completions``. Returns ``[]`` on any catalog
    or transport failure.
    """
    if not api_key:
        return []
    try:
        live_ids = discover(base_url)
    except Exception as exc:
        logger.warning(f"Model discovery failed for {base_url}: {exc}")
        return []

    selected: list[dict] = []
    for model in sorted(i for i in live_ids if i.endswith("-free")):
        try:
            status, body = probe_chat(
                base_url, api_key, model, session_id, timeout_s=timeout_s
            )
        except Exception as exc:
            logger.warning(f"Probe failed for {model}: {exc}")
            continue
        outcome, _retry = classify(status, body, "chat/completions")
        if outcome == "OK":
            selected.append({"id": model, "base_url": base_url})
    return selected


def refresh_models_file(
    path: str,
    base_url: str,
    api_key: str,
    session_id: str = "",
    *,
    timeout_s: int = 10,
) -> list[dict]:
    """Discover free models and cache them in ``models.json``.

    Writes the file only when at least one free model answered, so a failed
    discovery leaves the existing fallback cache in place. Returns the
    discovered list.
    """
    selected = discover_free_models(
        base_url, api_key, session_id, timeout_s=timeout_s
    )
    if selected:
        write_models_json(path, [base_url], selected, probes=[])
    return selected
