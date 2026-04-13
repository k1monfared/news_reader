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
            source="aljazeera",
            source_url="https://aljazeera.com/news/1",
            timestamp="2026-03-29T05:00:00+00:00",
            title="Iran strikes reported near Isfahan",
            text="Explosions heard near Isfahan early Saturday.",
            language="en",
            fetch_id="aj001",
            text_en="Explosions heard near Isfahan early Saturday.",
            title_en="Iran strikes reported near Isfahan",
        )
        # Has all RawItem fields
        assert item.source == "aljazeera"
        assert item.language == "en"
        assert item.fetch_id == "aj001"
        # Plus translated fields
        assert item.text_en == "Explosions heard near Isfahan early Saturday."
        assert item.title_en == "Iran strikes reported near Isfahan"
        # Optional translation_call_id defaults to None
        assert item.translation_call_id is None


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


class TestConfig:
    def test_config_loads(self, sample_config: PipelineConfig):
        assert isinstance(sample_config, PipelineConfig)
        assert len(sample_config.sources) == 4
        assert len(sample_config.buckets) == 9

    def test_config_source_fields(self, sample_config: PipelineConfig):
        for src in sample_config.sources:
            assert src.name, "Every source must have a name"
            assert src.type == "rss", f"Unknown type: {src.type}"
            assert src.url.startswith("http"), f"URL should be HTTP(S): {src.url}"
            assert src.language == "en", f"Unexpected language: {src.language}"
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
