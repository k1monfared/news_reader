"""Tests for date-scoped backfill tooling.

filter_items_by_date must keep only items published on the target date
(schedule timezone), so backfilled briefs are never built from the wrong
day's news. The editorial stage must accept a custom prompt name so
repairs can use repair_editorial without touching the default path.
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_pipeline import _item_local_date, filter_items_by_date

PDT = timezone(timedelta(hours=-7))


def _item(ts: str, fetch_id: str) -> dict:
    return {
        "source": "france24",
        "source_url": f"https://example.com/{fetch_id}",
        "timestamp": ts,
        "title": "Headline",
        "text": "Body text.",
        "language": "en",
        "fetch_id": fetch_id,
    }


class TestItemLocalDate:
    def test_rfc822_utc_converts_to_vancouver(self):
        # 14:00 UTC on Aug 21 is 07:00 in Vancouver, same local date.
        assert (
            _item_local_date({"timestamp": "Fri, 21 Aug 2026 14:00:00 GMT"}, PDT)
            == "2026-08-21"
        )

    def test_late_utc_evening_falls_on_previous_local_day(self):
        # 06:00 UTC on Aug 21 is 23:00 on Aug 20 in Vancouver.
        assert (
            _item_local_date({"timestamp": "Fri, 21 Aug 2026 06:00:00 GMT"}, PDT)
            == "2026-08-20"
        )

    def test_early_morning_utc_belongs_to_same_local_day(self):
        # 06:00 UTC on Aug 22 is 23:00 on Aug 21 in Vancouver.
        assert (
            _item_local_date({"timestamp": "Sat, 22 Aug 2026 06:00:00 GMT"}, PDT)
            == "2026-08-21"
        )

    def test_iso_timestamp_with_z_suffix(self):
        assert (
            _item_local_date({"timestamp": "2026-08-21T12:00:00Z"}, PDT)
            == "2026-08-21"
        )

    def test_naive_timestamp_treated_as_utc(self):
        assert (
            _item_local_date({"timestamp": "2026-08-21T18:00:00"}, PDT)
            == "2026-08-21"
        )

    def test_garbage_and_missing_return_none(self):
        assert _item_local_date({"timestamp": "not-a-date"}, PDT) is None
        assert _item_local_date({"timestamp": ""}, PDT) is None


class TestFilterItemsByDate:
    def test_keeps_only_target_date_items(self, tmp_path):
        items = [
            _item("Fri, 21 Aug 2026 14:00:00 GMT", "keep1"),
            _item("Fri, 21 Aug 2026 20:00:00 GMT", "keep2"),
            _item("Thu, 20 Aug 2026 15:00:00 GMT", "drop-old"),
            _item("Sat, 22 Aug 2026 18:00:00 GMT", "drop-new"),
            _item("garbage timestamp", "drop-bad"),
        ]
        raw = tmp_path / "raw_items.json"
        raw.write_text(json.dumps(items))

        kept = filter_items_by_date(str(tmp_path), PDT, "2026-08-21")

        assert kept == 2
        surviving = json.loads(raw.read_text())
        assert [i["fetch_id"] for i in surviving] == ["keep1", "keep2"]

    def test_zero_matches_returns_zero(self, tmp_path):
        items = [_item("Mon, 10 Aug 2026 10:00:00 GMT", "old")]
        (tmp_path / "raw_items.json").write_text(json.dumps(items))

        kept = filter_items_by_date(str(tmp_path), PDT, "2026-08-21")

        assert kept == 0
        assert json.loads((tmp_path / "raw_items.json").read_text()) == []


class TestEditorialPromptParam:
    def test_default_prompt_unchanged(self, tmp_run_dir, sample_config, mock_llm_client):
        from stages.editorial import run_editorial

        Path(tmp_run_dir, "report.md").write_text("draft")
        run_editorial(tmp_run_dir, sample_config, mock_llm_client, None)

        assert mock_llm_client.calls[0]["prompt_name"] == "editorial"

    def test_repair_prompt_selected_by_name(
        self, tmp_run_dir, sample_config, mock_llm_client
    ):
        """Repair flows route through the repair_editorial template."""
        from stages.editorial import run_editorial

        mock_llm_client.mock_response = "**Key development:** rebuilt"
        Path(tmp_run_dir, "report.md").write_text("headlines only")

        run_editorial(
            tmp_run_dir, sample_config, mock_llm_client, None,
            prompt_name="repair_editorial",
        )

        assert mock_llm_client.calls[0]["prompt_name"] == "repair_editorial"
        assert Path(tmp_run_dir, "report_edited.md").read_text() == (
            "**Key development:** rebuilt"
        )
