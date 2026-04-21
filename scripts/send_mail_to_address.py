#!/usr/bin/env python3
"""Send a daily brief (rendered same as the mailer stage) to a SINGLE email
address via Resend's transactional /emails endpoint. Does NOT touch the
audience. Useful for previewing how the email looks to a specific tester
without spamming the whole list.

Usage:
    export RESEND_API_KEY=re_...
    python scripts/send_mail_to_address.py 2026-04-20 fa some-address@example.com

Args:
    date (YYYY-MM-DD): the post date (must exist in docs/_{posts,fa_posts}/)
    lang (en|fa):      which language version of the post to send
    address:           the one recipient
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make sure repo root is on sys.path.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import httpx

from models import load_config
from stages.mailer import (
    _markdown_to_html,
    _plain_text_fallback,
    _split_frontmatter,
    _wrap_email_html,
)


def main() -> int:
    if len(sys.argv) != 4:
        print("Usage: python scripts/send_mail_to_address.py YYYY-MM-DD {en|fa} <email>",
              file=sys.stderr)
        return 1

    date_str, lang, to_addr = sys.argv[1], sys.argv[2], sys.argv[3]
    if lang not in ("en", "fa"):
        print(f"lang must be 'en' or 'fa', got '{lang}'", file=sys.stderr)
        return 1

    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if not api_key:
        print("RESEND_API_KEY not set in environment.", file=sys.stderr)
        return 1

    cfg = load_config()
    mailer_cfg = cfg.mailer or {}
    from_addr = mailer_cfg.get("from_addr")
    site_base_url = mailer_cfg.get("site_base_url", "")
    if lang == "fa":
        site_title = mailer_cfg.get("site_title_fa", mailer_cfg.get("site_title_en", "Daily Brief"))
        post_path = REPO_ROOT / "docs" / "_fa_posts" / f"{date_str}-daily-brief.md"
        labels = mailer_cfg.get("labels_fa") or {
            "generated": "تولید شده در",
            "view_online": "مشاهده آنلاین",
            "unsubscribe": "لغو اشتراک",
        }
    else:
        site_title = mailer_cfg.get("site_title_en", "Daily Brief")
        post_path = REPO_ROOT / "docs" / "_posts" / f"{date_str}-daily-brief.md"
        labels = mailer_cfg.get("labels_en") or {
            "generated": "Generated",
            "view_online": "View online",
            "unsubscribe": "Unsubscribe",
        }

    if not post_path.exists():
        print(f"Post not found: {post_path}", file=sys.stderr)
        return 1
    if not from_addr:
        print("mailer.from_addr not configured in config.yaml", file=sys.stderr)
        return 1

    raw = post_path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(raw)

    from datetime import datetime
    if lang == "fa":
        subject = frontmatter.get("title") or f"USrael War Daily Brief: {date_str}"
        date_display = frontmatter.get("date_fa", date_str)
    else:
        subject = f"USrael War Daily Brief: {date_str}"
        try:
            date_display = datetime.strptime(date_str, "%Y-%m-%d").strftime("%B %d, %Y")
        except ValueError:
            date_display = date_str

    y, m, d = date_str.split("-")
    if lang == "fa":
        canonical = f"{site_base_url.rstrip('/')}/fa/daily-brief/{y}/{m}/{d}/daily-brief.html"
    else:
        canonical = f"{site_base_url.rstrip('/')}/daily-brief/{y}/{m}/{d}/daily-brief.html"

    html = _wrap_email_html(
        body_html=_markdown_to_html(body),
        subject=subject,
        date_display=date_display,
        canonical_url=canonical,
        site_title=site_title,
        lang=lang,
        labels=labels,
    )
    text = _plain_text_fallback(body)

    # Send directly to one recipient via the transactional endpoint.
    payload = {
        "from": from_addr,
        "to": to_addr,
        "subject": subject,
        "html": html,
        "text": text,
    }
    reply_to = mailer_cfg.get(f"reply_to_{lang}") or mailer_cfg.get("reply_to")
    if reply_to:
        payload["reply_to"] = reply_to

    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    if 200 <= resp.status_code < 300:
        print(f"Sent: {resp.json()}")
        return 0
    print(f"Send failed: {resp.status_code} {resp.text}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
