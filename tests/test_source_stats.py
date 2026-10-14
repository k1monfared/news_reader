"""Tests for the per-source funnel accumulator."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stages.source_stats import read_source_stats, update_source_stats


def test_creates_and_merges(tmp_path):
    run = str(tmp_path)
    update_source_stats(run, "aljazeera", fetched=25, down=False)
    update_source_stats(run, "aljazeera", prefilter_passed=1)
    update_source_stats(run, "bbc", fetched=35)
    stats = read_source_stats(run)
    assert stats["aljazeera"] == {"fetched": 25, "down": False, "prefilter_passed": 1}
    assert stats["bbc"]["fetched"] == 35


def test_additive_for_counts_and_overwrite_for_bool(tmp_path):
    run = str(tmp_path)
    update_source_stats(run, "aj", fetched=10)
    update_source_stats(run, "aj", fetched=5)
    assert read_source_stats(run)["aj"]["fetched"] == 15
    update_source_stats(run, "aj", down=True)
    update_source_stats(run, "aj", down=False)
    assert read_source_stats(run)["aj"]["down"] is False


def test_tolerates_missing_and_malformed_file(tmp_path):
    run = str(tmp_path)
    assert read_source_stats(run) == {}
    (tmp_path / "source_stats.json").write_text("{not json")
    assert read_source_stats(run) == {}
    update_source_stats(run, "aj", fetched=1)
    assert read_source_stats(run) == {"aj": {"fetched": 1}}


def test_tolerates_non_dict_entries(tmp_path):
    run = str(tmp_path)
    (tmp_path / "source_stats.json").write_text(json.dumps({"aj": 5, "bbc": {"fetched": 2}}))
    update_source_stats(run, "aj", fetched=1)
    stats = read_source_stats(run)
    assert stats["aj"]["fetched"] == 1
    assert stats["bbc"]["fetched"] == 2
