"""Metrics stage: the run database and the dashboard view.

Two artifacts:

* ``data/run_metrics.jsonl`` -- the database. One comprehensive JSON record per
  pipeline run (stage funnel, per-stage token usage, emails, subscribers,
  models). Append/update by ``run_id``. This is the source of truth meant to be
  queried and re-visualised in new ways later.
* ``docs/_data/dashboard.json`` -- one view over that database, rendered by the
  ``/dashboard/`` page. Contains today's full run, per-day aggregates, and
  all-time totals/rates.

The stage is non-critical: a failure is recorded but never fails the run.
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
DEFAULT_DB_FILE = "data/run_metrics.jsonl"
DEFAULT_BIASES_FILE = "docs/_data/source_biases.json"


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


def read_bias_counts(path: str) -> dict[str, int]:
    """Count bias observations per day from source_biases.json date_added."""
    data = _load_json(Path(path), {})
    counts: dict[str, int] = {}
    if not isinstance(data, dict):
        return counts
    for info in data.values():
        biases = info.get("biases", []) if isinstance(info, dict) else []
        for bias in biases:
            added = bias.get("date_added")
            if added:
                counts[added] = counts.get(added, 0) + 1
    return counts


def read_audit(run_dir: str) -> dict:
    """Aggregate the run's ``audit/llm_calls.jsonl`` by stage and model."""
    by_stage: dict[str, dict[str, int]] = {}
    models: list[str] = []
    total = {"input": 0, "output": 0, "thinking": 0, "calls": 0}
    path = Path(run_dir) / "audit" / "llm_calls.jsonl"
    if not path.exists():
        return {"by_stage": by_stage, "models": models, "total": total}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        stage = entry.get("stage", "unknown")
        usage = by_stage.setdefault(
            stage, {"input": 0, "output": 0, "thinking": 0, "calls": 0}
        )
        usage["input"] += entry.get("tokens_in", 0) or 0
        usage["output"] += entry.get("tokens_out", 0) or 0
        usage["thinking"] += entry.get("tokens_thinking", 0) or 0
        usage["calls"] += 1
        model = entry.get("model")
        if model and model not in models:
            models.append(model)
    for usage in by_stage.values():
        for key in ("input", "output", "thinking", "calls"):
            total[key] += usage[key]
    return {"by_stage": by_stage, "models": models, "total": total}


def _stage_list(meta: dict) -> list[dict]:
    stages = meta.get("stages", {}) if isinstance(meta.get("stages"), dict) else {}
    out = []
    for name, info in stages.items():
        info = info if isinstance(info, dict) else {}
        out.append({
            "name": name,
            "status": info.get("status", "unknown"),
            "duration_s": info.get("duration_s"),
            "error": info.get("error"),
            "degraded": bool(info.get("degraded")),
        })
    return out


def _funnel(meta: dict, posted_en: int, posted_fa: int) -> dict:
    stages = meta.get("stages", {}) if isinstance(meta.get("stages"), dict) else {}
    fetch = stages.get("fetch", {}) if isinstance(stages.get("fetch"), dict) else {}
    filt = stages.get("filter", {}) if isinstance(stages.get("filter"), dict) else {}
    track = (
        stages.get("track_developments", {})
        if isinstance(stages.get("track_developments"), dict)
        else {}
    )
    return {
        "fetched": meta.get("items_fetched") or fetch.get("total_items"),
        "included": meta.get("items_included") or filt.get("items_included"),
        "new": track.get("new"),
        "continuation": track.get("continuation"),
        "development": track.get("development"),
        "posted_en": posted_en,
        "posted_fa": posted_fa,
        "posted": posted_en + posted_fa,
    }


