"""Categorize stage: assigns items to topic buckets."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from models import PipelineConfig, FilteredItem, CategorizedItem
from llm_client import AuditedLLMClient, extract_json
from audit_logger import AuditedHTTPClient
from prompt_loader import load_prompt

logger = logging.getLogger(__name__)


def _parse_json_response(response_text: str) -> list[dict]:
    """Parse LLM JSON, tolerating code fences and surrounding prose."""
    return extract_json(response_text)


def run_categorize(
    run_dir: str,
    config: PipelineConfig,
    llm_client: AuditedLLMClient,
    http_client: AuditedHTTPClient,
) -> dict:
    run_path = Path(run_dir)
    items_path = run_path / "filtered_items.json"
    raw = json.loads(items_path.read_text())
    all_items = [FilteredItem(**item) for item in raw]

    included_items = [item for item in all_items if item.included]
    if not included_items:
        logger.warning("No included items to categorize")
        output = [
            CategorizedItem(**item.model_dump(), primary_category="Other").model_dump()
            for item in all_items
        ]
        (run_path / "categorized_items.json").write_text(json.dumps(output, indent=2))
        return {"items_categorized": 0}

    # Build bucket list for prompt
    bucket_names = [b.name for b in config.buckets]
    bucket_text = "\n".join(
        f"- {b.name}: {b.description} (keywords: {', '.join(b.keywords[:5])})"
        for b in config.buckets
    )

    # Build items text
    items_text = "\n".join(
        f'<item fetch_id="{item.fetch_id}" source="{item.source}">\n'
        f"Title: {item.title_en}\n"
        f"Text: {item.text_en[:300]}\n"
        f"</item>"
        for item in included_items
    )

    # Load prompt and call LLM
    template = load_prompt("categorize", config.paths.get("prompts_dir", "prompts"))
    system, user_msg, version = template.render(buckets=bucket_text, items=items_text)

    model = config.models.get("default", "deepseek-v4-flash-free")

    try:
        response_text = llm_client.call(
            stage="categorize",
            prompt_name="categorize",
            prompt_version=version,
            system=system,
            user_message=user_msg,
            model=model,
        )

        decisions = _parse_json_response(response_text)
        decision_map = {d["fetch_id"]: d for d in decisions}
    except Exception as e:
        logger.error(f"Categorization failed: {e}. Defaulting all to 'Other'.")
        decision_map = {}

    # Log suggested new buckets
    suggestions_path = Path("data/source_analysis")
    suggestions_path.mkdir(parents=True, exist_ok=True)
    suggestions_file = suggestions_path / "suggested_buckets.jsonl"

    # Build categorized items
    categorized = []
    for item in all_items:
        decision = decision_map.get(item.fetch_id, {})
        primary = decision.get("primary_category", "Other")
        secondary = decision.get("secondary_category")
        suggested = decision.get("suggested_new_bucket")

        # Validate category exists
        if primary not in bucket_names:
            logger.warning(f"Unknown category '{primary}' for {item.fetch_id}, using 'Other'")
            primary = "Other"
        if secondary and secondary not in bucket_names:
            secondary = None

        # Log suggested new bucket
        if suggested:
            with open(suggestions_file, "a") as f:
                f.write(json.dumps({
                    "fetch_id": item.fetch_id,
                    "suggested": suggested,
                    "source": item.source,
                }) + "\n")

        if not item.included:
            primary = "Other"
            secondary = None

        categorized.append(CategorizedItem(
            **item.model_dump(),
            primary_category=primary,
            secondary_category=secondary,
        ))

    output_path = run_path / "categorized_items.json"
    output_path.write_text(
        json.dumps([item.model_dump() for item in categorized], indent=2)
    )

    logger.info(f"Categorized {len(included_items)} items into buckets")
    return {"items_categorized": len(included_items)}
