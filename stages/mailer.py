"""Mailer stage: after publish + translate_fa, send the day's post(s) as
email broadcasts to subscribers via Resend.

This stage is project-specific glue. The reusable parts live in
`resend_broadcast/` (HTTP wrapper) and `subscribe-proxy/` (Worker that
accepts subscribe form submissions). Everything here is about converting
a Jekyll post into email-safe HTML and handing it off.

Runs last in the pipeline. Failures do not unpublish: the posts are already
on disk and pushed. A mail failure is logged and surfaced in run_meta.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path

import markdown as md_lib

from models import PipelineConfig
from llm_client import AuditedLLMClient
from audit_logger import AuditedHTTPClient
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


def _flatten_details(markdown_body: str) -> str:
    """Strip <details>/<summary> wrappers, keeping their content as plain
    markdown. Gmail and most other clients strip <details>/<summary> anyway,
    so we want the content to flow as normal paragraphs. The summary line
    in these posts is already **bold**, so no extra formatting is needed.
    """
    pattern = re.compile(
        r'<details[^>]*>\s*<summary[^>]*>(.*?)</summary>\s*(.*?)\s*</details>',
        re.DOTALL,
    )

    def replace(match: re.Match[str]) -> str:
        summary = match.group(1).strip()
        body = match.group(2).strip()
        return f"\n\n{summary}\n\n{body}\n\n"

    return pattern.sub(replace, markdown_body)


def _markdown_to_html(body: str) -> str:
    flattened = _flatten_details(body)
    return md_lib.markdown(flattened, extensions=["extra"])


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
) -> str:
    direction = "rtl" if lang == "fa" else "ltr"
    generated_label = labels.get("generated", "Generated")
    view_online_label = labels.get("view_online", "View online")
    unsubscribe_label = labels.get("unsubscribe", "Unsubscribe")
    header = (
        f'<div class="email-header">'
        f'<strong>{site_title}</strong> &middot; {date_display}'
        f"</div>"
    )
    footer = (
        f'<div class="email-footer">'
        f'<p>{generated_label}: {datetime.utcnow().strftime("%Y-%m-%d %H:%M")} UTC</p>'
        f'<p><a href="{canonical_url}">{view_online_label}</a> &middot; '
        f'<a href="{{{{RESEND_UNSUBSCRIBE_URL}}}}">{unsubscribe_label}</a></p>'
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


def _plain_text_fallback(body_markdown: str) -> str:
    """Cheap markdown-to-text fallback. Resend will generate one if omitted,
    but shipping our own keeps the sections + links in a sensible order."""
    text = _flatten_details(body_markdown)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*', r'\1', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip() + "\n"


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

    title = frontmatter.get("title") or f"Daily Brief: {date_str}"
    if lang == "fa":
        date_display = frontmatter.get("date_fa", date_str)
    else:
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
        subject=title,
        date_display=date_display,
        canonical_url=canonical,
        site_title=site_title,
        lang=lang,
        labels=labels,
    )
    text = _plain_text_fallback(body)

    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("RESEND_API_KEY not set in environment")

    result = send_broadcast(
        api_key=api_key,
        audience_id=audience_id,
        from_addr=mailer_cfg["from_addr"],
        subject=title,
        html=html,
        text=text,
        reply_to=mailer_cfg.get("reply_to"),
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

    en_post = site_dir / "_posts" / f"{date_str}-daily-brief.md"
    fa_post = site_dir / "_fa_posts" / f"{date_str}-daily-brief.md"

    site_base_url = mailer_cfg["site_base_url"]
    en_site_title = mailer_cfg.get("site_title_en", "Daily Brief")
    fa_site_title = mailer_cfg.get("site_title_fa", en_site_title)
    en_labels = mailer_cfg.get("labels_en") or {
        "generated": "Generated",
        "view_online": "View online",
        "unsubscribe": "Unsubscribe",
    }
    fa_labels = mailer_cfg.get("labels_fa") or {
        "generated": "تولید شده در",
        "view_online": "مشاهده آنلاین",
        "unsubscribe": "لغو اشتراک",
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
