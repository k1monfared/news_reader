"""Publish stage: writes the report as a Jekyll post and pushes to master."""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

from models import PipelineConfig
from llm_client import AuditedLLMClient
from audit_logger import AuditedHTTPClient

logger = logging.getLogger(__name__)


def _date_from_run_dir(run_dir: str) -> str:
    """Extract the target date (YYYY-MM-DD) from the run directory name."""
    return Path(run_dir).name[:10]


def _build_frontmatter(config: PipelineConfig, run_dir: str) -> str:
    """Build Jekyll frontmatter for the final post."""
    date_str = _date_from_run_dir(run_dir)
    date_obj = datetime.strptime(date_str, "%Y-%m-%d")

    sources_down = []
    meta_path = Path(run_dir) / "run_meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            sources_down = meta.get("sources_down", [])
        except (json.JSONDecodeError, KeyError):
            pass

    generated_at = datetime.now(timezone(timedelta(hours=-7))).strftime("%Y-%m-%d %H:%M %Z")

    return (
        "---\n"
        "layout: post\n"
        f'title: "Daily Brief: {date_obj.strftime("%B %d, %Y")}"\n'
        f"date: {date_str}\n"
        "categories: [daily-brief]\n"
        f"sources_down: {json.dumps(sources_down)}\n"
        f'generated_at: "{generated_at}"\n'
        "---\n\n"
    )


def _models_used(run_dir: str, config: PipelineConfig) -> list[str]:
    """Ordered unique model names actually called during this run.

    Reads the run's ``audit/llm_calls.jsonl``. Falls back to the config
    default when the audit file is missing or empty (e.g. a fully fallback
    run that made no LLM calls).
    """
    models: list[str] = []
    calls_file = Path(run_dir) / "audit" / "llm_calls.jsonl"
    if calls_file.exists():
        for line in calls_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            model = entry.get("model")
            if model and model not in models:
                models.append(model)
    if not models:
        models = [config.models.get("default", "deepseek-v4-flash-free")]
    return models


def build_model_footer(run_dir: str, config: PipelineConfig) -> str:
    """Footer block recording when and with which model(s) the report was made."""
    models = ", ".join(_models_used(run_dir, config))
    date_str = _date_from_run_dir(run_dir)
    return f"\n---\n\n*Generated on {date_str} using {models}*\n"


def run_publish(
    run_dir: str,
    config: PipelineConfig,
    llm_client: AuditedLLMClient,
    http_client: AuditedHTTPClient,
) -> dict:
    run_path = Path(run_dir)
    site_dir = Path(config.publish.get("site_dir", "docs"))

    # Find the best available report
    report_path = run_path / "report_verified.md"
    if not report_path.exists():
        report_path = run_path / "report_edited.md"
    if not report_path.exists():
        report_path = run_path / "report.md"
    if not report_path.exists():
        logger.error("No report found to publish")
        return {"status": "failed", "reason": "no_report"}

    report_content = report_path.read_text()
    frontmatter = _build_frontmatter(config, run_dir)
    footer = build_model_footer(run_dir, config)

    # Determine post filename from run_id target date
    date_str = _date_from_run_dir(run_dir)
    post_filename = f"{date_str}-daily-brief.md"
    posts_dir = site_dir / "_posts"
    posts_dir.mkdir(parents=True, exist_ok=True)
    post_path = posts_dir / post_filename

    # Write the post with frontmatter and model footer
    post_path.write_text(frontmatter + report_content + footer)
    logger.info(f"Wrote post to {post_path}")

    # When Farsi translation is enabled, defer git to the translate_fa stage
    # so English and Farsi ship atomically in a single commit.
    tfa = getattr(config, "translate_fa", {}) or {}
    if tfa.get("enabled", False):
        logger.info("translate_fa enabled; deferring git commit to translate_fa stage.")
        return {"status": "staged", "post": str(post_path)}

    # Git: commit the new post and push master
    try:
        _git_publish(date_str)
        return {"status": "published", "post": str(post_path)}
    except Exception as e:
        logger.error(f"Git publish failed: {e}. Retrying once...")
        try:
            _git_publish(date_str)
            return {"status": "published_retry", "post": str(post_path)}
        except Exception as e2:
            logger.error(f"Git publish retry failed: {e2}. Post is on disk.")
            return {"status": "failed_push", "post": str(post_path), "error": str(e2)}


def _git_publish(date_str: str) -> None:
    """Add, commit, and push from the repo root."""
    def run_git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git"] + list(args),
            capture_output=True,
            text=True,
            timeout=60,
        )

    run_git("add", "docs/_posts/", "docs/_data/")

    result = run_git("diff", "--cached", "--quiet")
    if result.returncode == 0:
        logger.info("No changes to commit")
        return

    run_git("commit", "-m", f"Daily brief: {date_str}")
    run_git("push", "origin", "master")
