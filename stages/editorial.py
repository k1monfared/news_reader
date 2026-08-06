"""Editorial stage: reviews and improves the draft report."""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path

from models import PipelineConfig
from llm_client import AuditedLLMClient, extract_json
from audit_logger import AuditedHTTPClient
from prompt_loader import load_prompt

logger = logging.getLogger(__name__)

BIASES_PATH = Path("docs/_data/source_biases.json")

# Stop words for bias dedup similarity
_STOP = {
    "a", "an", "the", "of", "in", "to", "for", "and", "or", "on", "by",
    "with", "as", "at", "from", "is", "it", "that", "this", "be", "via",
}


def _tokenize(text: str) -> set[str]:
    """Extract meaningful lowercase words."""
    return set(re.findall(r"[a-z]+", text.lower())) - _STOP


def _similarity(a: str, b: str) -> float:
    """Jaccard similarity between two text strings."""
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _find_duplicate(new_pattern: str, new_detail: str, existing_biases: list[dict]) -> dict | None:
    """Check if a new bias duplicates an existing one.

    Compares pattern names and detail text using word overlap.
    Returns the matching existing bias, or None if no duplicate found.
    """
    new_combined = f"{new_pattern} {new_detail}"

    for existing in existing_biases:
        ep = existing.get("pattern", "")
        ed = existing.get("detail", "")
        existing_combined = f"{ep} {ed}"

        # Check pattern name substring match
        if new_pattern.lower() in ep.lower() or ep.lower() in new_pattern.lower():
            return existing

        # Check semantic similarity on pattern + detail
        sim = _similarity(new_combined, existing_combined)
        if sim >= 0.35:
            return existing

    return None


def _run_bias_detection(
    run_path: Path,
    config: PipelineConfig,
    llm_client: AuditedLLMClient,
) -> None:
    """Detect new source biases by comparing how sources framed today's items.

    Before adding a new bias, checks all existing biases for that source
    for duplicates. If a duplicate is found, the existing bias is updated
    to be more inclusive and its status is set to suggested.
    If no duplicate exists, the new bias is appended with status: suggested.

    This is non-fatal: any error is logged and silently skipped.
    """
    # Load existing biases
    if not BIASES_PATH.exists():
        logger.info("No source_biases.json found, skipping bias detection")
        return

    biases_data = json.loads(BIASES_PATH.read_text()) or {}

    # Load today's items (prefer tracked, fall back to categorized)
    tracked_path = run_path / "tracked_items.json"
    cat_path = run_path / "categorized_items.json"
    if tracked_path.exists():
        items = json.loads(tracked_path.read_text())
    elif cat_path.exists():
        items = json.loads(cat_path.read_text())
    else:
        logger.info("No items found for bias detection")
        return

    if not items:
        return

    # Group items by source
    by_source: dict[str, list[dict]] = {}
    for item in items:
        src = item.get("source", "unknown")
        by_source.setdefault(src, []).append({
            "title": item.get("title_en") or item.get("title", ""),
            "text": item.get("text_en") or item.get("text", ""),
            "source_url": item.get("source_url", ""),
        })

    # Format existing biases for the prompt
    existing_summary = []
    for src_key, src_data in biases_data.items():
        patterns = []
        for b in src_data.get("biases", []):
            pattern = b.get("pattern", b.get("observation", ""))
            patterns.append(pattern)
        if patterns:
            existing_summary.append(f"{src_key}: {'; '.join(patterns)}")
    existing_biases_text = "\n".join(existing_summary) if existing_summary else "None yet."

    items_by_source_text = json.dumps(by_source, indent=2, ensure_ascii=False)

    # Load and render prompt
    prompts_dir = config.paths.get("prompts_dir", "prompts")
    template = load_prompt("detect_biases", prompts_dir)
    system, user_msg, version = template.render(
        existing_biases=existing_biases_text,
        items_by_source=items_by_source_text,
    )

    model = config.models.get("default", "deepseek-v4-flash-free")

    response = llm_client.call(
        stage="editorial",
        prompt_name="detect_biases",
        prompt_version=version,
        system=system,
        user_message=user_msg,
        model=model,
        max_tokens=4096,
    )

    result = extract_json(response)
    new_biases = result.get("new_biases", [])

    if not new_biases:
        logger.info("Bias detection found no new observations")
        return

    today = date.today().isoformat()
    added = 0
    updated = 0

    for entry in new_biases:
        src = entry.get("source", "")
        pattern = entry.get("pattern", "")
        detail = entry.get("detail", "")
        debias = entry.get("debias", "")
        example_text = entry.get("example_text", "")
        example_url = entry.get("example_url", "")
        unbiased_text = entry.get("unbiased_text", "")

        if not src or not pattern or src not in biases_data:
            continue

        source_biases = biases_data[src].get("biases", [])

        # Check for duplicate against ALL existing biases for this source
        duplicate = _find_duplicate(pattern, detail, source_biases)

        if duplicate:
            # Update existing bias to be more inclusive
            old_detail = duplicate.get("detail", "")
            if detail and detail.lower() not in old_detail.lower():
                duplicate["detail"] = f"{old_detail} Also: {detail}"
            # Update debias if the new one adds value
            old_debias = duplicate.get("debias", "")
            if debias and debias.lower() not in old_debias.lower():
                duplicate["debias"] = f"{old_debias} {debias}"
            # Add example if not already present
            if example_text and not duplicate.get("example_text"):
                duplicate["example_text"] = example_text
                duplicate["example_url"] = example_url
                duplicate["unbiased_text"] = unbiased_text
            # Reset status to suggested so the user re-reviews
            if duplicate.get("status") == "confirmed":
                duplicate["status"] = "suggested"
                logger.info(
                    f"Bias '{duplicate['pattern']}' for {src} updated and "
                    f"status reset to suggested"
                )
            updated += 1
        else:
            # Add as new bias
            new_entry = {
                "pattern": pattern,
                "detail": detail,
                "debias": debias,
                "date_added": today,
                "status": "suggested",
            }
            if example_text:
                new_entry["example_text"] = example_text
                new_entry["example_url"] = example_url
                new_entry["unbiased_text"] = unbiased_text
            source_biases.append(new_entry)
            added += 1

    if added > 0 or updated > 0:
        BIASES_PATH.write_text(json.dumps(biases_data, indent=2, ensure_ascii=False) + "\n")
    logger.info(
        f"Bias detection: {len(new_biases)} candidates, "
        f"{added} new, {updated} updated existing"
    )


