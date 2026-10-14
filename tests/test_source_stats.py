"""Tests for the per-source funnel accumulator."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import stages.summarize as summarize_module
from stages.source_stats import (
    count_by_source,
    read_source_stats,
    summarize_posted_counts,
    update_source_stats,
)


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


def test_count_by_source():
    items = [{"source": "aj"}, {"source": "aj"}, {"source": "bbc"}]
    assert count_by_source(items, lambda i: True) == {"aj": 2, "bbc": 1}
    assert count_by_source(items, lambda i: i["source"] == "aj") == {"aj": 2}


def test_summarize_posted_counts_includes_related():
    items = [
        {"source": "bbc", "related_sources": ["aj"]},
        {"source": "france24", "related_sources": []},
    ]
    assert summarize_posted_counts(items) == {"bbc": 1, "aj": 1, "france24": 1}


class _FakeTrackedItem:
    def __init__(self, fetch_id, source, *, included=True, related=()):
        self.fetch_id = fetch_id
        self.source = source
        self.included = included
        self.primary_category = "Other"
        self.related_sources = list(related)
        self.sole_source_flag = False
        self.story_status = "new"
        self.title_en = "Headline"
        self.text_en = "Body text for the item. " * 5
        self.source_url = "https://example.com"
        self.development_note = None
        self.story_timeline = []


def test_run_summarize_records_posted_per_source(
    tmp_path, monkeypatch, sample_config, mock_llm_client
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    items = [
        _FakeTrackedItem("aj1", "aljazeera"),
        _FakeTrackedItem("bb1", "bbc", related=["aljazeera"]),
        _FakeTrackedItem("rt1", "reuters", included=False),
    ]
    monkeypatch.setattr(summarize_module, "_load_items", lambda p: items)

    summarize_module.run_summarize(
        str(run_dir), sample_config, mock_llm_client, None
    )

    stats = read_source_stats(str(run_dir))
    # aljazeera is credited as the source of aj1 and as a related source of bb1.
    assert stats["aljazeera"]["posted"] == 2
    assert stats["bbc"]["posted"] == 1
    # reuters was not included, so it is not credited.
    assert "posted" not in stats.get("reuters", {})
