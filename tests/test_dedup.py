"""Tests for the dedup stage clustering logic."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from models import TranslatedItem
from stages.dedup import _cluster_items, _build_deduped_items


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_translated_item(
    fetch_id: str,
    title_en: str,
    text_en: str,
    source: str = "aljazeera",
) -> TranslatedItem:
    """Create a minimal TranslatedItem for testing."""
    return TranslatedItem(
        source=source,
        source_url=f"https://example.com/{fetch_id}",
        timestamp="2026-03-29T08:00:00+00:00",
        title=title_en,
        text=text_en,
        language="en",
        fetch_id=fetch_id,
        text_en=text_en,
        title_en=title_en,
    )


def _compute_similarity(items: list[TranslatedItem]) -> np.ndarray:
    """Build a TF-IDF cosine similarity matrix from a list of items."""
    docs = [item.title_en + " " + item.text_en for item in items]
    tfidf = TfidfVectorizer()
    matrix = tfidf.fit_transform(docs)
    return cosine_similarity(matrix)


# ---------------------------------------------------------------------------
# Clustering tests
# ---------------------------------------------------------------------------


class TestDedupClustering:
    def test_dedup_clusters_duplicates(self):
        """Two items about the same event should end up in one cluster."""
        items = [
            _make_translated_item(
                "a1",
                "Drone strike near Isfahan, Iran denies damage",
                "A drone strike was reported near Isfahan early Saturday. "
                "Iranian officials said air defenses intercepted most drones "
                "and no critical infrastructure was hit.",
                source="aljazeera",
            ),
            _make_translated_item(
                "r1",
                "Oil prices surge on Iran conflict fears",
                "Brent crude jumped 4% to $96 per barrel as traders reacted "
                "to heightened military activity in the Persian Gulf. Analysts "
                "warn disruption to the Strait of Hormuz could push prices above $100.",
                source="reuters",
            ),
            _make_translated_item(
                "a2",
                "Iran drone attack Isfahan, officials deny major damage",
                "A drone attack was reported near Isfahan early Saturday morning. "
                "Iranian officials said air defenses intercepted most of the drones "
                "and that no critical infrastructure was hit. Regional analysts "
                "believe the attack originated from Israeli assets.",
                source="aljazeera",
            ),
        ]

        sim = _compute_similarity(items)
        threshold = 0.5  # Lowered for test: these items share many terms
        clusters = _cluster_items(sim, threshold)

        # Items 0 and 2 are near-duplicates, item 1 is different
        cluster_with_both = None
        for cluster in clusters:
            if 0 in cluster and 2 in cluster:
                cluster_with_both = cluster
                break

        assert cluster_with_both is not None, (
            f"Items 0 and 2 should be in the same cluster. Got clusters: {clusters}"
        )
        # Item 1 (oil prices) should NOT be in that cluster
        assert 1 not in cluster_with_both

    def test_dedup_no_duplicates(self):
        """Three completely different items should each be their own cluster."""
        items = [
            _make_translated_item(
                "x1",
                "UN General Assembly votes on new climate resolution",
                "The United Nations General Assembly passed a resolution calling "
                "for immediate action on carbon emissions. The vote was 143 to 12.",
            ),
            _make_translated_item(
                "x2",
                "Tokyo Olympics committee announces new sports for 2028",
                "Five new sports have been added to the 2028 Olympic program "
                "including cricket and squash. Organizers expect record viewership.",
            ),
            _make_translated_item(
                "x3",
                "SpaceX completes 100th successful Starship landing",
                "SpaceX achieved a milestone with its 100th consecutive Starship "
                "booster landing at the Boca Chica facility in Texas.",
            ),
        ]

        sim = _compute_similarity(items)
        clusters = _cluster_items(sim, threshold=0.85)

        # Every item should be in its own cluster
        assert len(clusters) == 3, (
            f"Expected 3 singleton clusters, got {len(clusters)}: {clusters}"
        )
        for cluster in clusters:
            assert len(cluster) == 1

    def test_dedup_primary_is_longest(self):
        """The primary item in a cluster should be the one with the longest text_en."""
        items = [
            _make_translated_item(
                "s1",
                "Isfahan drone strike",
                "Drones hit Isfahan.",
                source="aljazeera",
            ),
            _make_translated_item(
                "s2",
                "Isfahan drone strike reported, Iran denies major damage",
                "A drone strike was reported near Isfahan early Saturday morning. "
                "Iranian officials said air defenses intercepted most of the drones "
                "and that no critical infrastructure was hit. Regional analysts "
                "believe the attack originated from Israeli-controlled assets. "
                "Multiple explosions were heard across the city.",
                source="reuters",
            ),
        ]

        sim = _compute_similarity(items)
        # Use a low threshold to force these into one cluster
        clusters = _cluster_items(sim, threshold=0.1)

        # They should be in the same cluster
        assert len(clusters) == 1, f"Expected 1 cluster, got: {clusters}"

        deduped = _build_deduped_items(items, clusters, sim, threshold=0.1)

        # Find the primary
        primaries = [d for d in deduped if d.is_primary]
        assert len(primaries) == 1
        primary = primaries[0]

        # The primary should be item s2 (the longer text)
        assert primary.fetch_id == "s2", (
            f"Expected primary to be 's2' (longest text), got '{primary.fetch_id}'"
        )
        assert len(primary.text_en) > len(items[0].text_en)


# ---------------------------------------------------------------------------
# Integration: run_dedup with files
# ---------------------------------------------------------------------------


class TestDedupIntegration:
    def test_run_dedup_writes_output(self, tmp_run_dir, sample_config, mock_llm_client, mock_http_client, sample_translated_items):
        """run_dedup should read translated_items.json and write deduped_items.json."""
        from stages.dedup import run_dedup

        run_path = Path(tmp_run_dir)
        input_path = run_path / "translated_items.json"
        input_path.write_text(json.dumps(sample_translated_items, indent=2))

        result = run_dedup(tmp_run_dir, sample_config, mock_llm_client, mock_http_client)

        output_path = run_path / "deduped_items.json"
        assert output_path.exists(), "deduped_items.json should be created"

        output_data = json.loads(output_path.read_text())
        assert len(output_data) == len(sample_translated_items)
        assert result["total_items"] == len(sample_translated_items)
        assert result["clusters_found"] > 0
        assert result["primary_count"] > 0
        assert result["primary_count"] <= result["total_items"]

    def test_run_dedup_empty_input(self, tmp_run_dir, sample_config, mock_llm_client, mock_http_client):
        """run_dedup should handle an empty item list gracefully."""
        from stages.dedup import run_dedup

        run_path = Path(tmp_run_dir)
        input_path = run_path / "translated_items.json"
        input_path.write_text("[]")

        result = run_dedup(tmp_run_dir, sample_config, mock_llm_client, mock_http_client)

        assert result["total_items"] == 0
        assert result["clusters_found"] == 0
        assert result["primary_count"] == 0
