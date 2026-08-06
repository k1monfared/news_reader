"""Filter stage: decides which deduped items to include in the daily brief."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from models import DedupedItem, FilteredItem, PipelineConfig
from llm_client import AuditedLLMClient, extract_json
from audit_logger import AuditedHTTPClient
from prompt_loader import load_prompt
import bias_tracker

logger = logging.getLogger(__name__)

BATCH_SIZE = 10
CACHE_PATH = Path("data/cache/filter_cache.json")


def _load_cache() -> dict:
    """Load the filter decision cache from disk."""
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Could not load filter cache: {e}")
    return {}


def _save_cache(cache: dict) -> None:
    """Persist the filter decision cache to disk."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _cache_is_valid(entry: dict, ttl_hours: int) -> bool:
    """Return True if a cache entry has not expired."""
    cached_at = entry.get("cached_at", 0)
    return (time.time() - cached_at) < ttl_hours * 3600


def _find_source_config(source_name: str, config: PipelineConfig):
    """Find the SourceConfig matching a given source name."""
    for src in config.sources:
        if src.name == source_name:
            return src
    return None


def _build_batch_payload(
    items: list[DedupedItem], config: PipelineConfig
) -> str:
    """Format a batch of DedupedItem objects with per-source bias info."""
    batch = []
    for item in items:
        entry = {
            "fetch_id": item.fetch_id,
            "title": item.title_en,
            "text": item.text_en,
            "source": item.source,
        }
        src_cfg = _find_source_config(item.source, config)
        if src_cfg:
            entry["filter_instructions"] = src_cfg.filter_instructions
            entry["known_biases"] = src_cfg.known_biases
        batch.append(entry)
    return json.dumps(batch, ensure_ascii=False, indent=2)


def _parse_filter_response(response_text: str) -> list[dict]:
    """Parse the LLM JSON response into a list of filter decision dicts."""
    return extract_json(response_text)


def run_filter(
    run_dir: str,
    config: PipelineConfig,
    llm_client: AuditedLLMClient,
    http_client: AuditedHTTPClient,
) -> dict:
    """Run the filter stage.

    Reads deduped_items.json, filters items via batched LLM calls, and
    writes filtered_items.json.

    Returns:
        Stage result dict with filter stats.
    """
    run_path = Path(run_dir)
    ttl_hours = config.pipeline.get("cache_ttl_hours", 24)

    # Load deduped items
    deduped_path = run_path / "deduped_items.json"
    deduped_data = json.loads(deduped_path.read_text(encoding="utf-8"))
    items = [DedupedItem(**d) for d in deduped_data]

    primary_items = [item for item in items if item.is_primary]
    non_primary_items = [item for item in items if not item.is_primary]

    logger.info(
        f"Filter stage: {len(items)} total, "
        f"{len(primary_items)} primary, {len(non_primary_items)} non-primary"
    )

    # Load cache and prompt template
    cache = _load_cache()
    prompt_template = load_prompt("filter")

    # Track stats
    cache_hits = 0
    decisions: dict[str, dict] = {}  # fetch_id -> decision dict

    # Collect primary items that need LLM evaluation
    items_needing_llm: list[DedupedItem] = []

    for item in primary_items:
        cached_entry = cache.get(item.fetch_id)
        if cached_entry and _cache_is_valid(cached_entry, ttl_hours):
            decisions[item.fetch_id] = cached_entry
            cache_hits += 1
        else:
            items_needing_llm.append(item)

    # Process items in batches
    for batch_start in range(0, len(items_needing_llm), BATCH_SIZE):
        batch = items_needing_llm[batch_start : batch_start + BATCH_SIZE]
        batch_payload = _build_batch_payload(batch, config)

        system, user_message, version = prompt_template.render(items=batch_payload)

        try:
            response_text = llm_client.call(
                stage="filter",
                prompt_name="filter",
                prompt_version=version,
                system=system,
                user_message=user_message,
            )
            parsed = _parse_filter_response(response_text)

            for entry in parsed:
                entry["cached_at"] = time.time()
                decisions[entry["fetch_id"]] = entry
                cache[entry["fetch_id"]] = entry

            logger.info(
                f"Filtered batch {batch_start // BATCH_SIZE + 1}: "
                f"{len(parsed)} decisions"
            )
        except Exception as e:
            logger.error(
                f"Filter LLM call failed for batch starting at index "
                f"{batch_start}: {e}",
                exc_info=True,
            )
            # Fallback: check cache first, then pass all with a warning
            for item in batch:
                cached_entry = cache.get(item.fetch_id)
                if cached_entry:
                    decisions[item.fetch_id] = cached_entry
                    logger.info(
                        f"Using expired cache for {item.fetch_id} after LLM failure"
                    )
                else:
                    decisions[item.fetch_id] = {
                        "fetch_id": item.fetch_id,
                        "included": True,
                        "confidence": 0.0,
                        "filter_reason": "LLM unavailable, passed by default",
                    }
                    logger.warning(
                        f"No cache for {item.fetch_id}, passing by default"
                    )

    # Save updated cache
    _save_cache(cache)

    # Build primary decision lookup by event_id for non-primary inheritance
    event_decisions: dict[str, dict] = {}
    for item in primary_items:
        decision = decisions.get(item.fetch_id, {})
        event_decisions[item.event_id] = decision

    # Build FilteredItem list
    filtered: list[FilteredItem] = []

    for item in primary_items:
        decision = decisions.get(item.fetch_id, {})
        included = decision.get("included", True)
        confidence = decision.get("confidence", 0.0)
        filter_reason = decision.get("filter_reason", "")
        sole_source = (
            len(item.related_sources) == 0 and confidence < 0.8
        )

        filtered.append(
            FilteredItem(
                **item.model_dump(),
                included=included,
                confidence=confidence,
                filter_reason=filter_reason,
                sole_source_flag=sole_source,
            )
        )

    for item in non_primary_items:
        primary_decision = event_decisions.get(item.event_id, {})
        included = primary_decision.get("included", True)
        confidence = primary_decision.get("confidence", 0.0)
        filter_reason = primary_decision.get(
            "filter_reason", "inherited from cluster primary"
        )
        sole_source = (
            len(item.related_sources) == 0 and confidence < 0.8
        )

        filtered.append(
            FilteredItem(
                **item.model_dump(),
                included=included,
                confidence=confidence,
                filter_reason=filter_reason,
                sole_source_flag=sole_source,
            )
        )

    # Write output
    output_path = run_path / "filtered_items.json"
    output_path.write_text(
        json.dumps(
            [item.model_dump() for item in filtered],
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # Track bias patterns
    bias_tracker.track_biases(filtered, run_dir)

    items_included = sum(1 for item in filtered if item.included)
    items_excluded = len(filtered) - items_included

    logger.info(
        f"Filter complete: {len(filtered)} items total, "
        f"{items_included} included, {items_excluded} excluded, "
        f"{cache_hits} cache hits"
    )

    return {
        "items_in": len(items),
        "items_included": items_included,
        "items_excluded": items_excluded,
        "cache_hits": cache_hits,
    }
