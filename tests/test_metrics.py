"""Tests for the dashboard metrics stage and backfill inputs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import stages.metrics as metrics_module
from stages.metrics import (
    count_entries,
    count_links,
    day_status,
    recompute_totals,
    run_metrics,
)

POST = """\
**Key development:** Something happened.

<details markdown="block">
<summary markdown="span">**One.** Why. *(BBC)*</summary>

Sources: [BBC](https://b.co) \\| [NPR](https://n.co)

</details>

<details markdown="block">
<summary markdown="span">**Two.** Why. *(AJ)*</summary>

Sources: [AJ](https://a.co)

</details>
"""


class TestCounting:
    def test_count_entries(self):
        assert count_entries(POST) == 2
        assert count_entries("No significant developments reported today.") == 0

    def test_count_links_unique(self):
        # b.co, n.co, a.co -> 3 unique
        assert count_links(POST) == 3


class TestDayStatus:
    def test_success(self):
        meta = {"stages": {"summarize": {"status": "completed"}}}
        assert day_status(meta) == ("success", [], [])

    def test_failed_critical(self):
        meta = {"stages": {"publish": {"status": "failed"}}}
        status, failed, degraded = day_status(meta)
        assert status == "failed" and failed == ["publish"]

    def test_degraded_non_critical(self):
        meta = {"stages": {"editorial": {"status": "failed"}}}
        status, failed, degraded = day_status(meta)
        assert status == "degraded" and failed == ["editorial"]

    def test_degraded_flag(self):
        meta = {"stages": {"editorial": {"status": "completed", "degraded": True}}}
        status, _, degraded = day_status(meta)
        assert status == "degraded" and degraded == ["editorial"]


class TestTotals:
    def _day(self, date, posted=1, links=1, en=True, fa=True, subs=None, recipients=None, status="success"):
        emails = {
            "en": {"sent": True, "recipients": recipients},
            "fa": {"sent": True, "recipients": None},
        }
        return {
            "date": date, "posted": posted, "posted_en": posted, "posted_fa": 0,
            "links": links, "processed": None, "included": None,
            "en": en, "fa": fa, "emails": emails, "subscribers": subs,
            "status": status, "empty": False,
        }

    def test_sums_and_nulls(self):
        days = [
            self._day("2026-10-01", posted=2, links=4, subs=10),
            self._day("2026-10-02", posted=3, links=5, subs=12, recipients=10),
        ]
        totals = recompute_totals(days, joins={"distinct_emails": 99})
        assert totals["days_running"] == 2
        assert totals["posted"] == 5
        assert totals["links"] == 9
        assert totals["processed"] is None  # no known values
        assert totals["subscribers_current"] == 12
        assert totals["subscribers_peak"] == 12
        assert totals["subscribers_total_ever"] == 99
        assert totals["deliveries"] == 10
        assert totals["emails_total"] == 4

    def test_days_running_spans_gaps(self):
        days = [self._day("2026-09-01"), self._day("2026-09-10")]
        assert recompute_totals(days, None)["days_running"] == 10


class TestRunMetrics:
    def _setup(self, tmp_path, sample_config):
        date = "2026-10-06"
        run_dir = tmp_path / f"{date}-090000"
        (run_dir / "audit").mkdir(parents=True)
        (run_dir / "run_meta.json").write_text(json.dumps({
            "run_id": f"{date}-090000",
            "items_fetched": 292,
            "items_included": 33,
            "total_duration_s": 120.0,
            "models_used": ["longcat-2.5-preview-free"],
            "stages": {"summarize": {"status": "completed"}},
        }))
        site = tmp_path / "site"
        (site / "_posts").mkdir(parents=True)
        (site / "_fa_posts").mkdir(parents=True)
        (site / "_posts" / f"{date}-daily-brief.md").write_text(POST)
        (site / "_fa_posts" / f"{date}-daily-brief.md").write_text(POST)

        ledger = tmp_path / "ledger.json"
        ledger.write_text(json.dumps({
            date: {"en": {"broadcast_id": "x", "recipients": 42}}
        }))
        streak = tmp_path / "streak.json"
        streak.write_text(json.dumps({"history": [{"date": date, "empty": False}]}))

        config = sample_config.model_copy(deep=True)
        config.publish["site_dir"] = str(site)
        config.mailer["sent_state_file"] = str(ledger)
        config.empty_brief["state_file"] = str(streak)
        config.metrics["dashboard_file"] = str(tmp_path / "dashboard.json")
        return date, run_dir, config

    def test_writes_dashboard(self, tmp_path, monkeypatch, sample_config):
        date, run_dir, config = self._setup(tmp_path, sample_config)
        monkeypatch.setattr(metrics_module, "_git_commit_file", lambda *a, **k: None)
        monkeypatch.setattr(metrics_module, "audience_count", lambda *a, **k: 42)
        monkeypatch.setattr(metrics_module, "subscribe_joins", lambda *a, **k: {"distinct_emails": 500})

        result = run_metrics(str(run_dir), config, None, None)

        assert result["status"] == "completed"
        data = json.loads((tmp_path / "dashboard.json").read_text())
        assert len(data["days"]) == 1
        day = data["days"][0]
        assert day["date"] == date
        assert day["posted"] == 4  # two entries per language, two languages
        assert day["processed"] == 292
        assert day["emails"]["en"]["recipients"] == 42
        assert data["totals"]["subscribers_current"] == 84  # 42 + 42
        assert data["totals"]["subscribers_total_ever"] == 500

    def test_rerun_is_idempotent(self, tmp_path, monkeypatch, sample_config):
        _, run_dir, config = self._setup(tmp_path, sample_config)
        monkeypatch.setattr(metrics_module, "_git_commit_file", lambda *a, **k: None)
        monkeypatch.setattr(metrics_module, "audience_count", lambda *a, **k: None)
        monkeypatch.setattr(metrics_module, "subscribe_joins", lambda *a, **k: None)

        run_metrics(str(run_dir), config, None, None)
        run_metrics(str(run_dir), config, None, None)

        data = json.loads((tmp_path / "dashboard.json").read_text())
        assert len(data["days"]) == 1

    def test_disabled_skips(self, tmp_path, sample_config):
        _, run_dir, config = self._setup(tmp_path, sample_config)
        config.metrics["enabled"] = False
        assert run_metrics(str(run_dir), config, None, None) == {"status": "skipped"}
