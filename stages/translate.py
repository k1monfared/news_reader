"""Translate stage: translates Farsi items to English via batched LLM calls."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from models import PipelineConfig, RawItem, TranslatedItem
from llm_client import AuditedLLMClient
from audit_logger import AuditedHTTPClient
from prompt_loader import load_prompt

logger = logging.getLogger(__name__)

BATCH_SIZE = 10


def _build_batch_payload(items: list[RawItem]) -> str:
    """Format a batch of RawItem objects as JSON for the prompt template."""
    batch = [
        {
            "fetch_id": item.fetch_id,
            "title": item.title,
            "text": item.text,
        }
        for item in items
    ]
    return json.dumps(batch, ensure_ascii=False, indent=2)


def _parse_translation_response(response_text: str) -> list[dict]:
    """Parse the LLM JSON response into a list of translation dicts.

    Each dict is expected to have keys: fetch_id, title_en, text_en.
    """
    # Strip markdown code fences if the model wraps its response
    text = response_text.strip()
    if text.startswith("```"):
        # Remove opening fence (possibly ```json)
        first_newline = text.index("\n")
        text = text[first_newline + 1:]
    if text.endswith("```"):
        text = text[:-3].rstrip()

    return json.loads(text)


def run_translate(
    run_dir: str,
    config: PipelineConfig,
    llm_client: AuditedLLMClient,
    http_client: AuditedHTTPClient,
) -> dict:
    """Run the translate stage.

    Reads raw_items.json, translates Farsi items in batches, and writes
    translated_items.json.

    Returns:
        Stage result dict with translation stats.
    """
    run_path = Path(run_dir)

    # Load raw items
    raw_path = run_path / "raw_items.json"
    raw_data = json.loads(raw_path.read_text(encoding="utf-8"))
    raw_items = [RawItem(**item) for item in raw_data]

    # Split into English and Farsi items
    english_items: list[RawItem] = []
    farsi_items: list[RawItem] = []
    for item in raw_items:
        if item.language == "fa":
            farsi_items.append(item)
        else:
            english_items.append(item)

    logger.info(
        f"Translate stage: {len(raw_items)} total, "
        f"{len(english_items)} English, {len(farsi_items)} Farsi"
    )

    # Build translated items for English (no LLM call needed)
    translated: list[TranslatedItem] = []
    for item in english_items:
        translated.append(
            TranslatedItem(
                **item.model_dump(),
                text_en=item.text,
                title_en=item.title,
                translation_call_id=None,
            )
        )

    # Load the translate prompt template
    prompt_template = load_prompt("translate")

    # Translate Farsi items in batches
    translation_map: dict[str, dict] = {}
    failed_fetch_ids: set[str] = set()

    for batch_start in range(0, len(farsi_items), BATCH_SIZE):
        batch = farsi_items[batch_start : batch_start + BATCH_SIZE]
        batch_payload = _build_batch_payload(batch)

        system, user_message, version = prompt_template.render(items=batch_payload)

        try:
            response_text = llm_client.call(
                stage="translate",
                prompt_name="translate",
                prompt_version=version,
                system=system,
                user_message=user_message,
            )
            translations = _parse_translation_response(response_text)

            for entry in translations:
                translation_map[entry["fetch_id"]] = entry

            logger.info(
                f"Translated batch {batch_start // BATCH_SIZE + 1}: "
                f"{len(translations)} items"
            )
        except Exception as e:
            logger.error(
                f"Translation failed for batch starting at index {batch_start}: {e}",
                exc_info=True,
            )
            for item in batch:
                failed_fetch_ids.add(item.fetch_id)

    # Build translated items for Farsi items
    for item in farsi_items:
        if item.fetch_id in failed_fetch_ids or item.fetch_id not in translation_map:
            # Fallback: use original text if translation failed
            logger.warning(
                f"Using fallback (original text) for item {item.fetch_id}"
            )
            translated.append(
                TranslatedItem(
                    **item.model_dump(),
                    text_en=item.text,
                    title_en=item.title,
                    translation_call_id=None,
                )
            )
        else:
            entry = translation_map[item.fetch_id]
            translated.append(
                TranslatedItem(
                    **item.model_dump(),
                    text_en=entry["text_en"],
                    title_en=entry["title_en"],
                    translation_call_id=entry.get("call_id"),
                )
            )

    # Write output
    output_path = run_path / "translated_items.json"
    output_path.write_text(
        json.dumps(
            [item.model_dump() for item in translated],
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    translated_count = len(farsi_items) - len(failed_fetch_ids)
    fallback_count = len(failed_fetch_ids)

    logger.info(
        f"Translate complete: {len(translated)} items total, "
        f"{translated_count} translated, {len(english_items)} English pass-through, "
        f"{fallback_count} fallback"
    )

    return {
        "total": len(translated),
        "translated_count": translated_count,
        "english_count": len(english_items),
        "fallback_count": fallback_count,
    }
