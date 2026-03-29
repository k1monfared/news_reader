"""Dedup stage: clusters near-duplicate items using TF-IDF cosine similarity."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from models import PipelineConfig, TranslatedItem, DedupedItem
from llm_client import AuditedLLMClient
from audit_logger import AuditedHTTPClient

logger = logging.getLogger(__name__)


def _cluster_items(
    similarity_matrix: np.ndarray,
    threshold: float,
) -> list[list[int]]:
    """Greedy clustering: iterate items, group unclustered items above threshold."""
    n = similarity_matrix.shape[0]
    assigned = [False] * n
    clusters: list[list[int]] = []

    for i in range(n):
        if assigned[i]:
            continue
        cluster = [i]
        assigned[i] = True
        for j in range(i + 1, n):
            if assigned[j]:
                continue
            if similarity_matrix[i, j] > threshold:
                cluster.append(j)
                assigned[j] = True
        clusters.append(cluster)

    return clusters


def _build_deduped_items(
    items: list[TranslatedItem],
    clusters: list[list[int]],
    similarity_matrix: np.ndarray,
    threshold: float,
) -> list[DedupedItem]:
    """Convert clusters into DedupedItem objects."""
    deduped: list[DedupedItem] = []

    for cluster in clusters:
        # Pick primary: item with longest text_en
        primary_idx = max(cluster, key=lambda idx: len(items[idx].text_en))
        event_id = items[cluster[0]].fetch_id

        for idx in cluster:
            item = items[idx]
            is_primary = idx == primary_idx

            # Related sources: other items in the cluster
            related_sources = [
                items[other].source
                for other in cluster
                if other != idx
            ]

            # Similarity scores: all items with score > 0.3
            sim_scores: dict[str, float] = {}
            for j in range(len(items)):
                if j == idx:
                    continue
                score = float(similarity_matrix[idx, j])
                if score > 0.3:
                    sim_scores[items[j].fetch_id] = round(score, 4)

            deduped.append(DedupedItem(
                **item.model_dump(),
                event_id=event_id,
                is_primary=is_primary,
                related_sources=related_sources,
                similarity_scores=sim_scores,
                cluster_method="tfidf_cosine",
                cluster_threshold=threshold,
            ))

    return deduped


def _build_similarity_audit(
    items: list[TranslatedItem],
    similarity_matrix: np.ndarray,
) -> dict[str, dict[str, float]]:
    """Build full similarity audit dict for scores > 0.3."""
    audit: dict[str, dict[str, float]] = {}

    for i in range(len(items)):
        fetch_id_i = items[i].fetch_id
        row: dict[str, float] = {}
        for j in range(len(items)):
            if i == j:
                continue
            score = float(similarity_matrix[i, j])
            if score > 0.3:
                row[items[j].fetch_id] = round(score, 4)
        if row:
            audit[fetch_id_i] = row

    return audit


def _fallback_all_primary(
    items: list[TranslatedItem],
    threshold: float,
) -> list[DedupedItem]:
    """Fallback: mark every item as primary with no clustering."""
    return [
        DedupedItem(
            **item.model_dump(),
            event_id=item.fetch_id,
            is_primary=True,
            related_sources=[],
            similarity_scores={},
            cluster_method="tfidf_cosine",
            cluster_threshold=threshold,
        )
        for item in items
    ]


def run_dedup(
    run_dir: str,
    config: PipelineConfig,
    llm_client: AuditedLLMClient,
    http_client: AuditedHTTPClient,
) -> dict:
    """Run the deduplication stage.

    Returns:
        Stage result dict with total_items, clusters_found, primary_count.
    """
    run_path = Path(run_dir)
    threshold = config.pipeline.get("dedup_similarity_threshold", 0.85)

    # Load translated items
    input_path = run_path / "translated_items.json"
    raw_data = json.loads(input_path.read_text(encoding="utf-8"))
    items = [TranslatedItem(**d) for d in raw_data]

    if not items:
        output_path = run_path / "deduped_items.json"
        output_path.write_text("[]")
        return {"total_items": 0, "clusters_found": 0, "primary_count": 0}

    try:
        # Build TF-IDF vectors from title_en + text_en
        documents = [
            item.title_en + " " + item.text_en
            for item in items
        ]
        vectorizer = TfidfVectorizer()
        tfidf_matrix = vectorizer.fit_transform(documents)

        # Compute pairwise cosine similarity
        sim_matrix = cosine_similarity(tfidf_matrix)

        # Greedy clustering
        clusters = _cluster_items(sim_matrix, threshold)

        # Build deduped items
        deduped = _build_deduped_items(items, clusters, sim_matrix, threshold)

        # Save similarity audit data
        audit_dir = run_path / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit_data = _build_similarity_audit(items, sim_matrix)
        audit_path = audit_dir / "dedup_similarity_matrix.json"
        audit_path.write_text(json.dumps(audit_data, indent=2))

        primary_count = sum(1 for d in deduped if d.is_primary)

    except Exception as e:
        logger.error(f"TF-IDF dedup failed, passing all items as primary: {e}", exc_info=True)
        deduped = _fallback_all_primary(items, threshold)
        clusters = [[i] for i in range(len(items))]
        primary_count = len(items)

    # Write output
    output_path = run_path / "deduped_items.json"
    output_path.write_text(
        json.dumps([d.model_dump() for d in deduped], indent=2)
    )

    logger.info(
        f"Dedup complete: {len(items)} items -> {len(clusters)} clusters, "
        f"{primary_count} primaries"
    )

    return {
        "total_items": len(items),
        "clusters_found": len(clusters),
        "primary_count": primary_count,
    }
