"""Pipeline orchestrator. Creates run directories, calls stages in sequence, handles backfill."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from models import load_config, RunMeta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pipeline")

# Stages whose failure means the run did not deliver what it promised.
# English set: without these there is no usable brief. Farsi set: the Farsi
# edition is missing while English still ships. Non-critical stages
# (dedup, categorize, track_developments, editorial, verify, mailer) each
# degrade gracefully and must not fail the CI run by themselves.
CRITICAL_STAGES_EN = {"fetch", "filter", "summarize", "publish"}
CRITICAL_STAGES_FA = {"translate_fa"}
CRITICAL_STAGES = CRITICAL_STAGES_EN | CRITICAL_STAGES_FA


def get_timezone_offset(tz_name: str) -> timezone:
    """Get timezone offset from name. Supports America/Vancouver = UTC-7 (PDT)."""
    offsets = {
        "America/Vancouver": timezone(timedelta(hours=-7)),
        "America/Los_Angeles": timezone(timedelta(hours=-7)),
        "UTC": timezone.utc,
    }
    return offsets.get(tz_name, timezone(timedelta(hours=-7)))


def create_run_dir(base_dir: str, run_id: str) -> Path:
    """Create the run directory structure."""
    run_dir = Path(base_dir) / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "audit").mkdir(exist_ok=True)
    (run_dir / "audit" / "llm_inputs").mkdir(exist_ok=True)
    (run_dir / "audit" / "llm_outputs").mkdir(exist_ok=True)
    return run_dir


def update_latest_symlink(base_dir: str, run_id: str) -> None:
    """Point data/latest symlink to the most recent successful run."""
    latest = Path(base_dir) / "latest"
    target = Path("runs") / run_id
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(target)


def find_missing_dates(base_dir: str, tz: timezone) -> list[str]:
    """Find dates with no successful run since the last one."""
    runs_dir = Path(base_dir) / "runs"
    if not runs_dir.exists():
        return []

    existing_dates = set()
    for d in runs_dir.iterdir():
        if d.is_dir() and not d.is_symlink():
            date_part = d.name[:10]
            meta_path = d / "run_meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text())
                    if meta.get("finished_at") and not meta.get("errors"):
                        existing_dates.add(date_part)
                except (json.JSONDecodeError, KeyError):
                    pass

    if not existing_dates:
        return []

    last_date = max(existing_dates)
    today = datetime.now(tz).strftime("%Y-%m-%d")

    missing = []
    current = datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)
    end = datetime.strptime(today, "%Y-%m-%d")

    while current < end:
        date_str = current.strftime("%Y-%m-%d")
        if date_str not in existing_dates:
            missing.append(date_str)
        current += timedelta(days=1)

    return missing


def run_pipeline(target_date: str | None = None, backfill: bool = False) -> str:
    """Run the full pipeline for a given date.

    Args:
        target_date: Date to run for (YYYY-MM-DD). Defaults to today.
        backfill: If True, this is a backfill run (sparser data expected).

    Returns:
        run_id of the completed run
    """
    config = load_config()
    tz = get_timezone_offset(config.schedule.get("timezone", "America/Vancouver"))
    now = datetime.now(tz)
    data_dir = config.paths.get("data_dir", "data")

    if target_date is None:
        target_date = now.strftime("%Y-%m-%d")

    run_id = f"{target_date}-{now.strftime('%H%M%S')}"
    run_dir = create_run_dir(data_dir, run_id)

    logger.info(f"Starting pipeline run: {run_id} (backfill={backfill})")

    meta = RunMeta(
        run_id=run_id,
        started_at=now.isoformat(),
    )

    # Import stages lazily to avoid circular imports
    from llm_client import AuditedLLMClient
    from audit_logger import AuditedHTTPClient

    llm_client = AuditedLLMClient(str(run_dir), config.budget, config.models)
    http_client = AuditedHTTPClient(str(run_dir))

    stage_order = [
        "fetch",
        "translate",
        "dedup",
        "filter",
        "categorize",
        "track_developments",
        "summarize",
        "editorial",
        "verify",
        "publish",
        "translate_fa",
        "mailer",
    ]

    try:
        for stage_name in stage_order:
            stage_start = time.time()
            logger.info(f"Running stage: {stage_name}")

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
                logger.error(f"Stage {stage_name} failed: {e}", exc_info=True)
                meta.errors.append(f"{stage_name}: {e}")
                meta.stages[stage_name] = {
                    "status": "failed",
                    "error": str(e),
                    "duration_s": round(time.time() - stage_start, 2),
                }

                # Fetch failure with no sources = abort
                if stage_name == "fetch":
                    raw_path = run_dir / "raw_items.json"
                    if not raw_path.exists() or json.loads(raw_path.read_text()) == []:
                        logger.error("All sources failed. Aborting run.")
                        break
                continue

            stage_duration = round(time.time() - stage_start, 2)
            meta.stages[stage_name] = {
                "status": "completed",
                "duration_s": stage_duration,
                **(stage_result if isinstance(stage_result, dict) else {}),
            }
            logger.info(f"Stage {stage_name} completed in {stage_duration}s")

    finally:
        http_client.close()

    # Finalize run metadata
    meta.finished_at = datetime.now(tz).isoformat()
    meta.total_cost_usd = llm_client.total_cost
    meta.total_duration_s = round(time.time() - time.mktime(
        datetime.fromisoformat(meta.started_at).timetuple()
    ), 2)
    meta.prompt_versions = llm_client.prompt_versions_used

    meta_path = run_dir / "run_meta.json"
    meta_path.write_text(json.dumps(meta.model_dump(), indent=2))

    # Update latest symlink only if no critical errors
    failed_critical = [
        s for s in CRITICAL_STAGES
        if meta.stages.get(s, {}).get("status") == "failed"
    ]
    failed_en = [s for s in failed_critical if s in CRITICAL_STAGES_EN]
    failed_fa = [s for s in failed_critical if s in CRITICAL_STAGES_FA]
    if not failed_critical:
        update_latest_symlink(data_dir, run_id)
        logger.info(f"Run {run_id} completed successfully. Latest symlink updated.")
    else:
        if failed_en:
            logger.error(f"Run {run_id} failed English critical stages: {failed_en}.")
        if failed_fa:
            logger.error(f"Run {run_id} failed Farsi critical stages: {failed_fa}.")

    return run_id


def critical_failures(data_dir: str, run_id: str) -> list[str]:
    """Return the critical stages recorded as failed in a run's meta.

    Reads ``run_meta.json`` so callers (e.g. ``main``) can decide the
    process exit code after ``run_pipeline`` has finished.
    """
    meta_path = Path(data_dir) / "runs" / run_id / "run_meta.json"
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError):
        return sorted(CRITICAL_STAGES)
    stages = meta.get("stages", {})
    return sorted(
        s for s in CRITICAL_STAGES if stages.get(s, {}).get("status") == "failed"
    )


def print_summary(data_dir: str, run_id: str) -> None:
    """Print the headlines summary to the terminal."""
    headlines_path = Path(data_dir) / "runs" / run_id / "headlines.txt"
    if headlines_path.exists():
        print("\n" + headlines_path.read_text())
    else:
        logger.warning("No headlines.txt found for this run.")


def main() -> None:
    """Entry point. Handles backfill logic and runs the pipeline.

    Exits non-zero when any critical stage failed in today's run or in a
    backfill run, so scheduled CI runs surface failures instead of hiding
    them behind a green check.
    """
    config = load_config()
    tz = get_timezone_offset(config.schedule.get("timezone", "America/Vancouver"))
    data_dir = config.paths.get("data_dir", "data")
    all_failures: list[str] = []

    # Check for missed dates and backfill
    missing_dates = find_missing_dates(data_dir, tz)
    if missing_dates:
        logger.info(f"Backfilling {len(missing_dates)} missed date(s): {missing_dates}")
        for date in missing_dates:
            try:
                run_id = run_pipeline(target_date=date, backfill=True)
                failed = critical_failures(data_dir, run_id)
                if failed:
                    logger.error(f"Backfill {date} had critical stage failures: {failed}")
                    all_failures.extend(f"{date}:{s}" for s in failed)
            except Exception as e:
                logger.error(f"Backfill for {date} failed: {e}", exc_info=True)
                all_failures.append(f"{date}:exception")

    # Run today's pipeline
    run_id = run_pipeline()
    print_summary(data_dir, run_id)

    failed = critical_failures(data_dir, run_id)
    if failed:
        all_failures.extend(f"{run_id}:{s}" for s in failed)

    if all_failures:
        logger.error(f"Pipeline finished with critical failures: {all_failures}")
        sys.exit(1)


if __name__ == "__main__":
    main()
