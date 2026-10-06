"""Tests for the failures view builder (scripts/build_failures.py)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.build_failures import (
    build_payload,
    run_date,
    scan_backfilled,
)


class TestRunDate:
    def test_utc_to_schedule_tz(self):
        # 16:43 UTC -> 09:43 America/Vancouver (UTC-7) same day
        assert run_date("2026-10-04T16:43:36Z", -7) == "2026-10-04"

    def test_late_utc_crosses_day(self):
        # 02:00 UTC -> 19:00 previous day in UTC-7
        assert run_date("2026-10-05T02:00:00Z", -7) == "2026-10-04"

    def test_bad_input(self):
        assert run_date("", -7) is None
        assert run_date("not-a-date", -7) is None


class TestScanBackfilled:
    def test_detects_flag(self, tmp_path):
        d = tmp_path / "_posts"
        d.mkdir()
        (d / "2026-09-14-daily-brief.md").write_text(
            "---\nlayout: post\nbackfilled: true\n---\n\nbody\n"
        )
        (d / "2026-09-15-daily-brief.md").write_text(
            "---\nlayout: post\n---\n\nbody\n"
        )
        out = scan_backfilled(d)
        assert "2026-09-14" in out
        assert "2026-09-15" not in out

    def test_missing_dir(self, tmp_path):
        assert scan_backfilled(tmp_path / "nope") == {}


class TestBuildPayload:
    def _commit(self, path):
        return {"hash": "abc12345", "short": "abc12345", "message": "Daily brief", "date": "", "url": "u"}

    def test_counts_and_linking(self):
        failed_runs = {
            "2026-10-04": {"url": "https://x/runs/1", "databaseId": 1, "createdAt": "2026-10-04T16:00:00Z"},
            "2026-10-03": {"url": "https://x/runs/2", "databaseId": 2, "createdAt": "2026-10-03T16:00:00Z"},
        }
        records = {
            "2026-10-04": {"status": "failed", "failed_stages": ["summarize"], "errors": ["boom"]},
        }
        backfilled = {"2026-10-04": ["docs/_posts/2026-10-04-daily-brief.md"]}
        payload = build_payload(
            failed_runs, records, backfilled,
            commit_lookup=self._commit,
            fix_lookup=lambda d: [{"hash": "fix", "short": "fix", "message": "fix " + d, "date": "", "url": "u"}],
        )
        assert payload["counts"]["failed"] == 2
        assert payload["counts"]["backfilled"] == 1
        assert payload["counts"]["failed_and_backfilled"] == 1
        assert payload["counts"]["failed_not_backfilled"] == ["2026-10-03"]

        day4 = next(d for d in payload["failed_dates"] if d["date"] == "2026-10-04")
        assert day4["backfilled"] is True
        assert day4["backfill_commit"]["short"] == "abc12345"
        assert day4["fix_commits"][0]["message"] == "fix 2026-10-04"
        assert day4["run_url"] == "https://x/runs/1"

        day3 = next(d for d in payload["failed_dates"] if d["date"] == "2026-10-03")
        assert day3["backfilled"] is False
        assert day3["backfill_commit"] is None
        assert day3["fix_commits"] == []

    def test_record_failed_without_github_run(self):
        payload = build_payload(
            {}, {"2026-10-05": {"status": "failed", "failed_stages": ["publish"]}}, {},
            commit_lookup=self._commit, fix_lookup=lambda d: [],
        )
        assert payload["counts"]["failed"] == 1
        assert payload["failed_dates"][0]["run_url"] is None
