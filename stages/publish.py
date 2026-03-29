"""Publish stage: deploys the report to GitHub Pages via git."""

from __future__ import annotations

import json
import logging
import subprocess
import shutil
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

    return (
        "---\n"
        "layout: post\n"
        f'title: "Iran Conflict Brief -- {date_obj.strftime("%B %d, %Y")}"\n'
        f"date: {date_str}\n"
        "categories: [daily-brief]\n"
        f"sources_down: {json.dumps(sources_down)}\n"
        "---\n\n"
    )


def run_publish(
    run_dir: str,
    config: PipelineConfig,
    llm_client: AuditedLLMClient,
    http_client: AuditedHTTPClient,
) -> dict:
    run_path = Path(run_dir)
    site_dir = Path(config.publish.get("site_dir", "site"))
    branch = config.publish.get("branch", "gh-pages")

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

    # Determine post filename from run_id target date
    date_str = _date_from_run_dir(run_dir)
    post_filename = f"{date_str}-daily-brief.md"
    posts_dir = site_dir / "_posts"
    posts_dir.mkdir(parents=True, exist_ok=True)
    post_path = posts_dir / post_filename

    # Write the post with frontmatter
    post_path.write_text(frontmatter + report_content)
    logger.info(f"Wrote post to {post_path}")

    # Git operations
    try:
        _git_publish(site_dir, branch, date_str)
        return {"status": "published", "post": str(post_path)}
    except Exception as e:
        logger.error(f"Git publish failed: {e}. Retrying once...")
        try:
            _git_publish(site_dir, branch, date_str)
            return {"status": "published_retry", "post": str(post_path)}
        except Exception as e2:
            logger.error(f"Git publish retry failed: {e2}. Post is on disk.")
            return {"status": "failed_push", "post": str(post_path), "error": str(e2)}


def _git_publish(site_dir: Path, branch: str, date_str: str) -> None:
    """Add, commit, and push the site directory."""
    def run_git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git"] + list(args),
            cwd=str(site_dir),
            capture_output=True,
            text=True,
            timeout=60,
        )

    # Check if we're in a git repo
    result = run_git("rev-parse", "--git-dir")
    if result.returncode != 0:
        # Initialize git in the site directory
        run_git("init")
        run_git("checkout", "-b", branch)

    run_git("add", "-A")

    result = run_git("diff", "--cached", "--quiet")
    if result.returncode == 0:
        logger.info("No changes to commit")
        return

    run_git("commit", "-m", f"Daily brief: {date_str}")
    run_git("push", "-u", "origin", branch)
