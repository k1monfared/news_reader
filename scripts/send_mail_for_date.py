#!/usr/bin/env python3
"""Send the daily brief email for a given date without running the full
pipeline. Useful for smoke-testing the mailer stage.

Usage:
    export RESEND_API_KEY=re_...
    python scripts/send_mail_for_date.py 2026-04-20

Requires:
    - docs/_posts/YYYY-MM-DD-daily-brief.md exists
    - (optional) docs/_fa_posts/YYYY-MM-DD-daily-brief.md exists
    - config.yaml mailer block is populated
    - RESEND_API_KEY is in the environment

Setting mailer.enabled in config.yaml is NOT required for this script
(we bypass the disabled-check by copying the config and flipping enabled).
"""

from __future__ import annotations

import logging
import os
import sys
from copy import deepcopy
from pathlib import Path

# Ensure repo root is on sys.path so imports work regardless of CWD.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from models import load_config
from stages.mailer import run_mailer


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if len(sys.argv) != 2:
        print("Usage: python scripts/send_mail_for_date.py YYYY-MM-DD", file=sys.stderr)
        return 1

    date = sys.argv[1]
    if not (len(date) == 10 and date[4] == "-" and date[7] == "-"):
        print(f"Invalid date: {date}. Use YYYY-MM-DD.", file=sys.stderr)
        return 1

    if not os.environ.get("RESEND_API_KEY", "").strip():
        print("RESEND_API_KEY not set in environment.", file=sys.stderr)
        return 1

    cfg = load_config()

    # Mirror the live config but force-enable the mailer just for this run.
    # A fresh dict avoids mutating the loaded PipelineConfig.
    cfg_copy = deepcopy(cfg)
    cfg_copy.mailer = {**(cfg_copy.mailer or {}), "enabled": True}

    en_post = REPO_ROOT / "docs" / "_posts" / f"{date}-daily-brief.md"
    fa_post = REPO_ROOT / "docs" / "_fa_posts" / f"{date}-daily-brief.md"
    print(f"English post: {en_post} {'[OK]' if en_post.exists() else '[MISSING]'}")
    print(f"Farsi post:   {fa_post} {'[OK]' if fa_post.exists() else '[MISSING]'}")

    # run_mailer derives the date from the first 10 chars of run_dir basename.
    # Any path whose basename starts with the target date works.
    fake_run_dir = f"data/runs/{date}-000000"

    result = run_mailer(
        run_dir=fake_run_dir,
        config=cfg_copy,
        llm_client=None,  # mailer doesn't use the LLM client
        http_client=None,  # or the HTTP client (uses its own httpx inside)
    )
    print()
    print("Result:", result)
    status = result.get("status", "unknown")
    if status == "sent":
        return 0
    if status == "skipped":
        print("Mailer skipped. Check config.yaml mailer block.", file=sys.stderr)
        return 1
    if status == "partial":
        print("Partial success. See errors above.", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
