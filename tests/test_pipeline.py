"""Tests for the pipeline orchestrator (run_pipeline.py)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_pipeline import create_run_dir, find_missing_dates


# ---------------------------------------------------------------------------
# create_run_dir
# ---------------------------------------------------------------------------


class TestCreateRunDir:
    def test_create_run_dir(self, tmp_path):
        """create_run_dir should build the expected directory structure."""
        base_dir = str(tmp_path / "data")
        run_id = "2026-03-29-080000"

        run_dir = create_run_dir(base_dir, run_id)

        assert run_dir.exists()
        assert run_dir == tmp_path / "data" / "runs" / run_id
        assert (run_dir / "audit").is_dir()
        assert (run_dir / "audit" / "llm_inputs").is_dir()
        assert (run_dir / "audit" / "llm_outputs").is_dir()

    def test_create_run_dir_idempotent(self, tmp_path):
        """Calling create_run_dir twice should not raise or lose data."""
        base_dir = str(tmp_path / "data")
        run_id = "2026-03-29-080000"

        run_dir = create_run_dir(base_dir, run_id)
        # Write a file into the run dir
        marker = run_dir / "marker.txt"
        marker.write_text("keep me")

        # Call again
        run_dir2 = create_run_dir(base_dir, run_id)
        assert run_dir2 == run_dir
        assert marker.read_text() == "keep me"


# ---------------------------------------------------------------------------
# find_missing_dates
# ---------------------------------------------------------------------------


class TestFindMissingDates:
    def _create_successful_run(self, runs_dir: Path, date: str) -> None:
        """Create a minimal successful run directory with valid meta."""
        run_id = f"{date}-080000"
        run_path = runs_dir / run_id
        run_path.mkdir(parents=True, exist_ok=True)

        meta = {
            "run_id": run_id,
            "started_at": f"{date}T08:00:00-07:00",
            "finished_at": f"{date}T08:15:00-07:00",
            "errors": [],
        }
        (run_path / "run_meta.json").write_text(json.dumps(meta))

    def _create_failed_run(self, runs_dir: Path, date: str) -> None:
        """Create a run directory that represents a failed run."""
        run_id = f"{date}-080000"
        run_path = runs_dir / run_id
        run_path.mkdir(parents=True, exist_ok=True)

        meta = {
            "run_id": run_id,
            "started_at": f"{date}T08:00:00-07:00",
            "finished_at": f"{date}T08:15:00-07:00",
            "errors": ["fetch: All sources failed"],
        }
        (run_path / "run_meta.json").write_text(json.dumps(meta))

    def test_find_missing_dates_returns_gaps(self, tmp_path):
        """Dates between the last successful run and today should be detected."""
        base_dir = str(tmp_path / "data")
        runs_dir = tmp_path / "data" / "runs"
        runs_dir.mkdir(parents=True)

        # Use a fixed "today" by creating a timezone with known offset
        tz = timezone(timedelta(hours=-7))

        # Simulate: successful runs on March 25 and 27, nothing on 26 or 28
        self._create_successful_run(runs_dir, "2026-03-25")
        self._create_successful_run(runs_dir, "2026-03-27")

        # Mock "today" as March 29 by using the real function
        # find_missing_dates looks from last successful date up to today
        missing = find_missing_dates(base_dir, tz)

        # March 28 should be missing (between last run 2026-03-27 and today)
        # The function looks from (last_date + 1) to (today - 1)
        assert "2026-03-28" in missing
        # March 26 should NOT be missing (it is before the last successful run)
        assert "2026-03-26" not in missing

    def test_find_missing_dates_no_runs(self, tmp_path):
        """If no runs directory exists, return an empty list."""
        base_dir = str(tmp_path / "data")
        tz = timezone(timedelta(hours=-7))

        missing = find_missing_dates(base_dir, tz)
        assert missing == []

    def test_find_missing_dates_no_successful_runs(self, tmp_path):
        """If all runs failed, return an empty list."""
        base_dir = str(tmp_path / "data")
        runs_dir = tmp_path / "data" / "runs"
        runs_dir.mkdir(parents=True)
        tz = timezone(timedelta(hours=-7))

        self._create_failed_run(runs_dir, "2026-03-25")
        self._create_failed_run(runs_dir, "2026-03-27")

        missing = find_missing_dates(base_dir, tz)
        assert missing == []

    def test_find_missing_dates_all_present(self, tmp_path):
        """If every day has a run, the missing list should be empty."""
        base_dir = str(tmp_path / "data")
        runs_dir = tmp_path / "data" / "runs"
        runs_dir.mkdir(parents=True)
        tz = timezone(timedelta(hours=-7))

        today = datetime.now(tz).strftime("%Y-%m-%d")
        yesterday = (datetime.now(tz) - timedelta(days=1)).strftime("%Y-%m-%d")

        # Create runs for yesterday and today
        self._create_successful_run(runs_dir, yesterday)
        self._create_successful_run(runs_dir, today)

        missing = find_missing_dates(base_dir, tz)
        assert missing == []

    def test_find_missing_dates_ignores_corrupt_meta(self, tmp_path):
        """Runs with corrupt or missing meta should be treated as non-existent."""
        base_dir = str(tmp_path / "data")
        runs_dir = tmp_path / "data" / "runs"
        runs_dir.mkdir(parents=True)
        tz = timezone(timedelta(hours=-7))

        # One valid run
        self._create_successful_run(runs_dir, "2026-03-25")

        # One run with corrupt meta
        corrupt_dir = runs_dir / "2026-03-26-080000"
        corrupt_dir.mkdir(parents=True)
        (corrupt_dir / "run_meta.json").write_text("not valid json {{{")

        # Another valid run two days later
        self._create_successful_run(runs_dir, "2026-03-27")

        missing = find_missing_dates(base_dir, tz)

        # March 28 should be missing (between 2026-03-27 and today)
        assert "2026-03-28" in missing
