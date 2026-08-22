"""Summarize stage: produces the daily executive brief with expandable format."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from models import PipelineConfig, TrackedItem, CategorizedItem
from llm_client import AuditedLLMClient
from audit_logger import AuditedHTTPClient
from prompt_loader import load_prompt

logger = logging.getLogger(__name__)


def _build_frontmatter(config: PipelineConfig, sources_down: list[str]) -> str:
    """Build Jekyll frontmatter for the report."""
    tz = timezone(timedelta(hours=-7))
    now = datetime.now(tz)
    return (
        "---\n"
        "layout: post\n"
        f'title: "Daily Brief: {now.strftime("%B %d, %Y")}"\n'
        f"date: {now.strftime('%Y-%m-%d')}\n"
        "categories: [daily-brief]\n"
        f"sources_down: {json.dumps(sources_down)}\n"
        "---\n\n"
    )


def _build_debias_notes(config: PipelineConfig) -> str:
    """Build debias notes from source configs."""
    lines = []
    for source in config.sources:
        if source.debias_instructions:
            lines.append(f"- {source.name}: {source.debias_instructions}")
    return "\n".join(lines) if lines else "No specific debias notes."


def _load_items(run_path: Path) -> list[TrackedItem]:
    """Load items, preferring tracked_items.json, falling back to categorized_items.json."""
    tracked_path = run_path / "tracked_items.json"
    if tracked_path.exists():
        raw = json.loads(tracked_path.read_text())
        return [TrackedItem(**item) for item in raw]

    cat_path = run_path / "categorized_items.json"
    raw = json.loads(cat_path.read_text())
    return [TrackedItem(**CategorizedItem(**item).model_dump()) for item in raw]


def run_summarize(
    run_dir: str,
    config: PipelineConfig,
    llm_client: AuditedLLMClient,
    http_client: AuditedHTTPClient,
) -> dict:
    run_path = Path(run_dir)
    all_items = _load_items(run_path)

    included = [item for item in all_items if item.included]
    if not included:
        logger.warning("No included items to summarize")
        report = "No significant developments reported today.\n"
        (run_path / "report.md").write_text(report)
        return {"items_summarized": 0}

    # Group by category, order by item count descending
    categories: dict[str, list[TrackedItem]] = {}
    for item in included:
        cat = item.primary_category
        categories.setdefault(cat, []).append(item)
    sorted_cats = sorted(categories.items(), key=lambda x: len(x[1]), reverse=True)

    # Build items text for prompt, including tracking data
    items_text = ""
    for cat_name, cat_items in sorted_cats:
        items_text += f"\n## {cat_name}\n"
        for item in cat_items:
            sole = " [SOLE SOURCE]" if item.sole_source_flag else ""
            status_tag = f' story_status="{item.story_status}"'
            items_text += (
                f'<item fetch_id="{item.fetch_id}" source="{item.source}"{sole}{status_tag}>\n'
                f"Title: {item.title_en}\n"
                f"Text: {item.text_en[:500]}\n"
                f"URL: {item.source_url}\n"
                f"Related sources: {', '.join(item.related_sources) if item.related_sources else 'none'}\n"
            )
            if item.story_status == "development":
                if item.development_note:
                    items_text += f"Development note: {item.development_note}\n"
                if item.story_timeline:
                    items_text += "Timeline:\n"
                    for entry in item.story_timeline:
                        items_text += f"  - {entry['date']}: {entry['summary']}\n"
            items_text += f"</item>\n"

    # Detect sources down from run meta or fetch stage
    sources_down = []
    meta_path = run_path / "run_meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        sources_down = meta.get("sources_down", [])
        fetch_stage = meta.get("stages", {}).get("fetch", {})
        if fetch_stage.get("sources_down"):
            sources_down = fetch_stage["sources_down"]

    # Load prompt
    template = load_prompt("summarize", config.paths.get("prompts_dir", "prompts"))
    debias_notes = _build_debias_notes(config)
    min_bullets = config.pipeline.get("min_report_bullets", 3)
    max_bullets = config.pipeline.get("max_report_bullets", 15)

    system, user_msg, version = template.render(
        debias_notes=debias_notes,
        items=items_text,
        min_bullets=str(min_bullets),
        max_bullets=str(max_bullets),
        sources_down=json.dumps(sources_down),
    )

    model = config.models.get("default", "")

    # Critical stage: let LLM failures propagate so run_meta.json records
    # the failure and the CI run goes red, instead of silently publishing
    # a raw bullet fallback.
    report_body = llm_client.call(
        stage="summarize",
        prompt_name="summarize",
        prompt_version=version,
        system=system,
        user_message=user_msg,
        model=model,
        max_tokens=8192,
    )

    # Write draft report (without frontmatter for editorial stage)
    (run_path / "report.md").write_text(report_body)

    # Write plain-text headlines for terminal output
    _write_headlines(run_path, sorted_cats)

    logger.info(f"Summarized {len(included)} items into report")
    return {"items_summarized": len(included), "categories_used": len(sorted_cats)}


def _write_headlines(run_path: Path, sorted_cats: list) -> None:
    """Write a plain-text headlines file for terminal display."""
    lines = []
    for cat_name, items in sorted_cats:
        lines.append(f"\n{cat_name}")
        for item in items:
            sole = " [SOLE SOURCE]" if item.sole_source_flag else ""
            lines.append(f"  - {item.title_en}{sole} ({item.source})")
    text = "\n".join(lines).strip() + "\n"
    (run_path / "headlines.txt").write_text(text)


def _fallback_report(sorted_cats: list) -> str:
    """Produce a raw bullet list when the LLM is unavailable.

    Kept for manual/debugging use only; the pipeline no longer ships this
    fallback automatically (summarize failures now propagate).
    """
    lines = ["*Note: This report was generated without AI summarization.*\n"]
    for cat_name, items in sorted_cats:
        lines.append(f"\n## {cat_name}\n")
        for item in items:
            lines.append(f"- **{item.title_en}** *({item.source})*\n")
    return "\n".join(lines)
