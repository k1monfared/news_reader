"""Metrics stage: builds ``docs/_data/dashboard.json`` for the site dashboard.

Runs last and is non-critical. It reads the run's ``run_meta.json``, the day's
English and Farsi posts, the broadcast ledger, and the empty-brief history,
plus best-effort Resend and subscribe-proxy stats, then records or updates that
day's entry and recomputes totals. The file is committed so the Jekyll
dashboard page refreshes after each run.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from models import PipelineConfig
from llm_client import AuditedLLMClient
from audit_logger import AuditedHTTPClient
from stats_client import (
    audience_count,
    configured_subscribe_stats_url,
    subscribe_joins,
)
from stages.empty_streak import _git_commit_file

logger = logging.getLogger(__name__)

CRITICAL_STAGES = {"fetch", "filter", "summarize", "render_check", "publish", "translate_fa"}

DETAILS_RE = re.compile(r"<details\b")
LINK_RE = re.compile(r"\]\((https?://[^)]+)\)")

DEFAULT_DASHBOARD_FILE = "docs/_data/dashboard.json"


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------


def count_entries(post_text: str) -> int:
    """Number of brief entries (one ``<details>`` block each)."""
    return len(DETAILS_RE.findall(post_text))


def count_links(post_text: str) -> int:
    """Number of unique source links cited in a post."""
    return len({m for m in LINK_RE.findall(post_text)})


def day_status(meta: dict) -> tuple[str, list[str], list[str]]:
    """Return ``(status, failed_stages, degraded_stages)`` for a run."""
    stages = meta.get("stages", {}) if isinstance(meta.get("stages"), dict) else {}
    failed = sorted(
        name for name, info in stages.items()
        if isinstance(info, dict) and info.get("status") == "failed"
    )
    degraded = sorted(
        name for name, info in stages.items()
        if isinstance(info, dict) and info.get("degraded")
    )
    failed_critical = [s for s in failed if s in CRITICAL_STAGES]
    if failed_critical:
        status = "failed"
    elif failed or degraded:
        status = "degraded"
    else:
        status = "success"
    return status, failed, degraded


def _read_post(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def build_day_record(
    date_str: str,
    *,
    run_meta: dict | None,
    en_post: str | None,
    fa_post: str | None,
    ledger: dict,
    subscribers: dict | None,
    empty: bool | None,
) -> dict:
    """Assemble one day's dashboard record from available sources."""
    meta = run_meta or {}
    status, failed, degraded = day_status(meta) if run_meta else ("unknown", [], [])
    stories = meta.get("stages", {}) if isinstance(meta.get("stages"), dict) else {}
    fetch = stories.get("fetch", {}) if isinstance(stories.get("fetch"), dict) else {}
    filtered = stories.get("filter", {}) if isinstance(stories.get("filter"), dict) else {}

    posted_en = count_entries(en_post) if en_post else 0
    posted_fa = count_entries(fa_post) if fa_post else 0
    links_en = count_links(en_post) if en_post else 0
    links_fa = count_links(fa_post) if fa_post else 0

    sent = ledger.get(date_str, {}) if isinstance(ledger, dict) else {}
    emails = {
        lang: {
            "sent": lang in sent,
            "broadcast_id": (sent.get(lang) or {}).get("broadcast_id"),
            "recipients": (sent.get(lang) or {}).get("recipients"),
        }
        for lang in ("en", "fa")
    }

    subscribers = subscribers or {}
    subs_en = subscribers.get("en")
    subs_fa = subscribers.get("fa")
    subs_total = None
    if subs_en is not None or subs_fa is not None:
        subs_total = (subs_en or 0) + (subs_fa or 0)

    return {
        "date": date_str,
        "run_id": meta.get("run_id"),
        "status": status,
        "failed_stages": failed,
        "degraded_stages": degraded,
        "en": en_post is not None,
        "fa": fa_post is not None,
        "posted_en": posted_en,
        "posted_fa": posted_fa,
        "posted": posted_en + posted_fa,
        "links_en": links_en,
        "links_fa": links_fa,
        "links": links_en + links_fa,
        "processed": meta.get("items_fetched") or fetch.get("total_items"),
        "included": meta.get("items_included") or filtered.get("included"),
        "emails": emails,
        "subscribers_en": subs_en,
        "subscribers_fa": subs_fa,
        "subscribers": subs_total,
        "models_used": meta.get("models_used") or [],
        "duration_s": meta.get("total_duration_s"),
        "empty": empty,
    }


