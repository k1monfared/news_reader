"""Backfill ``data/sent_broadcasts.json`` from Resend broadcast history.

Resend's List Broadcasts endpoint returns every broadcast (name, segment,
sent_at) and the Broadcasts Metrics endpoint returns per-broadcast sent and
delivered counts. This reconstructs the ledger for past days so the dashboard's
email and delivery totals cover history, not just from when the ledger was
introduced.

Usage:
  RESEND_API_KEY=... python scripts/backfill_broadcasts.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import load_config

API_BASE = "https://api.resend.com"
NAME_RE = re.compile(r"daily-brief-(en|fa)-(\d{4}-\d{2}-\d{2})")


def parse_broadcast(
    name: str, segment_id: str, sent_at: str, audience_map: dict[str, str]
) -> tuple[str, str] | None:
    """Return ``(date, lang)`` for a broadcast, or None if it is not ours."""
    match = NAME_RE.search(name or "")
    if match:
        return match.group(2), match.group(1)
    lang = audience_map.get(segment_id)
    if not lang or not sent_at:
        return None
    # sent_at is like "2026-12-02 19:32:22.980+00"; the brief date is the day.
    date = sent_at[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return None
    return date, lang


def list_broadcasts(api_key: str, timeout: float = 30.0) -> list[dict]:
    out: list[dict] = []
    headers = {"Authorization": f"Bearer {api_key}"}
    with httpx.Client(timeout=timeout) as client:
        after: str | None = None
        for _ in range(100):
            params = {"limit": 100}
            if after:
                params["after"] = after
            resp = client.get(f"{API_BASE}/broadcasts", headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("data") or []
            out.extend(items)
            if not data.get("has_more") or not items:
                break
            after = items[-1].get("id")
            if not after:
                break
    return out


def broadcast_metrics(
    api_key: str, start_date: str, end_date: str, timeout: float = 30.0
) -> dict[str, dict]:
    """Return ``{broadcast_id: {sent, delivered}}``; empty on any failure.

    Tries the broadcast metrics endpoint first (private beta) and falls back to
    the general email metrics endpoint grouped by broadcast.
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    params = {
        "start_date": start_date,
        "end_date": end_date,
        "metrics": "sent,delivered",
        "dimensions": "broadcast",
    }
    for path in ("/broadcasts/metrics", "/emails/metrics"):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(f"{API_BASE}{path}", headers=headers, params=params)
        except (httpx.HTTPError, ValueError) as exc:
            print(f"WARNING: {path} request failed: {exc}", file=sys.stderr)
            continue
        if resp.status_code != 200:
            print(
                f"WARNING: {path} unavailable ({resp.status_code})",
                file=sys.stderr,
            )
            continue
        result: dict[str, dict] = {}
        for row in resp.json().get("data") or []:
            bid = row.get("id")
            if bid:
                result[bid] = {"sent": row.get("sent"), "delivered": row.get("delivered")}
        if result:
            print(f"Metrics from {path}: {len(result)} broadcast(s)")
            return result
    return {}


def build_ledger(
    broadcasts: list[dict], metrics: dict[str, dict], audience_map: dict[str, str]
) -> dict:
    """Turn Resend broadcasts + metrics into the date -> lang -> record ledger."""
    ledger: dict = {}
    for b in broadcasts:
        if b.get("status") and b.get("status") != "sent":
            continue
        parsed = parse_broadcast(
            b.get("name", ""),
            b.get("segment_id") or b.get("audience_id") or "",
            b.get("sent_at") or "",
            audience_map,
        )
        if not parsed:
            continue
        date, lang = parsed
        m = metrics.get(b.get("id"), {})
        recipients = m.get("delivered")
        if recipients is None:
            recipients = m.get("sent")
        entry = {
            "broadcast_id": b.get("id"),
            "recipients": recipients,
            "sent": m.get("sent"),
            "delivered": m.get("delivered"),
            "sent_at": b.get("sent_at"),
        }
        # Keep the latest broadcast for a date+lang (re-sends).
        existing = ledger.get(date, {}).get(lang)
        if existing and (existing.get("sent_at") or "") > (entry.get("sent_at") or ""):
            continue
        ledger.setdefault(date, {})[lang] = entry
    return ledger


def merge_ledgers(existing: dict, new: dict) -> dict:
    """Merge new into existing; existing-only entries are preserved."""
    merged = {d: dict(v) for d, v in existing.items()}
    for date, langs in new.items():
        merged.setdefault(date, {})
        for lang, rec in langs.items():
            old = merged[date].get(lang, {})
            combined = {**old, **rec}
            # Don't lose an existing recipient count if Resend had none.
            if combined.get("recipients") is None and old.get("recipients") is not None:
                combined["recipients"] = old["recipients"]
            merged[date][lang] = combined
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if not api_key:
        print("RESEND_API_KEY not set; cannot backfill broadcasts.", file=sys.stderr)
        return 2

    config = load_config(args.config)
    mailer = config.mailer or {}
    audience_map = {}
    if mailer.get("audience_id_en"):
        audience_map[mailer["audience_id_en"]] = "en"
    if mailer.get("audience_id_fa"):
        audience_map[mailer["audience_id_fa"]] = "fa"

    ledger_file = mailer.get("sent_state_file", "data/sent_broadcasts.json")
    existing = {}
    if Path(ledger_file).exists():
        existing = json.loads(Path(ledger_file).read_text(encoding="utf-8"))

    broadcasts = list_broadcasts(api_key)
    print(f"Fetched {len(broadcasts)} broadcast(s) from Resend")

    sent_dates = [b.get("sent_at", "")[:10] for b in broadcasts if b.get("sent_at")]
    start = min(sent_dates) if sent_dates else "2026-01-01"
    end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    metrics = broadcast_metrics(api_key, start, end)
    print(f"Fetched metrics for {len(metrics)} broadcast(s)")

    new = build_ledger(broadcasts, metrics, audience_map)
    merged = merge_ledgers(existing, new)
    Path(ledger_file).write_text(
        json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    days = sum(len(v) for v in merged.values())
    print(f"Wrote {ledger_file}: {len(merged)} date(s), {days} broadcast(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