def run_editorial(
    run_dir: str,
    config: PipelineConfig,
    llm_client: AuditedLLMClient,
    http_client: AuditedHTTPClient,
) -> dict:
    run_path = Path(run_dir)

    # Read draft report
    report_path = run_path / "report.md"
    if not report_path.exists():
        logger.warning("No draft report found, skipping editorial")
        return {"status": "skipped", "reason": "no_draft"}

    report = report_path.read_text()

    # Read raw data for reference (prefer tracked, fall back to categorized)
    tracked_path = run_path / "tracked_items.json"
    cat_path = run_path / "categorized_items.json"
    if tracked_path.exists():
        raw_data = tracked_path.read_text()
    elif cat_path.exists():
        raw_data = cat_path.read_text()
    else:
        raw_data = "[]"

    # Load prompt
    template = load_prompt("editorial", config.paths.get("prompts_dir", "prompts"))
    system, user_msg, version = template.render(report=report, raw_data=raw_data)

    model = config.models.get("default", "deepseek-v4-flash-free")

    try:
        edited = llm_client.call(
            stage="editorial",
            prompt_name="editorial",
            prompt_version=version,
            system=system,
            user_message=user_msg,
            model=model,
            max_tokens=8192,
        )
    except Exception as e:
        logger.error(f"Editorial LLM call failed: {e}. Using draft as-is.")
        edited = report

    (run_path / "report_edited.md").write_text(edited)
    logger.info("Editorial review complete")

    # Bias detection (non-fatal)
    try:
        _run_bias_detection(run_path, config, llm_client)
    except Exception as e:
        logger.warning(f"Bias detection failed (non-fatal): {e}")

    return {"status": "completed"}