def build_run_record(
    date_str: str,
    *,
    run_meta: dict | None,
    en_post: str | None,
    fa_post: str | None,
    ledger: dict,
    subscribers: dict | None,
    audit: dict | None,
) -> dict:
    """Assemble the comprehensive per-run database record."""
    meta = run_meta or {}
    status, failed, degraded = day_status(meta) if run_meta else ("unknown", [], [])
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

    audit = audit or {"by_stage": {}, "models": [], "total": {}}
    subscribers = subscribers or {}
    subs_en = subscribers.get("en")
    subs_fa = subscribers.get("fa")
    subs_total = None
    if subs_en is not None or subs_fa is not None:
        subs_total = (subs_en or 0) + (subs_fa or 0)

    return {
        "run_id": meta.get("run_id"),
        "target_date": date_str,
        "backfill": bool(meta.get("backfill")),
        "started_at": meta.get("started_at"),
        "finished_at": meta.get("finished_at"),
        "duration_s": meta.get("total_duration_s"),
        "status": status,
        "failed_stages": failed,
        "degraded_stages": degraded,
        "errors": meta.get("errors", []),
        "stages": _stage_list(meta),
        "funnel": _funnel(meta, posted_en, posted_fa),
        "tokens": {
            "by_stage": audit.get("by_stage", {}),
            "total": audit.get("total", {}),
        },
        "models": audit.get("models", []),
        "posted_en": posted_en,
        "posted_fa": posted_fa,
        "posted": posted_en + posted_fa,
        "links_en": links_en,
        "links_fa": links_fa,
        "links": links_en + links_fa,
        "emails": emails,
        "subscribers_en": subs_en,
        "subscribers_fa": subs_fa,
        "subscribers": subs_total,
    }


# ---------------------------------------------------------------------------
# Database (append/update JSONL)
# ---------------------------------------------------------------------------


def load_run_records(db_file: str) -> list[dict]:
    path = Path(db_file)
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def append_run_record(db_file: str, record: dict) -> None:
    """Insert or replace a record by run_id, preserving file order."""
    records = load_run_records(db_file)
    run_id = record.get("run_id")
    replaced = False
    for i, existing in enumerate(records):
        if run_id and existing.get("run_id") == run_id:
            records[i] = record
            replaced = True
            break
    if not replaced:
        records.append(record)
    path = Path(db_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, sort_keys=True) for r in records) + "\n",
        encoding="utf-8",
    )


def latest_by_date(records: list[dict]) -> dict[str, dict]:
    """Pick the latest run per target date (max run_id sorts by time)."""
    out: dict[str, dict] = {}
    for rec in records:
        date = rec.get("target_date")
        if not date:
            continue
        cur = out.get(date)
        if cur is None or (rec.get("run_id") or "") >= (cur.get("run_id") or ""):
            out[date] = rec
    return out


# ---------------------------------------------------------------------------
# Dashboard view
# ---------------------------------------------------------------------------


