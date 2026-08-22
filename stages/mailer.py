"""Mailer stage: after publish + translate_fa, send the day's post(s) as
email broadcasts to subscribers via Resend.

This stage is project-specific glue. The reusable primitives come from the
`newsletter_base` library (pinned in requirements.txt). This module keeps
only the Jekyll-specific logic: frontmatter parsing, bilingual post path
resolution, Shamsi date formatting, and the project's custom email shell.

Runs last in the pipeline. Failures do not unpublish: the posts are already
on disk and pushed. A mail failure is logged and surfaced in run_meta.

Emails are sent ONLY when the brief's date equals today's date (in the
configured schedule timezone). Backfill runs and re-runs of old dates are
never emailed, as a hard rule enforced here regardless of how the stage is
invoked.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from models import PipelineConfig
from llm_client import AuditedLLMClient
from audit_logger import AuditedHTTPClient
from run_pipeline import get_timezone_offset

# Installed via `newsletter-base` dep in requirements.txt.
from newsletter.render import (
    _flatten_details,
    _md_to_html as _markdown_to_html,
    _text_fallback_from_markdown as _plain_text_fallback,
)
from resend_broadcast import BroadcastError, send_broadcast

logger = logging.getLogger(__name__)


EMAIL_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
       line-height: 1.55; color: #1a1a1a; max-width: 640px; margin: 0 auto; padding: 16px; }
h1 { font-size: 20px; margin: 24px 0 8px; }
h2 { font-size: 17px; margin: 28px 0 8px; padding-bottom: 4px; border-bottom: 1px solid #ddd; }
h3 { font-size: 15px; margin: 20px 0 6px; }
p { margin: 8px 0; }
a { color: #0b5394; text-decoration: underline; }
blockquote { border-left: 3px solid #ccc; margin: 12px 0; padding: 4px 12px; color: #444; }
.email-header { color: #666; font-size: 13px; margin-bottom: 16px; }
.email-footer { margin-top: 32px; padding-top: 16px; border-top: 1px solid #ddd;
                font-size: 12px; color: #666; }
.email-footer a { color: #666; }
"""


# ---------------------------------------------------------------------------
# Post parsing
# ---------------------------------------------------------------------------


def _split_frontmatter(markdown: str) -> tuple[dict, str]:
    if not markdown.startswith("---"):
        return {}, markdown
    end = markdown.find("\n---", 3)
    if end == -1:
        return {}, markdown
    raw_fm = markdown[3:end].strip()
    body = markdown[end + 4:].lstrip("\n")
    fm: dict = {}
    for line in raw_fm.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip().strip('"')
    return fm, body


# ---------------------------------------------------------------------------
# Email envelope
# ---------------------------------------------------------------------------


def _wrap_email_html(
    *,
    body_html: str,
    subject: str,
    date_display: str,
    canonical_url: str,
    site_title: str,
    lang: str,
    labels: dict,
    sponsor_url: str | None = None,
) -> str:
    direction = "rtl" if lang == "fa" else "ltr"
    generated_label = labels.get("generated", "Generated")
    view_online_label = labels.get("view_online", "View online")
    unsubscribe_label = labels.get("unsubscribe", "Unsubscribe")
    sponsor_label = labels.get("sponsor", "Sponsor")
    header = (
        f'<div class="email-header">'
        f'<strong>{site_title}</strong> &middot; {date_display}'
        f"</div>"
    )
    sponsor_link = (
        f'<a href="{sponsor_url}">{sponsor_label}</a> &middot; ' if sponsor_url else ""
    )
    footer = (
        f'<div class="email-footer">'
        f'<p>{generated_label}: {datetime.utcnow().strftime("%Y-%m-%d %H:%M")} UTC</p>'
        f'<p><a href="{canonical_url}">{view_online_label}</a> &middot; '
        f'{sponsor_link}'
        f'<a href="{{{{{{RESEND_UNSUBSCRIBE_URL}}}}}}">{unsubscribe_label}</a></p>'
        f"</div>"
    )
    return (
        f'<!doctype html><html lang="{lang}" dir="{direction}"><head>'
        f'<meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{subject}</title>'
        f'<style>{EMAIL_CSS}</style>'
        f'</head><body>'
        f'{header}'
        f'<article>{body_html}</article>'
        f'{footer}'
        f'</body></html>'
    )


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------


