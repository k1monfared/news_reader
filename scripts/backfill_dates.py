"""Date-scoped backfill driver.

Runs the full pipeline for specific past dates, keeping only items that
were actually published on each target date. Live feeds are a snapshot of
now, so this produces honest briefs only for dates whose items still
survive in the feeds; dates with no surviving coverage abort before any
LLM call instead of fabricating content under an old date.

Usage:
    python scripts/backfill_dates.py --dates 2026-08-16 2026-08-17

Each date runs through the normal stage chain (publish and translate_fa
push independently; mailer skips non-today dates by design). Exits
non-zero if any run had critical-stage failures.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import load_config
from run_pipeline import critical_failures, print_summary, run_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backfill")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dates", nargs="+", required=True,
        help="Dates to backfill (YYYY-MM-DD)",
    )
    args = parser.parse_args()

    config = load_config()
    data_dir = config.paths.get("data_dir", "data")
    all_failures = []

    for date_str in sorted(args.dates):
        logger.info(f"=== Backfilling {date_str} ===")
        try:
            run_id = run_pipeline(
                target_date=date_str,
                backfill=True,
                item_date_filter=date_str,
            )
            print_summary(data_dir, run_id)
            failed = critical_failures(data_dir, run_id)
            if failed:
                logger.error(f"Backfill {date_str} critical failures: {failed}")
                all_failures.extend(f"{date_str}:{s}" for s in failed)
        except Exception as e:
            logger.error(f"Backfill {date_str} failed: {e}", exc_info=True)
            all_failures.append(f"{date_str}:exception")

    if all_failures:
        logger.error(f"Backfills finished with failures: {all_failures}")
        sys.exit(1)


if __name__ == "__main__":
    main()
