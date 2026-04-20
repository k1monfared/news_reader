"""Command-line wrapper for send_broadcast. Usage:

  python -m resend_broadcast \
      --audience-id AUD_ID \
      --from "Brief <brief@example.com>" \
      --subject "Daily Brief: April 16, 2026" \
      --html-file out.html \
      [--reply-to you@example.com] \
      [--text-file out.txt] \
      [--name "internal label"]

Reads RESEND_API_KEY from the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .client import BroadcastError, send_broadcast


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="resend_broadcast",
        description="Create and send a Resend broadcast from an HTML file.",
    )
    p.add_argument("--audience-id", required=True, help="Resend audience UUID.")
    p.add_argument("--from", dest="from_addr", required=True,
                   help='Sender, e.g. "Brief <brief@example.com>".')
    p.add_argument("--subject", required=True, help="Email subject line.")
    p.add_argument("--html-file", required=True, type=Path,
                   help="Path to the HTML body file.")
    p.add_argument("--text-file", type=Path, default=None,
                   help="Optional path to a plain-text alt body.")
    p.add_argument("--reply-to", default=None, help="Optional Reply-To address.")
    p.add_argument("--name", default=None,
                   help="Optional internal broadcast name (visible in dashboard only).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if not api_key:
        print("RESEND_API_KEY not set", file=sys.stderr)
        return 2

    html = args.html_file.read_text(encoding="utf-8")
    text = args.text_file.read_text(encoding="utf-8") if args.text_file else None

    try:
        result = send_broadcast(
            api_key=api_key,
            audience_id=args.audience_id,
            from_addr=args.from_addr,
            subject=args.subject,
            html=html,
            reply_to=args.reply_to,
            text=text,
            name=args.name,
        )
    except BroadcastError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1

    print(json.dumps(result))
    return 0
