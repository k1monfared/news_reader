"""Repair degraded published posts by re-running editorial over their own
content, then republishing and regenerating the Farsi mirror.

For dates whose original run produced only bare headlines (LLM outage
fallbacks), this rebuilds the post body in the site's standard format using
the repair_editorial prompt. No new facts are introduced: the LLM sees only
what the published post already contains. Link verification still runs (it
passes through when no URLs exist). English and Farsi deploys stay decoupled:
publish commits/pushes "(en)", translate_fa commits/pushes "(fa)".

Usage:
    python scripts/repair_post.py --dates 2026-08-05 2026-08-09 2026-08-11

The mailer stage is intentionally not invoked: repaired dates are never
today, and broadcasts for backfills are forbidden.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import load_config
from run_pipeline import create_run_dir, get_timezone_offset
from stages.editorial import run_editorial
from stages.publish import run_publish
from stages.translate_fa import _split_frontmatter, run_translate_fa
from stages.verify import run_verify
from llm_client import AuditedLLMClient
from audit_logger import AuditedHTTPClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("repair")

NOTE_PREFIXES = ("*Note:", "> *Note:")


def _strip_note(body: str) -> str:
    """Drop the 'generated without AI summarization' disclaimer lines."""
    lines = [
        line for line in body.splitlines()
        if not any(line.strip().startswith(p) for p in NOTE_PREFIXES)
    ]
    return "\n".join(lines).strip() + "\n"


def repair_date(date_str: str, config, prompt_name: str = "repair_editorial") -> dict:
    """Rebuild one date's post from its own published content.

    ``prompt_name`` selects the editorial template: "repair_editorial"
    rebuilds bare headlines into the site format (no links exist), while
    the default-name "editorial" polishes a full-format draft that kept
    its Sources links.
    """
    site_dir = Path(config.publish.get("site_dir", "docs"))
    post_path = site_dir / "_posts" / f"{date_str}-daily-brief.md"
    if not post_path.exists():
        return {"date": date_str, "status": "skipped", "reason": "no_post"}

    raw = post_path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(raw)

    # Preserve historical accuracy in the regenerated frontmatter: carry
    # over the original run's sources_down rather than reporting today's.
    sources_down: list[str] = []
    if "sources_down" in frontmatter:
        try:
            sources_down = json.loads(frontmatter["sources_down"])
        except json.JSONDecodeError:
            sources_down = []

    tz = get_timezone_offset(config.schedule.get("timezone", "America/Vancouver"))
    now = datetime.now(tz)
    data_dir = config.paths.get("data_dir", "data")
    run_id = f"{date_str}-{now.strftime('%H%M%S')}"
    run_dir = create_run_dir(data_dir, run_id)

    # Seed meta so publish's frontmatter keeps the original sources_down.
    (run_dir / "run_meta.json").write_text(json.dumps({"sources_down": sources_down}))

    draft = _strip_note(body)
    (run_dir / "report.md").write_text(draft, encoding="utf-8")
    logger.info(f"[{date_str}] Repair run {run_id}: drafting from {len(draft)} chars")

    llm_client = AuditedLLMClient(str(run_dir), config.budget, config.models)
    http_client = AuditedHTTPClient(str(run_dir))
    results: dict = {"date": date_str, "run_id": run_id}
    try:
        results["editorial"] = run_editorial(
            str(run_dir), config, llm_client, http_client,
            prompt_name=prompt_name,
        )
        results["verify"] = run_verify(str(run_dir), config, llm_client, http_client)
        results["publish"] = run_publish(str(run_dir), config, llm_client, http_client)
        results["translate_fa"] = run_translate_fa(
            str(run_dir), config, llm_client, http_client
        )
    except Exception as e:
        logger.error(f"[{date_str}] Repair failed: {e}", exc_info=True)
        results["error"] = str(e)
    finally:
        http_client.close()

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dates", nargs="+", required=True,
        help="Post dates to repair (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--stock-editorial", nargs="*", default=[],
        help="Dates whose drafts are full-format with links: polish them "
             "with the standard editorial prompt instead of rebuilding "
             "via repair_editorial",
    )
    args = parser.parse_args()

    config = load_config()
    failures = []
    for date_str in args.dates:
        prompt = "editorial" if date_str in args.stock_editorial else "repair_editorial"
        result = repair_date(date_str, config, prompt_name=prompt)
        print(json.dumps(result, indent=2))
        if result.get("publish", {}).get("status") == "failed_push" or result.get("error"):
            failures.append(date_str)

    if failures:
        logger.error(f"Repairs with failures: {failures}")
        sys.exit(1)


if __name__ == "__main__":
    main()