def _read_post(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _day_from_record(rec: dict, en_post: str | None, fa_post: str | None, empty) -> dict:
    tokens = rec.get("tokens", {}) or {}
    total = tokens.get("total", {}) or {}
    emails = rec.get("emails", {})
    emails_total = sum(
        1 for lang in ("en", "fa") if (emails.get(lang) or {}).get("sent")
    )
    recipients = [
        (emails.get(lang) or {}).get("recipients") for lang in ("en", "fa")
    ]
    deliveries = (
        sum(r for r in recipients if isinstance(r, int))
        if any(isinstance(r, int) for r in recipients)
        else None
    )
    t_in, t_out, t_think = total.get("input"), total.get("output"), total.get("thinking")
    tokens_total = (
        (t_in or 0) + (t_out or 0) + (t_think or 0)
        if any(x is not None for x in (t_in, t_out, t_think))
        else None
    )
    return {
        "date": rec["target_date"],
        "run_id": rec.get("run_id"),
        "status": rec.get("status", "unknown"),
        "failed_stages": rec.get("failed_stages", []),
        "degraded_stages": rec.get("degraded_stages", []),
        "en": en_post is not None,
        "fa": fa_post is not None,
        "posted_en": rec.get("posted_en", 0),
        "posted_fa": rec.get("posted_fa", 0),
        "posted": rec.get("posted", 0),
        "links_en": rec.get("links_en", 0),
        "links_fa": rec.get("links_fa", 0),
        "links": rec.get("links", 0),
        "processed": (rec.get("funnel", {}) or {}).get("fetched"),
        "included": (rec.get("funnel", {}) or {}).get("included"),
        "new_stories": (rec.get("funnel", {}) or {}).get("new"),
        "continuations": (rec.get("funnel", {}) or {}).get("continuation"),
        "developments": (rec.get("funnel", {}) or {}).get("development"),
        "biases": 0,
        "emails": emails,
        "emails_total": emails_total,
        "deliveries": deliveries,
        "subscribers_en": rec.get("subscribers_en"),
        "subscribers_fa": rec.get("subscribers_fa"),
        "subscribers": rec.get("subscribers"),
        "tokens": {
            "input": total.get("input"),
            "output": total.get("output"),
            "thinking": total.get("thinking"),
            "calls": total.get("calls"),
        },
        "tokens_total": tokens_total,
        "empty": empty,
    }


def _day_from_posts(date_str: str, en_post, fa_post, ledger, empty) -> dict:
    sent = ledger.get(date_str, {}) if isinstance(ledger, dict) else {}
    posted_en = count_entries(en_post) if en_post else 0
    posted_fa = count_entries(fa_post) if fa_post else 0
    links_en = count_links(en_post) if en_post else 0
    links_fa = count_links(fa_post) if fa_post else 0
    emails = {
        lang: {
            "sent": lang in sent,
            "broadcast_id": (sent.get(lang) or {}).get("broadcast_id"),
            "recipients": (sent.get(lang) or {}).get("recipients"),
        }
        for lang in ("en", "fa")
    }
    emails_total = sum(1 for lang in ("en", "fa") if emails[lang]["sent"])
    recipients = [emails[lang]["recipients"] for lang in ("en", "fa")]
    deliveries = (
        sum(r for r in recipients if isinstance(r, int))
        if any(isinstance(r, int) for r in recipients)
        else None
    )
    return {
        "date": date_str,
        "run_id": None,
        "status": "unknown",
        "failed_stages": [],
        "degraded_stages": [],
        "en": en_post is not None,
        "fa": fa_post is not None,
        "posted_en": posted_en,
        "posted_fa": posted_fa,
        "posted": posted_en + posted_fa,
        "links_en": links_en,
        "links_fa": links_fa,
        "links": links_en + links_fa,
        "processed": None,
        "included": None,
        "new_stories": None,
        "continuations": None,
        "developments": None,
        "biases": 0,
        "emails": emails,
        "emails_total": emails_total,
        "deliveries": deliveries,
        "subscribers_en": None,
        "subscribers_fa": None,
        "subscribers": None,
        "tokens": {"input": None, "output": None, "thinking": None, "calls": None},
        "tokens_total": None,
        "empty": empty,
    }


def build_days(records: list[dict], en_dir: Path, fa_dir: Path, ledger: dict, empty_by_date: dict, bias_by_date: dict | None = None) -> list[dict]:
    by_date = latest_by_date(records)
    dates: set[str] = set(by_date)
    for d in en_dir.glob("*-daily-brief.md"):
        dates.add(d.name[:10])
    for d in fa_dir.glob("*-daily-brief.md"):
        dates.add(d.name[:10])
    dates.update(ledger.keys())
    bias_by_date = bias_by_date or {}

    days = []
    for date_str in sorted(dates):
        en_post = _read_post(en_dir / f"{date_str}-daily-brief.md")
        fa_post = _read_post(fa_dir / f"{date_str}-daily-brief.md")
        empty = empty_by_date.get(date_str)
        rec = by_date.get(date_str)
        if rec:
            day = _day_from_record(rec, en_post, fa_post, empty)
        else:
            day = _day_from_posts(date_str, en_post, fa_post, ledger, empty)
        day["biases"] = bias_by_date.get(date_str, 0)
        days.append(day)
    return days


def recompute_totals(days: list[dict], joins: dict | None = None) -> dict:
    """Aggregate per-day records into dashboard totals and rates."""
    days = sorted(days, key=lambda d: d.get("date") or "")
    if not days:
        return {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

    def _sum(key: str):
        vals = [d.get(key) for d in days if d.get(key) is not None]
        return sum(vals) if vals else None

    def _bool_count(key: str) -> int:
        return sum(1 for d in days if d.get(key))

    def _token(key: str):
        vals = [
            (d.get("tokens") or {}).get(key)
            for d in days
            if (d.get("tokens") or {}).get(key) is not None
        ]
        return sum(vals) if vals else None

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
        "new_stories": _sum("new_stories"),
        "continuations": _sum("continuations"),
        "developments": _sum("developments"),
        "biases": _sum("biases"),
        "emails_en": sum(1 for d in days if (d.get("emails") or {}).get("en", {}).get("sent")),
        "emails_fa": sum(1 for d in days if (d.get("emails") or {}).get("fa", {}).get("sent")),
        "deliveries": sum(deliveries_vals) if deliveries_vals else None,
        "subscribers_current": subscribers[-1] if subscribers else None,
        "subscribers_peak": max(subscribers) if subscribers else None,
        "days_failed": sum(1 for d in days if d.get("status") == "failed"),
        "days_degraded": sum(1 for d in days if d.get("status") == "degraded"),
        "days_empty": sum(1 for d in days if d.get("empty")),
        "tokens_input": _token("input"),
        "tokens_output": _token("output"),
        "tokens_thinking": _token("thinking"),
        "llm_calls": _token("calls"),
    }
    totals["emails_total"] = totals["emails_en"] + totals["emails_fa"]
    tin, tout, tthink = totals["tokens_input"], totals["tokens_output"], totals["tokens_thinking"]
    totals["tokens_total"] = (
        (tin or 0) + (tout or 0) + (tthink or 0) if (tin or tout or tthink) else None
    )

    if joins:
        totals["subscribers_total_ever"] = joins.get("distinct_emails")
    else:
        totals["subscribers_total_ever"] = None

    running = totals["days_running"] or 0

    def _rate(total):
        if total is None or running <= 0:
            return {"per_day": None, "per_week": None, "per_month": None}
        per_day = total / running
        return {
            "per_day": round(per_day, 1),
            "per_week": round(per_day * 7, 1),
            "per_month": round(per_day * 30.44, 1),
        }

    totals["rates"] = {
        "posted": _rate(totals["posted"]),
        "posted_en": _rate(totals["posted_en"]),
        "posted_fa": _rate(totals["posted_fa"]),
        "processed": _rate(totals["processed"]),
        "included": _rate(totals["included"]),
        "new_stories": _rate(totals["new_stories"]),
        "continuations": _rate(totals["continuations"]),
        "developments": _rate(totals["developments"]),
        "biases": _rate(totals["biases"]),
        "links": _rate(totals["links"]),
        "emails": _rate(totals["emails_total"]),
        "deliveries": _rate(totals["deliveries"]),
        "tokens_input": _rate(totals["tokens_input"]),
        "tokens_output": _rate(totals["tokens_output"]),
        "tokens_thinking": _rate(totals["tokens_thinking"]),
        "tokens_total": _rate(totals["tokens_total"]),
    }

    totals["entries_per_brief_en"] = (
        round(totals["posted_en"] / totals["briefs_en"], 1) if totals["briefs_en"] else None
    )
    totals["entries_per_brief_fa"] = (
        round(totals["posted_fa"] / totals["briefs_fa"], 1) if totals["briefs_fa"] else None
    )

    subs_pairs = [
        (d["date"], d["subscribers"]) for d in days if d.get("subscribers") is not None
    ]
    if len(subs_pairs) >= 2:
        span = _days_between(subs_pairs[0][0], subs_pairs[-1][0])
        delta = subs_pairs[-1][1] - subs_pairs[0][1]
        totals["subscribers_growth"] = {
            "total": delta,
            "per_week": round(delta / (span / 7), 1) if span > 0 else None,
        }
    else:
        totals["subscribers_growth"] = None

    return totals


def _days_between(first: str, last: str) -> int:
    try:
        a = datetime.strptime(first, "%Y-%m-%d")
        b = datetime.strptime(last, "%Y-%m-%d")
        return (b - a).days + 1
    except (TypeError, ValueError):
        return 0


def _today_from_day(day: dict) -> dict:
    """Synthesize a today-record from a derived day when no run record exists."""
    return {
        "target_date": day["date"],
        "run_id": None,
        "status": day.get("status", "unknown"),
        "failed_stages": day.get("failed_stages", []),
        "degraded_stages": day.get("degraded_stages", []),
        "stages": [],
        "funnel": {
            "fetched": day.get("processed"),
            "included": day.get("included"),
            "posted_en": day.get("posted_en"),
            "posted_fa": day.get("posted_fa"),
            "posted": day.get("posted"),
        },
        "tokens": {"by_stage": {}, "total": {}},
        "models": [],
        "posted_en": day.get("posted_en", 0),
        "posted_fa": day.get("posted_fa", 0),
        "posted": day.get("posted", 0),
        "links_en": day.get("links_en", 0),
        "links_fa": day.get("links_fa", 0),
        "links": day.get("links", 0),
        "emails": day.get("emails", {}),
        "subscribers": day.get("subscribers"),
        "duration_s": None,
        "finished_at": None,
        "recorded": False,
    }


def build_dashboard(records: list[dict], en_dir: Path, fa_dir: Path, ledger: dict, empty_by_date: dict, joins: dict | None, today_date: str | None, bias_by_date: dict | None = None) -> dict:
    days = build_days(records, en_dir, fa_dir, ledger, empty_by_date, bias_by_date)
    totals = recompute_totals(days, joins)
    by_date = latest_by_date(records)
    # "Today's run" is always present: the requested date's run, else the most
    # recent recorded run, else the most recent day derived from posts/ledger.
    today = None
    if today_date and today_date in by_date:
        today = dict(by_date[today_date])
        today["recorded"] = True
    elif by_date:
        today = dict(by_date[max(by_date)])
        today["recorded"] = True
    elif days:
        today = _today_from_day(days[-1])
    return {"generated_at": totals["generated_at"], "today": today, "days": days, "totals": totals}


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
    db_file = (
        metrics_cfg.get("db_file", DEFAULT_DB_FILE)
        if isinstance(metrics_cfg, dict)
        else DEFAULT_DB_FILE
    )

    site_dir = Path(config.publish.get("site_dir", "docs"))
    en_dir = site_dir / "_posts"
    fa_dir = site_dir / "_fa_posts"
    date_str = Path(run_dir).name[:10]

    run_meta = _load_json(Path(run_dir) / "run_meta.json", None)
    en_post = _read_post(en_dir / f"{date_str}-daily-brief.md")
    fa_post = _read_post(fa_dir / f"{date_str}-daily-brief.md")
    audit = read_audit(run_dir)

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
    empty_by_date = {
        h.get("date"): h.get("empty")
        for h in (streak.get("history", []) if isinstance(streak, dict) else [])
    }

    # Best-effort live stats.
    api_key = os.environ.get("RESEND_API_KEY", "")
    subscribers: dict = {}
    if isinstance(mailer_cfg, dict):
        subscribers["en"] = audience_count(api_key, mailer_cfg.get("audience_id_en", ""))
        subscribers["fa"] = audience_count(api_key, mailer_cfg.get("audience_id_fa", ""))
    stats_url, stats_token = configured_subscribe_stats_url(config)
    joins = subscribe_joins(stats_url, stats_token)

    record = build_run_record(
        date_str,
        run_meta=run_meta,
        en_post=en_post,
        fa_post=fa_post,
        ledger=ledger,
        subscribers=subscribers,
        audit=audit,
    )
    append_run_record(db_file, record)

    records = load_run_records(db_file)
    bias_by_date = read_bias_counts(str(site_dir / "_data" / "source_biases.json"))
    dashboard = build_dashboard(
        records, en_dir, fa_dir, ledger, empty_by_date, joins, today_date=date_str,
        bias_by_date=bias_by_date,
    )
    dashboard_path = Path(dashboard_file)
    dashboard_path.parent.mkdir(parents=True, exist_ok=True)
    dashboard_path.write_text(json.dumps(dashboard, indent=2) + "\n", encoding="utf-8")

    _git_commit_file(db_file, f"Record run metrics: {date_str}")
    _git_commit_file(dashboard_file, f"Update dashboard: {date_str}")
    logger.info(f"Metrics recorded for {date_str}: status={record['status']}")

    return {"status": "completed", "db_file": db_file, "dashboard_file": dashboard_file, "run": record}
