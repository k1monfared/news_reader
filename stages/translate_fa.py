"""Translate the day's English brief to Farsi and update the Farsi bias
mirror. Runs after `publish` as the last pipeline stage. When
`translate_fa.enabled` is true in config, this stage is responsible for
the final `git add / commit / push` that covers BOTH languages atomically
— `publish` in that mode only writes the English file to disk."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jdatetime

from models import PipelineConfig
from llm_client import AuditedLLMClient, extract_json
from audit_logger import AuditedHTTPClient
from prompt_loader import load_prompt
from stages.publish import build_model_footer

logger = logging.getLogger(__name__)


BIASES_PATH = Path("docs/_data/source_biases.json")
BIASES_FA_PATH = Path("docs/_data/source_biases_fa.json")
FA_POSTS_DIR = Path("docs/_fa_posts")
BIAS_BATCH_SIZE = 1  # One entry per call — individual bias `detail` fields
                     # can be very long, and Farsi output uses more tokens
                     # than English, so batching risks mid-reply truncation.


PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _to_persian_digits(text: str) -> str:
    return text.translate(PERSIAN_DIGITS)


def _shamsi_date(iso_date: str) -> str:
    """Convert a YYYY-MM-DD Gregorian date to a Farsi Shamsi string.

    Output format is day-month-year in logical order with Persian digits,
    e.g. "۲۷ فروردین ۱۴۰۵". Rendered in an RTL paragraph the visual
    left-to-right reading is year-month-day.
    """
    year, month, day = (int(part) for part in iso_date.split("-"))
    jdt = jdatetime.date.fromgregorian(year=year, month=month, day=day)
    month_name = jdt.j_months_fa[jdt.month - 1]
    return f"{_to_persian_digits(str(jdt.day))} {month_name} {_to_persian_digits(str(jdt.year))}"


def _split_frontmatter(markdown: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_markdown) from a Jekyll post file."""
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


def _build_fa_frontmatter(
    date_str: str,
    fa_date_str: str,
    fa_title: str,
    generated_at: str | None,
    sources_down: list[str],
) -> str:
    lines = [
        "---",
        "layout: post",
        "lang: fa",
        f'title: "{fa_title}"',
        f"date: {date_str}",
        f'date_fa: "{fa_date_str}"',
        f"sources_down: {json.dumps(sources_down, ensure_ascii=False)}",
    ]
    if generated_at:
        lines.append(f'generated_at: "{generated_at}"')
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + "\n"


def _fa_title_for_date(fa_date_str: str) -> str:
    return f"گزارش روزانه: {fa_date_str}"


def _translate_brief(
    body_markdown: str,
    category_translations: dict[str, str],
    llm_client: AuditedLLMClient,
    config: PipelineConfig,
) -> str:
    template = load_prompt("translate_brief_fa", config.paths.get("prompts_dir", "prompts"))
    dictionary = "\n".join(
        f'  - "{en}" → "{fa}"' for en, fa in category_translations.items()
    )
    system, user_msg, version = template.render(
        category_dictionary=dictionary or "(none)",
        brief_markdown=body_markdown,
    )
    model = config.models.get("default", "deepseek-v4-flash-free")
    response = llm_client.call(
        stage="translate_fa",
        prompt_name="translate_brief_fa",
        prompt_version=version,
        system=system,
        user_message=user_msg,
        model=model,
        max_tokens=8192,
    )
    cleaned = response.strip()
    cleaned = re.sub(r"^```(?:markdown)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned + ("\n" if not cleaned.endswith("\n") else "")


def _translate_bias_batch(
    entries: list[dict],
    llm_client: AuditedLLMClient,
    config: PipelineConfig,
) -> list[dict]:
    template = load_prompt("translate_biases_fa", config.paths.get("prompts_dir", "prompts"))
    system, user_msg, version = template.render(
        entries_json=json.dumps(entries, ensure_ascii=False, indent=2),
    )
    model = config.models.get("default", "deepseek-v4-flash-free")
    response = llm_client.call(
        stage="translate_fa",
        prompt_name="translate_biases_fa",
        prompt_version=version,
        system=system,
        user_message=user_msg,
        model=model,
        max_tokens=16384,
    )
    try:
        translated = extract_json(response)
    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"Bias batch translation returned invalid JSON: {e}")
        return entries  # pass through untranslated rather than losing entries
    if not isinstance(translated, list) or len(translated) != len(entries):
        logger.warning("Bias batch translation returned wrong shape; falling back.")
        return [_with_pattern_en(e) for e in entries]
    # Force copy-through of fields that must never be translated, and
    # stash the original English pattern so we can identify this entry
    # across languages on future runs.
    for src, dst in zip(entries, translated):
        dst["pattern_en"] = src.get("pattern", "")
        for key in ("date_added", "status", "example_url"):
            if key in src:
                dst[key] = src[key]
    return translated


def _with_pattern_en(entry: dict) -> dict:
    """Return a copy of an English bias entry tagged with its own
    pattern_en (so round-trip 'fallback to English' still carries the
    stable id needed for future matching)."""
    out = dict(entry)
    out["pattern_en"] = entry.get("pattern", "")
    return out


def _bias_key(entry: dict) -> tuple[str, str]:
    """Stable identity for a bias entry within its source, independent
    of language. Farsi entries carry the original English pattern in
    `pattern_en`; English entries only have `pattern`."""
    pattern = entry.get("pattern_en") or entry.get("pattern", "")
    return (pattern, entry.get("date_added", ""))


