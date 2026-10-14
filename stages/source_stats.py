"""Per-run per-source funnel counters.

Each pipeline stage adds the counters it knows to ``source_stats.json`` in the
run directory. Int counters are summed, booleans are overwritten. A missing or
malformed file is treated as empty so a partial run never crashes the merge.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

STATS_FILE = "source_stats.json"


def _path(run_dir: str) -> Path:
    return Path(run_dir) / STATS_FILE


def read_source_stats(run_dir: str) -> dict[str, dict]:
    path = _path(run_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        name: counters
        for name, counters in data.items()
        if isinstance(counters, dict)
    }


def update_source_stats(run_dir: str, source: str, **counters) -> None:
    """Merge counters for one source into the run's source_stats.json."""
    stats = read_source_stats(run_dir)
    entry = stats.setdefault(source, {})
    for key, value in counters.items():
        if isinstance(value, bool):
            entry[key] = value
        elif isinstance(value, (int, float)):
            entry[key] = entry.get(key, 0) + value
        else:
            entry[key] = value
    path = _path(run_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stats, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as exc:
        logger.warning(f"Could not write source stats for {source}: {exc}")


def count_by_source(items, predicate) -> dict[str, int]:
    """Count items per item.source where predicate(item) is true."""
    counts: dict[str, int] = {}
    for item in items:
        if predicate(item):
            name = item.get("source") if isinstance(item, dict) else getattr(item, "source", None)
            if name:
                counts[name] = counts.get(name, 0) + 1
    return counts


def summarize_posted_counts(items) -> dict[str, int]:
    """Count each item's source and its related sources as 'posted'."""
    counts: dict[str, int] = {}
    for item in items:
        src = item.get("source") if isinstance(item, dict) else getattr(item, "source", None)
        related = (item.get("related_sources") if isinstance(item, dict)
                   else getattr(item, "related_sources", [])) or []
        for name in [src, *related]:
            if name:
                counts[name] = counts.get(name, 0) + 1
    return counts
