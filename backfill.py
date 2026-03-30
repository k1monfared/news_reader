"""Historical backfill via GDELT and France24 archive.

Processes dates sequentially from start date through today, using GDELT
to fetch historical articles for each configured source.

Usage:
    python backfill.py                          # full range: 2026-02-26 to today
    python backfill.py --start 2026-03-01       # from March 1 to today
    python backfill.py --date 2026-03-27        # single date
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from models import load_config, RawItem, RunMeta
from llm_client import AuditedLLMClient
from audit_logger import AuditedHTTPClient
from run_pipeline import create_run_dir, update_latest_symlink, get_timezone_offset
from stages.fetchers.gdelt import GDELTFetcher
from stages.fetchers.archive_france24 import France24ArchiveFetcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backfill")

CONFLICT_START = "2026-02-26"

PIPELINE_STAGES = [
    "translate",
    "dedup",
    "filter",
    "categorize",
    "track_developments",
    "summarize",
    "editorial",
    "verify",
    "publish",
]


def has_successful_run(data_dir: str, date_str: str) -> bool:
    """Check if a successful run already exists for the given date."""
    runs_dir = Path(data_dir) / "runs"
    if not runs_dir.exists():
        return False

    for d in runs_dir.iterdir():
        if not d.is_dir() or d.is_symlink():
            continue
        if not d.name.startswith(date_str):
            continue
        meta_path = d / "run_meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                if meta.get("finished_at") and not meta.get("errors"):
                    return True
            except (json.JSONDecodeError, KeyError):
                pass

    return False


def fetch_for_date(config, http_client, target_date: str) -> list[RawItem]:
    """Fetch articles for all sources on a given date via GDELT + France24 archive."""
    all_items: list[RawItem] = []
    seen_urls: set[str] = set()

    for source in config.sources:
        # GDELT fetch for every source
        try:
            gdelt = GDELTFetcher(source, http_client, target_date)
            items = gdelt.fetch()
            for item in items:
                if item.source_url not in seen_urls:
                    seen_urls.add(item.source_url)
                    all_items.append(item)
        except Exception as e:
            logger.error(f"GDELT fetch failed for {source.name}: {e}")

        # France24 archive scraper (in addition to GDELT)
        if source.name == "france24":
            try:
                archive = France24ArchiveFetcher(source, http_client, target_date)
                items = archive.fetch()
                for item in items:
                    if item.source_url not in seen_urls:
                        seen_urls.add(item.source_url)
                        all_items.append(item)
            except Exception as e:
                logger.error(f"France24 archive fetch failed: {e}")

    return all_items


def _write_meta(meta: RunMeta, run_dir: Path, llm_client: AuditedLLMClient) -> None:
    """Write run metadata to disk."""
    meta.finished_at = datetime.now(timezone.utc).isoformat()
    meta.total_cost_usd = llm_client.total_cost
    meta.prompt_versions = llm_client.prompt_versions_used

    try:
        start = datetime.fromisoformat(meta.started_at)
        end = datetime.fromisoformat(meta.finished_at)
        meta.total_duration_s = round((end - start).total_seconds(), 2)
    except Exception:
        pass

    meta_path = run_dir / "run_meta.json"
    meta_path.write_text(json.dumps(meta.model_dump(), indent=2))


def run_backfill_date(target_date: str, config, data_dir: str) -> str | None:
    """Run the backfill pipeline for a single date.

    Returns run_id on success, None on skip/failure.
    """
    if has_successful_run(data_dir, target_date):
        logger.info(f"Skipping {target_date}: successful run already exists")
        return None

    now = datetime.now(timezone.utc)
    run_id = f"{target_date}-{now.strftime('%H%M%S')}"
    run_dir = create_run_dir(data_dir, run_id)

    logger.info(f"Backfill run: {run_id}")

    meta = RunMeta(
        run_id=run_id,
        started_at=now.isoformat(),
    )

    llm_client = AuditedLLMClient(str(run_dir), config.budget)
    http_client = AuditedHTTPClient(str(run_dir))

    try:
        # Fetch stage: use GDELT + archive instead of RSS
        stage_start = time.time()
        items = fetch_for_date(config, http_client, target_date)
        fetch_duration = round(time.time() - stage_start, 2)

        if not items:
            logger.warning(f"No items found for {target_date}. Skipping.")
            meta.errors.append("fetch: no items found")
            meta.stages["fetch"] = {
                "status": "failed",
                "error": "no items",
                "duration_s": fetch_duration,
            }
            return None

        # Write raw_items.json
        raw_path = run_dir / "raw_items.json"
        raw_path.write_text(json.dumps([item.model_dump() for item in items], indent=2))

        items_by_source: dict[str, int] = {}
        for item in items:
            items_by_source[item.source] = items_by_source.get(item.source, 0) + 1

        meta.stages["fetch"] = {
            "status": "completed",
            "duration_s": fetch_duration,
            "items_by_source": items_by_source,
            "total_items": len(items),
        }
        meta.items_fetched = len(items)
        logger.info(f"Fetched {len(items)} items for {target_date}: {items_by_source}")

        # Run remaining pipeline stages
        for stage_name in PIPELINE_STAGES:
            stage_start = time.time()
            logger.info(f"[{target_date}] Running stage: {stage_name}")

            try:
                stage_module = __import__(f"stages.{stage_name}", fromlist=[stage_name])
                stage_func = getattr(stage_module, f"run_{stage_name}")
                stage_result = stage_func(
                    run_dir=str(run_dir),
                    config=config,
                    llm_client=llm_client,
                    http_client=http_client,
                )
            except Exception as e:
                logger.error(f"[{target_date}] Stage {stage_name} failed: {e}", exc_info=True)
                meta.errors.append(f"{stage_name}: {e}")
                meta.stages[stage_name] = {
                    "status": "failed",
                    "error": str(e),
                    "duration_s": round(time.time() - stage_start, 2),
                }
                continue

            stage_duration = round(time.time() - stage_start, 2)
            meta.stages[stage_name] = {
                "status": "completed",
                "duration_s": stage_duration,
                **(stage_result if isinstance(stage_result, dict) else {}),
            }
            logger.info(f"[{target_date}] Stage {stage_name} completed in {stage_duration}s")

    finally:
        http_client.close()
        _write_meta(meta, run_dir, llm_client)

    # Update latest symlink if no critical failures
    critical_stages = {"fetch", "filter", "summarize"}
    failed_critical = [
        s for s in critical_stages
        if meta.stages.get(s, {}).get("status") == "failed"
    ]
    if not failed_critical:
        update_latest_symlink(data_dir, run_id)
        logger.info(f"Backfill {run_id} completed successfully.")
    else:
        logger.warning(f"Backfill {run_id} had critical failures: {failed_critical}")

    return run_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill historical news via GDELT")
    parser.add_argument(
        "--start", type=str, default=CONFLICT_START,
        help=f"Start date (YYYY-MM-DD, default: {CONFLICT_START})",
    )
    parser.add_argument(
        "--date", type=str, default=None,
        help="Single date to backfill (YYYY-MM-DD)",
    )
    args = parser.parse_args()

    config = load_config()
    tz = get_timezone_offset(config.schedule.get("timezone", "America/Vancouver"))
    data_dir = config.paths.get("data_dir", "data")
    today = datetime.now(tz).strftime("%Y-%m-%d")

    if args.date:
        dates = [args.date]
    else:
        start = datetime.strptime(args.start, "%Y-%m-%d")
        end = datetime.strptime(today, "%Y-%m-%d")
        dates = []
        current = start
        while current <= end:
            dates.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)

    logger.info(f"Backfill: {len(dates)} date(s) from {dates[0]} to {dates[-1]}")

    successes = 0
    failures = 0
    skipped = 0

    for date_str in dates:
        try:
            result = run_backfill_date(date_str, config, data_dir)
            if result is None:
                if has_successful_run(data_dir, date_str):
                    skipped += 1
                else:
                    failures += 1
            else:
                successes += 1
        except Exception as e:
            logger.error(f"Backfill for {date_str} failed: {e}", exc_info=True)
            failures += 1

    logger.info(
        f"Backfill complete: {successes} succeeded, {skipped} skipped, {failures} failed"
    )


if __name__ == "__main__":
    main()