def recompute_totals(days: list[dict], joins: dict | None = None) -> dict:
    """Aggregate per-day records into dashboard totals."""
    days = sorted(days, key=lambda d: d.get("date") or "")
    if not days:
        return {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

    def _sum(key: str):
        vals = [d.get(key) for d in days if d.get(key) is not None]
        return sum(vals) if vals else None

    def _bool_count(key: str) -> int:
        return sum(1 for d in days if d.get(key))

    subscribers = [d["subscribers"] for d in days if d.get("subscribers") is not None]
    deliveries_vals = [
        ((d.get("emails") or {}).get(lang) or {}).get("recipients")
        for d in days
        for lang in ("en", "fa")
        if isinstance(((d.get("emails") or {}).get(lang) or {}).get("recipients"), int)
    ]

    totals = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "first_date": days[0]["date"],
        "last_date": days[-1]["date"],
        "days_recorded": len(days),
        "days_running": _days_between(days[0]["date"], days[-1]["date"]),
        "briefs_en": _bool_count("en"),
        "briefs_fa": _bool_count("fa"),
        "posted": _sum("posted"),
        "posted_en": _sum("posted_en"),
        "posted_fa": _sum("posted_fa"),
        "links": _sum("links"),
        "processed": _sum("processed"),
        "included": _sum("included"),
        "emails_en": sum(1 for d in days if (d.get("emails") or {}).get("en", {}).get("sent")),
        "emails_fa": sum(1 for d in days if (d.get("emails") or {}).get("fa", {}).get("sent")),
        "deliveries": sum(deliveries_vals) if deliveries_vals else None,
        "subscribers_current": subscribers[-1] if subscribers else None,
        "subscribers_peak": max(subscribers) if subscribers else None,
        "days_failed": sum(1 for d in days if d.get("status") == "failed"),
        "days_degraded": sum(1 for d in days if d.get("status") == "degraded"),
        "days_empty": sum(1 for d in days if d.get("empty")),
    }
    totals["emails_total"] = totals["emails_en"] + totals["emails_fa"]
    if joins:
        totals["subscribers_total_ever"] = joins.get("distinct_emails")
    else:
        totals["subscribers_total_ever"] = None
    return totals


def _days_between(first: str, last: str) -> int:
    try:
        a = datetime.strptime(first, "%Y-%m-%d")
        b = datetime.strptime(last, "%Y-%m-%d")
        return (b - a).days + 1
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def run_metrics(
    run_dir: str,
    config: PipelineConfig,
    llm_client: AuditedLLMClient,
    http_client: AuditedHTTPClient,
) -> dict:
    metrics_cfg = getattr(config, "metrics", None) or {}
    if isinstance(metrics_cfg, dict) and metrics_cfg.get("enabled", True) is False:
        logger.info("metrics disabled; skipping.")
        return {"status": "skipped"}

    dashboard_file = (
        metrics_cfg.get("dashboard_file", DEFAULT_DASHBOARD_FILE)
        if isinstance(metrics_cfg, dict)
        else DEFAULT_DASHBOARD_FILE
    )
    dashboard_path = Path(dashboard_file)

    site_dir = Path(config.publish.get("site_dir", "docs"))
    date_str = Path(run_dir).name[:10]

    run_meta = _load_json(Path(run_dir) / "run_meta.json", None)
    en_post = _read_post(site_dir / "_posts" / f"{date_str}-daily-brief.md")
    fa_post = _read_post(site_dir / "_fa_posts" / f"{date_str}-daily-brief.md")

    mailer_cfg = getattr(config, "mailer", None) or {}
    ledger_file = (
        mailer_cfg.get("sent_state_file", "data/sent_broadcasts.json")
        if isinstance(mailer_cfg, dict)
        else "data/sent_broadcasts.json"
    )
    ledger = _load_json(Path(ledger_file), {})

    empty_cfg = getattr(config, "empty_brief", None) or {}
    streak = _load_json(
        Path(empty_cfg.get("state_file", "data/empty_streak.json"))
        if isinstance(empty_cfg, dict)
        else Path("data/empty_streak.json"),
        {},
    )
    empty = None
    for entry in reversed(streak.get("history", []) if isinstance(streak, dict) else []):
        if entry.get("date") == date_str:
            empty = bool(entry.get("empty"))
            break

    # Best-effort live stats (today's run only).
    api_key = os.environ.get("RESEND_API_KEY", "")
    subscribers: dict = {}
    if isinstance(mailer_cfg, dict):
        subscribers["en"] = audience_count(api_key, mailer_cfg.get("audience_id_en", ""))
        subscribers["fa"] = audience_count(api_key, mailer_cfg.get("audience_id_fa", ""))
    stats_url, stats_token = configured_subscribe_stats_url(config)
    joins = subscribe_joins(stats_url, stats_token)

    dashboard = _load_json(dashboard_path, {"days": []})
    days = [d for d in dashboard.get("days", []) if d.get("date") != date_str]
    record = build_day_record(
        date_str,
        run_meta=run_meta,
        en_post=en_post,
        fa_post=fa_post,
        ledger=ledger,
        subscribers=subscribers,
        empty=empty,
    )
    days.append(record)
    totals = recompute_totals(days, joins)
    payload = {"totals": totals, "days": sorted(days, key=lambda d: d.get("date") or "")}

    dashboard_path.parent.mkdir(parents=True, exist_ok=True)
    dashboard_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _git_commit_file(dashboard_file, f"Update dashboard metrics: {date_str}")
    logger.info(f"Dashboard updated for {date_str}: status={record['status']}")

    return {"status": "completed", "dashboard_file": dashboard_file, "day": record}