def _translate_new_biases(
    llm_client: AuditedLLMClient,
    config: PipelineConfig,
) -> dict:
    """Merge new/changed English bias entries into the Farsi mirror.

    Existing Farsi entries are preserved byte-for-byte. Only entries whose
    (pattern, date_added) identity is missing from the Farsi file get
    translated and added. Per-source top-level fields like `display_name`
    and `notes` are copied from the Farsi file when present, otherwise
    taken from the English file as-is (expected to be translated by the
    one-time bootstrap script).
    """
    if not BIASES_PATH.exists():
        logger.info("No source_biases.json on disk; skipping Farsi bias sync.")
        return {"new_biases_translated": 0}

    en_data = json.loads(BIASES_PATH.read_text(encoding="utf-8"))
    if BIASES_FA_PATH.exists():
        fa_data = json.loads(BIASES_FA_PATH.read_text(encoding="utf-8"))
    else:
        fa_data = {}

    new_count = 0
    for source_key, en_source in en_data.items():
        fa_source = fa_data.get(source_key, {})
        # Carry display_name / notes across — if the Farsi file was
        # bootstrapped these are already translated; if this is a brand-new
        # source we fall back to the English strings and the bootstrap
        # script can be re-run later to catch them up.
        display_name = fa_source.get("display_name") or en_source.get("display_name", source_key)
        notes = fa_source.get("notes", en_source.get("notes", ""))

        existing_keys = { _bias_key(b) for b in fa_source.get("biases", []) }
        to_translate = [
            b for b in en_source.get("biases", [])
            if _bias_key(b) not in existing_keys
        ]

        translated_new: list[dict] = []
        for i in range(0, len(to_translate), BIAS_BATCH_SIZE):
            batch = to_translate[i:i + BIAS_BATCH_SIZE]
            logger.info(
                f"Translating bias batch for {source_key}: "
                f"{i + 1}-{i + len(batch)} of {len(to_translate)}"
            )
            translated_new.extend(_translate_bias_batch(batch, llm_client, config))
            new_count += len(batch)

        fa_data[source_key] = {
            "display_name": display_name,
            "notes": notes,
            "biases": fa_source.get("biases", []) + translated_new,
        }

    BIASES_FA_PATH.parent.mkdir(parents=True, exist_ok=True)
    BIASES_FA_PATH.write_text(
        json.dumps(fa_data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {"new_biases_translated": new_count}


def _run_git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + list(args), capture_output=True, text=True, timeout=60
    )


def _git_commit_push(date_str: str) -> None:
    _run_git("add", "docs/_posts/", "docs/_fa_posts/", "docs/_data/")
    result = _run_git("diff", "--cached", "--quiet")
    if result.returncode == 0:
        logger.info("No changes to commit")
        return
    _run_git("commit", "-m", f"Daily brief: {date_str} (en + fa)")
    push = _run_git("push", "origin", "master")
    if push.returncode != 0:
        logger.warning(f"git push failed: {push.stderr.strip()}")


def run_translate_fa(
    run_dir: str,
    config: PipelineConfig,
    llm_client: AuditedLLMClient,
    http_client: AuditedHTTPClient,
) -> dict:
    tfa = config.translate_fa or {}
    if not tfa.get("enabled", False):
        logger.info("translate_fa disabled; skipping.")
        return {"status": "skipped"}

    run_path = Path(run_dir)
    date_str = run_path.name[:10]  # YYYY-MM-DD from run_id

    site_dir = Path(config.publish.get("site_dir", "docs"))
    en_post_path = site_dir / "_posts" / f"{date_str}-daily-brief.md"
    if not en_post_path.exists():
        logger.error(f"English post not found at {en_post_path}; cannot translate.")
        return {"status": "failed", "reason": "no_english_post"}

    # Parse the English post to split frontmatter from body.
    raw_post = en_post_path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(raw_post)

    # Remove the model footer from the English body before translation so it
    # is not translated/mangled; it is re-appended below for the Farsi post.
    body = re.sub(r"\n---\n\n\*Model: [^*]*\*\s*$", "", body).rstrip() + "\n"

    # Translate the brief body to Farsi.
    category_translations = tfa.get("category_translations", {})
    logger.info("Translating English brief body to Farsi...")
    fa_body = _translate_brief(body, category_translations, llm_client, config)

    # Append the model footer to the Farsi post as well.
    footer = build_model_footer(run_dir, config)
    fa_body = fa_body.rstrip() + footer

    # Build Farsi frontmatter.
    fa_date_str = _shamsi_date(date_str)
    fa_title = _fa_title_for_date(fa_date_str)
    sources_down = []
    if "sources_down" in frontmatter:
        try:
            sources_down = json.loads(frontmatter["sources_down"])
        except json.JSONDecodeError:
            sources_down = []
    fa_fm = _build_fa_frontmatter(
        date_str=date_str,
        fa_date_str=fa_date_str,
        fa_title=fa_title,
        generated_at=frontmatter.get("generated_at"),
        sources_down=sources_down,
    )

    # Write the Farsi post.
    FA_POSTS_DIR.mkdir(parents=True, exist_ok=True)
    fa_post_path = FA_POSTS_DIR / f"{date_str}-daily-brief.md"
    fa_post_path.write_text(fa_fm + fa_body, encoding="utf-8")
    logger.info(f"Wrote Farsi post to {fa_post_path}")

    # Merge any new English bias entries into the Farsi mirror.
    bias_result = _translate_new_biases(llm_client, config)
    logger.info(
        f"Bias sync: {bias_result.get('new_biases_translated', 0)} new entries translated."
    )

    # Atomic commit + push covering both languages.
    _git_commit_push(date_str)

    return {
        "status": "published",
        "fa_post": str(fa_post_path),
        **bias_result,
    }
