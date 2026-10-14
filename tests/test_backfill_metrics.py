"""Tests for the dashboard history rebuild (scripts/backfill_metrics.py).

The rebuild must thread the failures.json counts into build_dashboard. Without
them a rebuild nulls ``days_failed_ever`` / ``days_repaired`` and drops the
extra-runs term from ``cost_projected``, clobbering the live dashboard fields.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.backfill_metrics as backfill_metrics


class _FakeConfig:
    def __init__(self, site_dir, ledger_file, streak_file, dashboard_file, db_file):
        self.publish = {"site_dir": str(site_dir)}
        self.metrics = {
            "dashboard_file": str(dashboard_file),
            "db_file": str(db_file),
        }
        self.mailer = {"sent_state_file": str(ledger_file)}
        self.empty_brief = {"state_file": str(streak_file)}


def _setup(tmp_path, counts=None):
    site = tmp_path / "site"
    (site / "_posts").mkdir(parents=True)
    (site / "_fa_posts").mkdir(parents=True)
    (site / "_data").mkdir(parents=True)
    if counts is not None:
        (site / "_data" / "failures.json").write_text(
            json.dumps({"counts": counts}), encoding="utf-8"
        )
    db = tmp_path / "run_metrics.jsonl"
    db.write_text("", encoding="utf-8")
    config = _FakeConfig(
        site, tmp_path / "ledger.json", tmp_path / "streak.json",
        tmp_path / "dashboard.json", db,
    )
    return site, config


def _run_capture(tmp_path, monkeypatch, counts):
    site, config = _setup(tmp_path, counts)
    monkeypatch.setattr(backfill_metrics, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(sys, "argv", ["backfill_metrics.py"])
    captured = {}

    def fake_build_dashboard(*args, **kwargs):
        captured.update(kwargs)
        return {"generated_at": "x", "today": None, "days": [], "totals": {}}

    monkeypatch.setattr(backfill_metrics, "build_dashboard", fake_build_dashboard)
    assert backfill_metrics.main() == 0
    return captured


class TestBackfillMetricsFailures:
    def test_threads_counts_and_extra_runs(self, tmp_path, monkeypatch):
        captured = _run_capture(tmp_path, monkeypatch, {
            "failed": 19,
            "backfilled": 16,
            "failed_and_backfilled": 11,
            "failed_not_backfilled": ["2026-04-28", "2026-08-24"],
        })
        assert captured["extra_runs"] == 18  # 16 backfilled + 2 not-backfilled
        assert captured["failures"]["failed"] == 19
        assert captured["failures"]["failed_and_backfilled"] == 11

    def test_missing_failures_file_is_safe(self, tmp_path, monkeypatch):
        captured = _run_capture(tmp_path, monkeypatch, None)
        assert captured["extra_runs"] == 0
        assert captured["failures"] == {}

    def test_malformed_failures_file_is_safe(self, tmp_path, monkeypatch):
        site, config = _setup(tmp_path, None)
        (site / "_data" / "failures.json").write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(backfill_metrics, "load_config", lambda *a, **k: config)
        monkeypatch.setattr(sys, "argv", ["backfill_metrics.py"])
        captured = {}

        def fake_build_dashboard(*args, **kwargs):
            captured.update(kwargs)
            return {"generated_at": "x", "today": None, "days": [], "totals": {}}

        monkeypatch.setattr(backfill_metrics, "build_dashboard", fake_build_dashboard)
        assert backfill_metrics.main() == 0
        assert captured["extra_runs"] == 0
        assert captured["failures"] == {}

    def test_rebuild_keeps_reliability_and_projection(self, tmp_path, monkeypatch):
        site, config = _setup(tmp_path, {
            "failed": 19,
            "backfilled": 16,
            "failed_and_backfilled": 11,
            "failed_not_backfilled": ["2026-04-28", "2026-08-24"],
        })
        (tmp_path / "run_metrics.jsonl").write_text(
            json.dumps({
                "run_id": "2026-10-01-090000",
                "target_date": "2026-10-01",
                "status": "success",
                "cost_usd": 0.10,
                "posted_en": 1,
                "posted_fa": 0,
                "links_en": 1,
                "emails": {},
                "tokens": {
                    "total": {"input": 100, "output": 10, "thinking": 0, "calls": 1}
                },
            }) + "\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(backfill_metrics, "load_config", lambda *a, **k: config)
        monkeypatch.setattr(sys, "argv", ["backfill_metrics.py"])

        assert backfill_metrics.main() == 0

        totals = json.loads(
            (tmp_path / "dashboard.json").read_text(encoding="utf-8")
        )["totals"]
        assert totals["days_failed_ever"] == 19
        assert totals["days_repaired"] == 11
        assert totals["cost_extra_runs"] == 18
        assert totals["cost_projected"] == pytest.approx(0.10 * (1 + 18))