def _send_for_language(
    *,
    post_path: Path,
    audience_id: str,
    lang: str,
    mailer_cfg: dict,
    date_str: str,
    site_base_url: str,
    site_title: str,
    labels: dict,
) -> dict:
    raw = post_path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(raw)

    if lang == "fa":
        subject = frontmatter.get("title") or f"USrael War Daily Brief: {date_str}"
        date_display = frontmatter.get("date_fa", date_str)
    else:
        subject = f"USrael War Daily Brief: {date_str}"
        try:
            date_display = datetime.strptime(date_str, "%Y-%m-%d").strftime("%B %d, %Y")
        except ValueError:
            date_display = date_str

    body_html = _markdown_to_html(body)

    # Canonical URL: Jekyll permalink for the post. Posts sit at
    # /news_reader/daily-brief/YYYY/MM/DD/daily-brief.html for English,
    # and /fa/daily-brief/YYYY/MM/DD/daily-brief.html for Farsi.
    y, m, d = date_str.split("-")
    if lang == "fa":
        canonical = f"{site_base_url.rstrip('/')}/fa/daily-brief/{y}/{m}/{d}/daily-brief.html"
    else:
        canonical = f"{site_base_url.rstrip('/')}/daily-brief/{y}/{m}/{d}/daily-brief.html"

    html = _wrap_email_html(
        body_html=body_html,
        subject=subject,
        date_display=date_display,
        canonical_url=canonical,
        site_title=site_title,
        lang=lang,
        labels=labels,
        sponsor_url=mailer_cfg.get("sponsor_url"),
    )
    text = _plain_text_fallback(body)

    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("RESEND_API_KEY not set in environment")

    reply_to = mailer_cfg.get(f"reply_to_{lang}") or mailer_cfg.get("reply_to")
    result = send_broadcast(
        api_key=api_key,
        audience_id=audience_id,
        from_addr=mailer_cfg["from_addr"],
        subject=subject,
        html=html,
        text=text,
        reply_to=reply_to,
        name=f"daily-brief-{lang}-{date_str}",
    )
    logger.info(
        f"Broadcast sent ({lang}): broadcast_id={result.get('broadcast_id')} "
        f"audience={audience_id}"
    )
    return {"lang": lang, **result}


def run_mailer(
    run_dir: str,
    config: PipelineConfig,
    llm_client: AuditedLLMClient,
    http_client: AuditedHTTPClient,
) -> dict:
    mailer_cfg = getattr(config, "mailer", None) or {}
    if not mailer_cfg.get("enabled", False):
        logger.info("mailer disabled; skipping.")
        return {"status": "skipped"}

    required = ("from_addr", "site_base_url")
    missing = [k for k in required if not mailer_cfg.get(k)]
    if missing:
        logger.error(f"mailer config missing required keys: {missing}")
        return {"status": "failed", "reason": f"missing_config:{','.join(missing)}"}

    site_dir = Path(config.publish.get("site_dir", "docs"))
    date_str = Path(run_dir).name[:10]

    # Hard rule: never email backfills or re-runs of old dates. Broadcasts
    # go out only when the brief being processed is dated today.
    tz = get_timezone_offset(config.schedule.get("timezone", "America/Vancouver"))
    today = datetime.now(tz).strftime("%Y-%m-%d")
    if date_str != today:
        logger.info(
            f"Brief date {date_str} != today ({today}); skipping email "
            f"broadcasts. Emails are never sent for backfills."
        )
        return {
            "status": "skipped",
            "reason": "not_today",
            "brief_date": date_str,
        }

    en_post = site_dir / "_posts" / f"{date_str}-daily-brief.md"
    fa_post = site_dir / "_fa_posts" / f"{date_str}-daily-brief.md"

    site_base_url = mailer_cfg["site_base_url"]
    en_site_title = mailer_cfg.get("site_title_en", "Daily Brief")
    fa_site_title = mailer_cfg.get("site_title_fa", en_site_title)
    en_labels = mailer_cfg.get("labels_en") or {
        "generated": "Generated",
        "view_online": "View online",
        "unsubscribe": "Unsubscribe",
        "sponsor": "Sponsor",
    }
    fa_labels = mailer_cfg.get("labels_fa") or {
        "generated": "تولید شده در",
        "view_online": "مشاهده آنلاین",
        "unsubscribe": "لغو اشتراک",
        "sponsor": "حمایت مالی",
    }

    results: list[dict] = []
    errors: list[str] = []

    en_audience = mailer_cfg.get("audience_id_en")
    if en_post.exists() and en_audience:
        try:
            results.append(_send_for_language(
                post_path=en_post,
                audience_id=en_audience,
                lang="en",
                mailer_cfg=mailer_cfg,
                date_str=date_str,
                site_base_url=site_base_url,
                site_title=en_site_title,
                labels=en_labels,
            ))
        except (BroadcastError, RuntimeError) as e:
            logger.error(f"English broadcast failed: {e}")
            errors.append(f"en: {e}")
    elif not en_audience:
        logger.info("No audience_id_en configured; skipping English broadcast.")
    else:
        logger.warning(f"English post not found at {en_post}; skipping.")

    fa_audience = mailer_cfg.get("audience_id_fa")
    if fa_post.exists() and fa_audience:
        try:
            results.append(_send_for_language(
                post_path=fa_post,
                audience_id=fa_audience,
                lang="fa",
                mailer_cfg=mailer_cfg,
                date_str=date_str,
                site_base_url=site_base_url,
                site_title=fa_site_title,
                labels=fa_labels,
            ))
        except (BroadcastError, RuntimeError) as e:
            logger.error(f"Farsi broadcast failed: {e}")
            errors.append(f"fa: {e}")
    elif not fa_audience:
        logger.info("No audience_id_fa configured; skipping Farsi broadcast.")

    if errors and not results:
        return {"status": "failed", "errors": errors}
    if errors:
        return {"status": "partial", "results": results, "errors": errors}
    return {"status": "sent", "results": results}
