"""Editorial stage: reviews and improves the draft report."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from models import PipelineConfig
from llm_client import AuditedLLMClient
from audit_logger import AuditedHTTPClient
from prompt_loader import load_prompt

logger = logging.getLogger(__name__)


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

    # Read raw categorized data for reference
    cat_path = run_path / "categorized_items.json"
    raw_data = cat_path.read_text() if cat_path.exists() else "[]"

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
    return {"status": "completed"}
