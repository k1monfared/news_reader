"""Regenerate the Farsi edition for a date from its existing English post.

Use when the English brief shipped but the Farsi edition is missing (for
example after a translate_fa stage failure). It does not re-run the English
pipeline and never sends email; it only writes and pushes the Farsi
artifacts (``docs/_fa_posts/`` and the Farsi bias mirror).

Usage:
  python scripts/translate_fa_for_date.py --date 2026-10-03
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import RunMeta, load_config
from llm_client import AuditedLLMClient
from audit_logger import AuditedHTTPClient
from run_pipeline import create_run_dir
from stages.translate_fa import run_translate_fa

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("translate_fa_date")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date", required=True, help="YYYY-MM-DD of the existing English post"
    )
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args()

    config = load_config()
    if not (config.translate_fa or {}).get("enabled", False):
        logger.error("translate_fa is disabled in config; nothing to do.")
        return 1

    date_str = args.date
    if len(date_str) != 10 or date_str[4] != "-" or date_str[7] != "-":
        logger.error(f"Invalid --date {date_str!r}; expected YYYY-MM-DD")
        return 2

    site_dir = Path(config.publish.get("site_dir", "docs"))
    en_post = site_dir / "_posts" / f"{date_str}-daily-brief.md"
    if not en_post.exists():
        logger.error(f"English post not found at {en_post}; cannot translate.")
        return 1

    data_dir = args.data_dir or config.paths.get("data_dir", "data")
    now = datetime.now(timezone.utc)
    run_id = f"{date_str}-{now.strftime('%H%M%S')}"
    run_dir = create_run_dir(data_dir, run_id)
    logger.info(f"Farsi-only run: {run_id}")

    meta = RunMeta(run_id=run_id, started_at=now.isoformat(), backfill=True)
    llm_client = AuditedLLMClient(str(run_dir), config.budget, config.models)
    http_client = AuditedHTTPClient(str(run_dir))
    result: dict = {}
    try:
        result = run_translate_fa(str(run_dir), config, llm_client, http_client)
    except Exception as exc:  # noqa: BLE001 - record and exit non-zero
        logger.error(f"translate_fa failed for {date_str}: {exc}", exc_info=True)
        result = {"status": "failed", "error": str(exc)}
    finally:
        http_client.close()
        meta.finished_at = datetime.now(timezone.utc).isoformat()
        meta.total_cost_usd = llm_client.total_cost
        meta.prompt_versions = llm_client.prompt_versions_used
        meta.stages["translate_fa"] = result if isinstance(result, dict) else {}
        if isinstance(result, dict) and result.get("status") == "failed":
            meta.errors.append(
                f"translate_fa: {result.get('error', result.get('reason', 'failed'))}"
            )
        (run_dir / "run_meta.json").write_text(
            json.dumps(meta.model_dump(), indent=2), encoding="utf-8"
        )

    status = result.get("status") if isinstance(result, dict) else None
    if status in ("published", "skipped"):
        logger.info(f"Farsi edition {status} for {date_str}.")
        return 0
    logger.error(f"Farsi edition failed for {date_str}: {result}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
