"""Tests for Pydantic models and config loading."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import RawItem, TranslatedItem, DedupedItem, PipelineConfig, load_config


# ---------------------------------------------------------------------------
# RawItem
# ---------------------------------------------------------------------------


class TestRawItem:
    def test_raw_item_creation(self):
        item = RawItem(
            source="aljazeera",
            source_url="https://aljazeera.com/article/1",
            timestamp="2026-03-29T08:00:00+00:00",
            title="Iran strikes reported near Isfahan",
            text="Explosions were heard near the city of Isfahan early Saturday.",
            language="en",
            fetch_id="abc123",
        )
        assert item.source == "aljazeera"
        assert item.source_url == "https://aljazeera.com/article/1"
        assert item.timestamp == "2026-03-29T08:00:00+00:00"
        assert item.title == "Iran strikes reported near Isfahan"
        assert "Isfahan" in item.text
        assert item.language == "en"
        assert item.fetch_id == "abc123"


# ---------------------------------------------------------------------------
# TranslatedItem
# ---------------------------------------------------------------------------


class TestTranslatedItem:
    def test_translated_item_inherits(self):
        item = TranslatedItem(
            source="iranintl",
            source_url="https://ir.iranintl.com/news/1",
            timestamp="2026-03-29T05:00:00+00:00",
            title="\u062a\u0638\u0627\u0647\u0631\u0627\u062a \u062f\u0631 \u062a\u0647\u0631\u0627\u0646",
            text="\u0635\u062f\u0647\u0627 \u0646\u0641\u0631 \u062f\u0631 \u062a\u0647\u0631\u0627\u0646 \u0628\u0647 \u062e\u06cc\u0627\u0628\u0627\u0646\u200c\u0647\u0627 \u0622\u0645\u062f\u0646\u062f.",
            language="fa",
            fetch_id="ii001",
            text_en="Hundreds took to the streets of Tehran.",
            title_en="Protests in Tehran",
        )
        # Has all RawItem fields
        assert item.source == "iranintl"
        assert item.language == "fa"
        assert item.fetch_id == "ii001"
        # Plus translated fields
        assert item.text_en == "Hundreds took to the streets of Tehran."
        assert item.title_en == "Protests in Tehran"
        # Optional translation_call_id defaults to None
        assert item.translation_call_id is None


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


class TestConfig:
    def test_config_loads(self, sample_config: PipelineConfig):
        assert isinstance(sample_config, PipelineConfig)
        assert len(sample_config.sources) == 3
        assert len(sample_config.buckets) == 9

    def test_config_source_fields(self, sample_config: PipelineConfig):
        for src in sample_config.sources:
            assert src.name, "Every source must have a name"
            assert src.type in ("rss", "scrape"), f"Unknown type: {src.type}"
            assert src.url.startswith("http"), f"URL should be HTTP(S): {src.url}"
            assert src.language in ("en", "fa"), f"Unexpected language: {src.language}"
            # Bias and filter metadata must be present (even if empty)
            assert isinstance(src.known_biases, str)
            assert isinstance(src.filter_instructions, str)
            assert isinstance(src.reliability_notes, str)
            assert isinstance(src.debias_instructions, str)

    def test_config_budget_section(self, sample_config: PipelineConfig):
        assert "max_cost_per_run_usd" in sample_config.budget
        assert sample_config.budget["max_cost_per_run_usd"] > 0

    def test_config_pipeline_section(self, sample_config: PipelineConfig):
        assert "dedup_similarity_threshold" in sample_config.pipeline
        threshold = sample_config.pipeline["dedup_similarity_threshold"]
        assert 0.0 < threshold < 1.0
