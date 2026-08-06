"""One-time backfill: stamp every published daily brief with the model footer.

Adds the ``*Generated on <date> using <model>*`` footer to every English post
in ``docs/_posts`` and every Farsi post in ``docs/_fa_posts`` that does not
already carry the current footer format.

Model attribution (in priority order):
    1. The actual model(s) from the matching local run's
       ``audit/llm_calls.jsonl`` when a local run dir exists for that date.
    2. Otherwise the date-based fallback: posts before the OpenCode Zen
       migration (2026-08-06) are stamped ``claude-sonnet-4-5`` (matches
       all local audit logs); posts on or after that date are stamped
       ``deepseek-v4-flash-free``.

Usage:
    python scripts/backfill_footers.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

# Allow running from either the project root or the scripts/ directory.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backfill_footers")

MIGRATION_DATE = "2026-08-06"
POSTS_DIRS = [
    ROOT / "docs" / "_posts",
    ROOT / "docs" / "_fa_posts",
]
RUNS_DIR = ROOT / "data" / "runs"

# Matches either the old footer format or the current one at end of file.
FOOTER_RE = re.compile(r"\n---\n\n\*(?:Model: [^*]*|Generated on [^*]*)\*\s*$")

# Normalized (non-normalized path like docs/_posts) post filename pattern.
POST_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-daily-brief\.md$")


def models_from_run(date_str: str) -> list[str] | None:
    """Return ordered unique models from the latest local run for a date.

    Returns None when no local run dir (or audit log) exists for the date.
    """
    runs = (
        sorted(p.name for p in RUNS_DIR.glob(f"{date_str}-*"))
        if RUNS_DIR.exists()
        else []
    )
    for run_name in reversed(runs):
        calls_file = RUNS_DIR / run_name / "audit" / "llm_calls.jsonl"
        if not calls_file.exists():
            continue
        models: list[str] = []
        for line in calls_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                model = json.loads(line).get("model")
            except json.JSONDecodeError:
                continue
            if model and model not in models:
                models.append(model)
        if models:
            return models
    return None


def model_fallback(date_str: str) -> list[str]:
    """Date-based model fallback for dates without a local run dir."""
    if date_str >= MIGRATION_DATE:
        return ["deepseek-v4-flash-free"]
    return ["claude-sonnet-4-5"]


def models_for_date(date_str: str) -> list[str]:
    """Resolve the model list for a date: audit first, date fallback second."""
    models = models_from_run(date_str)
    if models is None:
        models = model_fallback(date_str)
    return models


def backfill_posts(dry_run: bool) -> tuple[int, int]:
    """Stamp every post with the model footer. Returns (changed, unchanged)."""
    changed = 0
    unchanged = 0
    for posts_dir in POSTS_DIRS:
        if not posts_dir.exists():
            logger.info(f"Skipping missing directory: {posts_dir}")
            continue
        for post_path in sorted(posts_dir.glob("*-daily-brief.md")):
            match = POST_RE.match(post_path.name)
            if not match:
                continue
            date_str = match.group(1)
            models = models_for_date(date_str)
            model_list = ", ".join(models)
            footer = f"\n---\n\n*Generated on {date_str} using {model_list}*\n"

            content = post_path.read_text(encoding="utf-8")
            body = FOOTER_RE.sub("", content).rstrip() + "\n"
            updated = body + footer

            if updated == content:
                unchanged += 1
                continue

            changed += 1
            if dry_run:
                logger.info(
                    f"[dry-run] would update {post_path.name}: {model_list}"
                )
            else:
                post_path.write_text(updated, encoding="utf-8")
                logger.info(f"Updated {post_path.name}: {model_list}")
    return changed, unchanged


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stamp every post with the model footer."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing files.",
    )
    args = parser.parse_args()

    changed, unchanged = backfill_posts(args.dry_run)
    action = "would update" if args.dry_run else "updated"
    logger.info(f"{action} {changed} post(s); {unchanged} already up to date.")


if __name__ == "__main__":
    main()
