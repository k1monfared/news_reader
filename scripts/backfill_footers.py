"""Backfill: migrate the model footer into frontmatter for every published post.

The ``*Generated on <date> using <model>*`` body footer was stamped into the
end of every English post in ``docs/_posts`` and every Farsi post in
``docs/_fa_posts``. This script moves the attribution out of the body and into
the YAML frontmatter ``models_used`` field, where the Jekyll ``post.html``
layout renders it inside the real ``post-footer`` element.

For each post it:
    1. Removes any ``*Model: ...*`` or ``*Generated on ... using ...*`` body
       footer (legacy or current format).
    2. Records ``models_used`` in frontmatter, preferring (in priority order):
         a. The model(s) already listed in the post's body footer.
         b. The actual model(s) from the matching local run's
            ``audit/llm_calls.jsonl`` when a local run dir exists.
         c. Otherwise the date-based fallback: posts before the OpenCode Zen
            migration (2026-08-06) are stamped ``claude-sonnet-4-5`` (matches
            all local audit logs); posts on or after that date are stamped
            ``deepseek-v4-flash-free``.
    3. Posts that already carry ``models_used`` in frontmatter and no body
       footer are left untouched (idempotent).

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

# Matches the body footer (old or current format) at end of file.
FOOTER_RE = re.compile(r"\n---\n\n\*(?:Model: ([^*]*)|Generated on [^*]* using ([^*]*))\*\s*$")

# Normalized (non-normalized path like docs/_posts) post filename pattern.
POST_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-daily-brief\.md$")


def _frontmatter_part(markdown: str) -> tuple[str, str]:
    """Return (frontmatter_text_without_delimiters, rest_of_file)."""
    if not markdown.startswith("---"):
        return "", markdown
    end = markdown.find("\n---", 3)
    if end == -1:
        return "", markdown
    return markdown[3:end].strip(), markdown[end:]


def _frontmatter_models(frontmatter: str) -> list[str] | None:
    """Return models_used from frontmatter text, or None if absent/invalid."""
    for line in frontmatter.splitlines():
        if not line.startswith("models_used:"):
            continue
        raw = line.partition(":")[2].strip()
        try:
            models = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if isinstance(models, list) and models:
            return models
        return None
    return None


def _set_frontmatter_models(frontmatter: str, models: list[str]) -> str:
    """Return frontmatter text with models_used set (replacing any existing)."""
    lines = [
        line
        for line in frontmatter.splitlines()
        if not line.startswith("models_used:")
    ]
    lines.append(f"models_used: {json.dumps(models)}")
    return "\n".join(lines)


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
    """Resolve the model list for a date: run audit first, date fallback second."""
    models = models_from_run(date_str)
    if models is None:
        models = model_fallback(date_str)
    return models


def backfill_posts(dry_run: bool) -> tuple[int, int]:
    """Migrate body footers into frontmatter. Returns (changed, unchanged)."""
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
            content = post_path.read_text(encoding="utf-8")

            # Strip the body footer (any format) off the end of the file first,
            # then split frontmatter from the clean body.
            body_no_footer = FOOTER_RE.sub("", content).rstrip() + "\n"
            fm, rest = _frontmatter_part(body_no_footer)
            existing_models = _frontmatter_models(fm) if fm else None

            # Model attribution: body footer first, then run audit, then fallback.
            footer_match = FOOTER_RE.search(content)
            if footer_match:
                raw_models = footer_match.group(1) or footer_match.group(2) or ""
                models = [m.strip() for m in raw_models.split(",") if m.strip()]
            elif existing_models:
                models = existing_models
            else:
                models = models_for_date(date_str)

            if fm:
                new_fm = _set_frontmatter_models(fm, models)
                # Rebuild: "---" + frontmatter + "\n---\n\n" + body
                updated = "---\n" + new_fm + rest
            else:
                updated = body_no_footer

            if updated == content:
                unchanged += 1
                continue

            changed += 1
            if dry_run:
                logger.info(
                    f"[dry-run] would update {post_path.name}: {', '.join(models)}"
                )
            else:
                post_path.write_text(updated, encoding="utf-8")
                logger.info(f"Updated {post_path.name}: {', '.join(models)}")
    return changed, unchanged


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate the model footer from post bodies into frontmatter."
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
