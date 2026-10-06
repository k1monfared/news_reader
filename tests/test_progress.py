"""Tests for the live run-progress status file."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from progress import Progress


def _read(path: Path) -> dict:
    return json.loads(path.read_text())


class TestProgress:
    def test_lifecycle(self, tmp_path):
        path = tmp_path / "run_status.json"
        progress = Progress(
            "2026-10-06-090000",
            "2026-10-06",
            ["fetch", "filter", "summarize"],
            status_file=str(path),
            commit=False,
        )

        progress.start()
        data = _read(path)
        assert data["status"] == "running"
        assert data["current_stage"] == "fetch"

        progress.stage_done("fetch", "completed", 12.0)
        data = _read(path)
        assert data["current_stage"] == "filter"
        assert data["stages"][0]["status"] == "completed"
        assert data["stages"][0]["duration_s"] == 12.0

        progress.stage_done("filter", "failed", 3.0, "boom")
        progress.finish("failed")
        data = _read(path)
        assert data["status"] == "failed"
        assert data["current_stage"] is None
        assert data["stages"][1]["status"] == "failed"
        assert data["stages"][1]["error"] == "boom"
        assert data["total_stages"] == 3

    def test_disabled_writes_nothing(self, tmp_path):
        path = tmp_path / "run_status.json"
        progress = Progress(
            "r", "2026-10-06", ["fetch"], status_file=str(path),
            commit=False, enabled=False,
        )
        progress.start()
        progress.stage_done("fetch")
        progress.finish()
        assert not path.exists()

    def test_unknown_stage_is_recorded(self, tmp_path):
        path = tmp_path / "run_status.json"
        progress = Progress(
            "r", "2026-10-06", ["fetch"], status_file=str(path), commit=False
        )
        progress.start()
        progress.stage_done("mystery", "completed", 1.0)
        data = _read(path)
        assert any(s["name"] == "mystery" for s in data["stages"])
        assert data["current_stage"] is None
