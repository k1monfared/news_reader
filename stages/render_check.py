"""Render-check stage: catch markdown that kramdown mis-renders.

The site renders posts with kramdown (``parse_block_html: true``, see
``docs/_config.yml``). Two failure modes have shipped broken pages before:

1. Bare ``<details>`` / ``<summary>`` tags (missing ``markdown="block"`` /
   ``markdown="span"``). Kramdown then treats the element body as raw text,
   escapes the literal ``</summary>`` tag, and nests the whole brief inside
   the summary, so the page shows one giant toggle label plus stray
   ``</summary>`` / ``</details>`` text.
2. Unescaped `` | `` separators, e.g. the ``Sources: [a](url) | [b](url)``
   line. Kramdown parses them as a table row, so links render inside a
   bordered two-column table.

``check_and_repair`` fixes the known-safe cases in place (adds the missing
markdown attributes, escapes source-line pipes) and reports anything it
cannot fix (unbalanced tags, escaped HTML entities, a ``<details>`` without
a ``<summary>``). The pipeline runs it on the final report before publish
and fails the run if problems remain, so a broken page never ships.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from models import PipelineConfig
from llm_client import AuditedLLMClient
from audit_logger import AuditedHTTPClient

logger = logging.getLogger(__name__)


DETAILS_OPEN_RE = re.compile(r"<details\b[^>]*>")
SUMMARY_OPEN_RE = re.compile(r"<summary\b[^>]*>")
TAG_RE = re.compile(r"</?(?:details|summary)\b[^>]*>")
SOURCE_LINE_RE = re.compile(r"^\s*\**\s*(?:Sources|منابع)\s*\**\s*:")
LINK_PIPE_RE = re.compile(r"\]\(https?://[^)]*\)\s*\|\s*")
UNESCAPED_PIPE_RE = re.compile(r"(?<!\\)\|")
ESCAPED_HTML_RE = re.compile(r"&lt;/?(?:details|summary)\b")
DETAILS_BLOCK_RE = re.compile(r"<details\b[^>]*>(.*?)</details>", re.S)


def _add_markdown_attr(tag: str, attr: str) -> str:
    """Insert ``attr`` into an opening tag unless a markdown attr is present."""
    if "markdown=" in tag:
        return tag
    return tag[: -len(">")] + f' {attr}>'


def repair_markdown_attrs(markdown: str) -> tuple[str, list[str]]:
    """Add missing markdown="block"/"span" attributes to details/summary tags."""
    repairs: list[str] = []

    def details_repl(match: re.Match) -> str:
        tag = match.group(0)
        fixed = _add_markdown_attr(tag, 'markdown="block"')
        if fixed != tag:
            repairs.append("added markdown=\"block\" to <details>")
        return fixed

    def summary_repl(match: re.Match) -> str:
        tag = match.group(0)
        fixed = _add_markdown_attr(tag, 'markdown="span"')
        if fixed != tag:
            repairs.append("added markdown=\"span\" to <summary>")
        return fixed

    markdown = DETAILS_OPEN_RE.sub(details_repl, markdown)
    markdown = SUMMARY_OPEN_RE.sub(summary_repl, markdown)
    return markdown, repairs


def escape_source_pipes(markdown: str) -> tuple[str, list[str]]:
    """Escape unescaped pipes on Sources:/منابع: lines so kramdown does not
    turn them into a table."""
    repairs: list[str] = []
    out_lines = []
    for line in markdown.splitlines():
        if (SOURCE_LINE_RE.match(line) or LINK_PIPE_RE.search(line)) and "->" not in line:
            fixed = re.sub(r"\\+\|", r"\\|", line)
            fixed = UNESCAPED_PIPE_RE.sub(r"\\|", fixed)
            if fixed != line:
                repairs.append("escaped pipe separator on sources line")
            line = fixed
        out_lines.append(line)
    return "\n".join(out_lines) + ("\n" if markdown.endswith("\n") else ""), repairs


def structural_issues(markdown: str) -> list[str]:
    """Return problems that cannot be safely auto-repaired."""
    issues: list[str] = []

    if ESCAPED_HTML_RE.search(markdown):
        issues.append("escaped HTML entity (&lt;details or &lt;/summary) in report")

    stack: list[str] = []
    for match in TAG_RE.finditer(markdown):
        tag = match.group(0)
        name = "details" if "details" in tag else "summary"
        if tag.startswith("</"):
            if not stack or stack[-1] != name:
                issues.append(f"unbalanced closing tag {tag}")
                return issues
            stack.pop()
        else:
            stack.append(name)
    if stack:
        issues.append(f"unclosed tag(s): {', '.join(stack)}")

    for match in DETAILS_BLOCK_RE.finditer(markdown):
        inner = match.group(1)
        first_tag = TAG_RE.search(inner)
        if first_tag is None:
            issues.append("<details> block without a <summary>")
            return issues
        if not first_tag.group(0).startswith("<summary"):
            issues.append("content before <summary> inside <details>")
            return issues

    return issues


def check_and_repair(markdown: str) -> tuple[str, list[str], list[str]]:
    """Repair safe issues and collect unresolved ones.

    Returns ``(repaired_markdown, repairs, issues)``.
    """
    repairs: list[str] = []
    markdown, new_repairs = repair_markdown_attrs(markdown)
    repairs.extend(new_repairs)
    markdown, new_repairs = escape_source_pipes(markdown)
    repairs.extend(new_repairs)
    issues = structural_issues(markdown)
    return markdown, repairs, issues


def find_report(run_path: Path) -> Path | None:
    """Pick the same report file publish would use."""
    for name in ("report_verified.md", "report_edited.md", "report.md"):
        candidate = run_path / name
        if candidate.exists():
            return candidate
    return None


def run_render_check(
    run_dir: str,
    config: PipelineConfig,
    llm_client: AuditedLLMClient,
    http_client: AuditedHTTPClient,
) -> dict:
    run_path = Path(run_dir)
    report_path = find_report(run_path)
    if report_path is None:
        logger.warning("No report found to render-check")
        return {"status": "skipped"}

    markdown = report_path.read_text(encoding="utf-8")
    repaired, repairs, issues = check_and_repair(markdown)

    if repairs:
        report_path.write_text(repaired, encoding="utf-8")
        logger.warning(
            f"Render check repaired {len(repairs)} issue(s) in {report_path.name}: "
            + "; ".join(sorted(set(repairs)))
        )

    if issues:
        (run_path / "render_check.json").write_text(
            json.dumps({"issues": issues, "repairs": repairs}, indent=2)
        )
        raise RuntimeError(
            "Render check failed: " + "; ".join(issues)
        )

    logger.info(f"Render check passed ({len(repairs)} repair(s), 0 unresolved)")
    return {
        "status": "passed",
        "report": report_path.name,
        "repairs": repairs,
        "issues": [],
    }
