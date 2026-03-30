"""Editorial stage: reviews and improves the draft report."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from models import PipelineConfig
from llm_client import AuditedLLMClient
from audit_logger import AuditedHTTPClient
from prompt_loader import load_prompt

logger = logging.getLogger(__name__)

BIASES_PATH = Path("docs/_data/source_biases.json")


def _run_bias_detection(
    run_path: Path,
    config: PipelineConfig,
    llm_client: AuditedLLMClient,
) -> None:
    """Detect new source biases by comparing how sources framed today's items.

    Appends any new observations as status: suggested to the biases YAML.
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

    model = config.models.get("default", "claude-sonnet-4-5")

    response = llm_client.call(
        stage="editorial",
        prompt_name="detect_biases",
        prompt_version=version,
        system=system,
        user_message=user_msg,
        model=model,
        max_tokens=2048,
    )

    # Strip markdown fences if present
    text = response.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    result = json.loads(text)
    new_biases = result.get("new_biases", [])

    if not new_biases:
        logger.info("Bias detection found no new observations")
        return

    today = date.today().isoformat()
    added = 0
    for entry in new_biases:
        src = entry.get("source", "")
        pattern = entry.get("pattern", "")
        detail = entry.get("detail", "")
        debias = entry.get("debias", "")
        if not src or not pattern or src not in biases_data:
            continue
        # Skip if a similar pattern name already exists
        existing_patterns = [
            b.get("pattern", "").lower()
            for b in biases_data[src].get("biases", [])
        ]
        if any(pattern.lower() in ep or ep in pattern.lower() for ep in existing_patterns if ep):
            continue
        biases_data[src]["biases"].append({
            "pattern": pattern,
            "detail": detail,
            "debias": debias,
            "date_added": today,
            "status": "suggested",
        })
        added += 1

    if added > 0:
        BIASES_PATH.write_text(json.dumps(biases_data, indent=2, ensure_ascii=False) + "\n")
    logger.info(f"Bias detection: {len(new_biases)} candidates, {added} new pattern(s) added")


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

    model = config.models.get("default", "claude-sonnet-4-5")

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
