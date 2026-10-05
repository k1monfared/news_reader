"""Discover, probe, and persist the free models the pipeline can use.

This closes the loop that ``scripts/probe_models.py`` only half-implemented:
the probe listed and tested free models but nothing consumed the result. This
script probes both catalogs, keeps the free models that answer on the
``chat/completions`` route the pipeline uses, and writes ``models.json``.
``AuditedLLMClient`` reads that file and tries the selected models first.

``models.json`` records a base URL per model and the client switches base per
entry, so one selection can span the Go and Zen catalogs.

Usage (CI):
  python scripts/select_models.py --out models.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.probe_models import (
        ALLOWLIST,
        classify,
        discover,
        probe_chat,
        probe_responses,
    )
except ImportError:  # run directly: python scripts/select_models.py
    from probe_models import (  # type: ignore[no-redef]
        ALLOWLIST,
        classify,
        discover,
        probe_chat,
        probe_responses,
    )

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("select_models")

DEFAULT_BASES = [
    "https://opencode.ai/zen/go/v1",
    "https://opencode.ai/zen/v1",
]

# Fallback candidate ids when a catalog's /models endpoint is unavailable.
SEED_CANDIDATES = {
    "https://opencode.ai/zen/go/v1": [
        "longcat-2.5-preview-free",
        "space-bunny-free",
    ],
    "https://opencode.ai/zen/v1": [
        "big-pickle",
        "mimo-v2.6-flash-free",
        "mimo-v2.5-free",
        "ling-3.1-flash-free",
        "ling-3.0-flash-fin-free",
        "nemotron-3-ultra-free",
        "nemotron-3.5-lightning-free",
        "fledge-alpha-free",
        "muse-spark-1.3-contributor-free",
    ],
}


def candidates_for_base(base: str, live_ids: list[str]) -> list[str]:
    """Free-model candidates for a base: live catalog ids plus seeds."""
    candidates = sorted(
        i for i in live_ids if i.endswith("-free") or i in ALLOWLIST
    )
    for seed in SEED_CANDIDATES.get(base, []):
        if seed not in candidates:
            candidates.append(seed)
    return candidates


def probe_base(
    base: str, key: str, session_id: str, max_tokens: int
) -> tuple[list[dict], bool]:
    """Probe every free candidate on ``base``.

    Returns ``(results, discovered)`` where ``discovered`` is False when the
    catalog's model list could not be fetched (used to avoid overwriting a
    good selection during a network outage).
    """
    try:
        live_ids = discover(base)
        discovered = True
    except Exception as exc:
        logger.warning(f"Discovery failed for {base}: {exc}; using seed candidates")
        live_ids = []
        discovered = False

    results: list[dict] = []
    for model in candidates_for_base(base, live_ids):
        status, body = probe_chat(base, key, model, session_id, max_tokens)
        outcome, retry = classify(status, body, "chat/completions")
        route = "chat/completions"
        if retry:
            s2, b2 = probe_responses(base, key, model, session_id, max_tokens)
            o2, _ = classify(s2, b2, "responses")
            if o2 == "OK":
                outcome, route = "OK", "responses"
            else:
                outcome = o2
        results.append({
            "id": model,
            "base_url": base,
            "route": route,
            "outcome": outcome,
            "status": status,
        })
        logger.info(f"{model:40s} @ {base} -> {outcome} ({route})")
    return results, discovered


def select_from_probes(probes: list[dict]) -> list[dict]:
    """Keep free models that answered on the chat/completions route.

    ``AuditedLLMClient`` only speaks ``/chat/completions``, so a model that
    only works on ``/responses`` is skipped despite being free. Dedup is by
    model id: a model served on both catalogs is kept once, on the first base
    that answered (bases are probed Go first, so the pipeline's own base wins
    over the Zen mirror).
    """
    selected: list[dict] = []
    seen_ids: set[str] = set()
    for probe in probes:
        if probe.get("outcome") != "OK" or probe.get("route") != "chat/completions":
            continue
        base = probe.get("base_url", "")
        model_id = probe.get("id", "")
        if not model_id or model_id in seen_ids:
            continue
        seen_ids.add(model_id)
        selected.append({"id": model_id, "base_url": base})
    return selected


def _account_or_transport_failure(outcome: str) -> bool:
    """True for outcomes that mean we could not really judge the model:
    bad/expired key (401/403), account funds (402), or transport failure."""
    return outcome.startswith(("HTTP_401", "HTTP_402", "HTTP_403", "HTTP_-1"))


def write_models_json(
    path: str, bases: list[str], selected: list[dict], probes: list[dict]
) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bases_probed": bases,
        "selected": selected,
        "probes": probes,
    }
    Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="models.json")
    parser.add_argument("--bases", default=",".join(DEFAULT_BASES))
    parser.add_argument(
        "--session-id",
        default=os.environ.get("OPENCODE_SESSION_ID", "news-reader-model-select"),
    )
    parser.add_argument("--max-tokens", type=int, default=64)
    args = parser.parse_args()

    key = os.environ.get("OPENCODE_API_KEY", "")
    if not key:
        print(
            "KEY_MISSING: OPENCODE_API_KEY is empty; cannot probe models.",
            file=sys.stderr,
        )
        return 2

    bases = [b.strip().rstrip("/") for b in args.bases.split(",") if b.strip()]
    all_probes: list[dict] = []
    any_discovered = False
    for base in bases:
        probes, discovered = probe_base(base, key, args.session_id, args.max_tokens)
        all_probes.extend(probes)
        any_discovered = any_discovered or discovered

    if not all_probes:
        print(f"NO_PROBES: no candidates on {bases}; leaving {args.out} unchanged.", file=sys.stderr)
        return 3

    selected = select_from_probes(all_probes)
    blocked = all(
        _account_or_transport_failure(p.get("outcome", "")) for p in all_probes
    )
    if not selected and (not any_discovered or blocked):
        print(
            "NO_CATALOG: catalogs were unreachable or the key was rejected; "
            f"leaving {args.out} unchanged.",
            file=sys.stderr,
        )
        return 3

    write_models_json(args.out, bases, selected, all_probes)
    names = ", ".join(m["id"] for m in selected) or "NONE"
    print(f"Selected {len(selected)} free model(s): {names} -> {args.out}")
    if not selected:
        print(
            "NONE_ACCESSIBLE: no free model answered on chat/completions; "
            "the pipeline will use the static config chain.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
