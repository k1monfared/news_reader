"""Seed ``docs/_data/dashboard.json`` from committed history.

Scans ``docs/_posts`` and ``docs/_fa_posts``, the broadcast ledger, and the
empty-streak history, and writes a dashboard data file covering every date
that has at least one post. No network calls: historical subscriber counts and
processed-link counts are left null.

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
from stages.metrics import DEFAULT_DASHBOARD_FILE, build_day_record, recompute_totals


def _dates(posts_dir: Path) -> set[str]:
    if not posts_dir.is_dir():
        return set()
    return {p.name[:10] for p in posts_dir.glob("*-daily-brief.md")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    config = load_config(args.config)
    site_dir = Path(config.publish.get("site_dir", "docs"))
    metrics_cfg = config.metrics or {}
    dashboard_file = metrics_cfg.get("dashboard_file", DEFAULT_DASHBOARD_FILE)

    en_dir = site_dir / "_posts"
    fa_dir = site_dir / "_fa_posts"
    dates = sorted(_dates(en_dir) | _dates(fa_dir))

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

    days = []
    for date_str in dates:
        en_path = en_dir / f"{date_str}-daily-brief.md"
        fa_path = fa_dir / f"{date_str}-daily-brief.md"
        days.append(
            build_day_record(
                date_str,
                run_meta=None,
                en_post=en_path.read_text(encoding="utf-8") if en_path.exists() else None,
                fa_post=fa_path.read_text(encoding="utf-8") if fa_path.exists() else None,
                ledger=ledger,
                subscribers=None,
                empty=empty_by_date.get(date_str),
            )
        )

    payload_days = days
    out = Path(dashboard_file)
    # Preserve the live total-ever subscriber count (this script has no network
    # and cannot recompute it); it is refreshed by the metrics stage each run.
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

    totals = recompute_totals(payload_days, None)
    if prior_total_ever is not None:
        totals["subscribers_total_ever"] = prior_total_ever

    payload = {"totals": totals, "days": payload_days}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out} with {len(days)} day(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
