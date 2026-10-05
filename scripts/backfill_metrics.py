"""Rebuild ``docs/_data/dashboard.json`` from committed history and the run DB.

Reads ``data/run_metrics.jsonl`` (real runs), plus the posts, broadcast ledger,
and empty-streak history, and writes the dashboard view. No network calls:
historical subscriber counts and processed-link counts stay null, and the
existing total-ever subscriber count is preserved.

Usage:
  python scripts/backfill_metrics.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import load_config
from stages.metrics import (
    DEFAULT_DASHBOARD_FILE,
    DEFAULT_DB_FILE,
    build_dashboard,
    load_run_records,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    config = load_config(args.config)
    site_dir = Path(config.publish.get("site_dir", "docs"))
    metrics_cfg = config.metrics or {}
    dashboard_file = metrics_cfg.get("dashboard_file", DEFAULT_DASHBOARD_FILE)
    db_file = metrics_cfg.get("db_file", DEFAULT_DB_FILE)

    en_dir = site_dir / "_posts"
    fa_dir = site_dir / "_fa_posts"

    ledger_file = (config.mailer or {}).get(
        "sent_state_file", "data/sent_broadcasts.json"
    )
    ledger = (
        json.loads(Path(ledger_file).read_text(encoding="utf-8"))
        if Path(ledger_file).exists()
        else {}
    )
    streak_file = (config.empty_brief or {}).get(
        "state_file", "data/empty_streak.json"
    )
    streak = (
        json.loads(Path(streak_file).read_text(encoding="utf-8"))
        if Path(streak_file).exists()
        else {}
    )
    empty_by_date = {
        h.get("date"): h.get("empty") for h in streak.get("history", [])
    }

    records = load_run_records(db_file)
    payload = build_dashboard(
        records, en_dir, fa_dir, ledger, empty_by_date, joins=None, today_date=None
    )

    # Preserve the live total-ever subscriber count (no network here).
    out = Path(dashboard_file)
    prior_total_ever = None
    if out.exists():
        try:
            prior_total_ever = (
                json.loads(out.read_text(encoding="utf-8"))
                .get("totals", {})
                .get("subscribers_total_ever")
            )
        except (OSError, json.JSONDecodeError):
            prior_total_ever = None
    if prior_total_ever is not None:
        payload["totals"]["subscribers_total_ever"] = prior_total_ever

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out}: {len(payload['days'])} day(s), {len(records)} run record(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
