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

# Deterministic Iran war signal keywords for pre-filter and post-verification.
# An item must contain at least one of these substrings (case-insensitive) in
# its title or text to be considered for Iran-war inclusion. This catches
# obvious non-war items like Dolly Parton or Nepal floods without an LLM call.
IRAN_WAR_KEYWORDS = [
    "iran",
    "iranian",
    "irgc",
    "qods",
    "quds",
    "khamenei",
    "pezeshkian",
    "tehran",
    "hormuz",
    "persian gulf",
    "natanz",
    "fordow",
    "bushehr",
    "parchin",
    "kharg",
    "larak",
    "ahvaz",
    "bandar abbas",
    "islamic republic",
    "supreme leader",
    "centrifuge",
    "enrichment",
    "strait of hormuz",
]


def _has_iran_signal(text: str, keywords: list[str] | None = None) -> bool:
    """Return True if text contains an Iran war signal keyword."""
    lower = text.lower()
    kws = keywords if keywords is not None else IRAN_WAR_KEYWORDS
    return any(kw.lower() in lower for kw in kws)


def _passes_iran_post_filter(item: DedupedItem, decision: dict, keywords: list[str] | None = None) -> dict:
    """Post-verification after LLM INCLUDE. Demote to EXCLUDE if no Iran signal.

    The LLM can hallucinate INCLUDE for globally important but non-Iran items
    like Venezuela oil or Nepal floods that mention Iran only in passing or not
    at all. This guard ensures the brief stays strictly on the Iran war.
    """
    if not decision.get("included"):
        return decision
    combined = f"{item.title_en or ''} {item.text_en or ''}"
    if not _has_iran_signal(combined, keywords):
        # Low confidence LLM includes without any Iran signal are almost
        # certainly non-war filler. Override to EXCLUDE.
        confidence = decision.get("confidence", 0.0)
        if confidence < 0.9:
            return {
                "fetch_id": decision["fetch_id"],
                "included": False,
                "confidence": 0.95,
                "filter_reason": "Post-filter: no Iran war signal (title/text lacks Iran/Hormuz/IRGC/etc.)",
            }
    return decision


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
    iran_keywords = config.pipeline.get("iran_keywords") or IRAN_WAR_KEYWORDS

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

    # Collect primary items that need LLM evaluation, with deterministic
    # pre-filter that drops obvious non-Iran items without an LLM call.
    items_needing_llm: list[DedupedItem] = []
    prefiltered_excluded = 0

    for item in primary_items:
        combined = f"{item.title_en or ''} {item.text_en or ''}"
        if not _has_iran_signal(combined, iran_keywords):
            # Pre-filter: no Iran war signal at all, exclude immediately
            decision = {
                "fetch_id": item.fetch_id,
                "included": False,
                "confidence": 0.99,
                "filter_reason": "Pre-filter: no Iran war signal (title/text lacks Iran/Hormuz/IRGC/etc.)",
                "cached_at": time.time(),
            }
            decisions[item.fetch_id] = decision
            cache[item.fetch_id] = decision
            prefiltered_excluded += 1
            continue

        cached_entry = cache.get(item.fetch_id)
        if cached_entry and _cache_is_valid(cached_entry, ttl_hours):
            # Even cached INCLUDE decisions get post-verified to guard
            # against stale LLM hallucinations
            verified = _passes_iran_post_filter(item, cached_entry, iran_keywords)
            decisions[item.fetch_id] = verified
            if not verified.get("included") and cached_entry.get("included"):
                logger.info(f"Post-filter corrected cached INCLUDE for {item.fetch_id}")
            cache_hits += 1
        else:
            items_needing_llm.append(item)

    if prefiltered_excluded:
        logger.info(f"Pre-filter excluded {prefiltered_excluded} items with no Iran signal before LLM")

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

            # Post-verify each LLM decision to ensure Iran war scope
            for entry in parsed:
                entry["cached_at"] = time.time()
                # Find the DedupedItem for this fetch_id for post-filter context
                item_for_entry = next((it for it in batch if it.fetch_id == entry["fetch_id"]), None)
                if item_for_entry is not None:
                    entry = _passes_iran_post_filter(item_for_entry, entry, iran_keywords)
                    if not entry.get("included"):
                        logger.info(f"Post-filter excluded {entry['fetch_id']} after LLM INCLUDE")
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
            # Fail behavior when LLM is unavailable: for items that already
            # passed the deterministic Iran signal pre-filter, we keep them
            # (they are highly likely Iran war). For any item without a
            # signal (should not be in this batch, but guard anyway) we
            # exclude. This keeps briefs focused while not dropping real war
            # news during outages.
            for item in batch:
                cached_entry = cache.get(item.fetch_id)
                if cached_entry:
                    # Even expired cache gets post-verified
                    verified = _passes_iran_post_filter(item, cached_entry, iran_keywords)
                    decisions[item.fetch_id] = verified
                    logger.info(
                        f"Using expired cache for {item.fetch_id} after LLM failure (verified included={verified.get('included')})"
                    )
                else:
                    combined = f"{item.title_en or ''} {item.text_en or ''}"
                    if _has_iran_signal(combined, iran_keywords):
                        decisions[item.fetch_id] = {
                            "fetch_id": item.fetch_id,
                            "included": True,
                            "confidence": 0.6,
                            "filter_reason": "LLM unavailable, included by Iran keyword fallback",
                            "cached_at": time.time(),
                        }
                        logger.warning(
                            f"No cache for {item.fetch_id}, included by keyword fallback (LLM down)"
                        )
                    else:
                        decisions[item.fetch_id] = {
                            "fetch_id": item.fetch_id,
                            "included": False,
                            "confidence": 0.95,
                            "filter_reason": "LLM unavailable, excluded by default (fail-closed for Iran war focus)",
                            "cached_at": time.time(),
                        }
                        logger.warning(
                            f"No cache for {item.fetch_id}, excluded by default (fail-closed)"
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
