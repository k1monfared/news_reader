"""Post-failure notification for the Daily Brief workflow.

GitHub's built-in failure email only says the run failed; it cannot carry a
custom log summary. To get a useful alert, the workflow tees the pipeline
output to ``pipeline.log`` and runs this script in an ``if: failure()`` step.
The script reads the newest run's ``run_meta.json`` plus the log, builds a
short summary of what failed and why, writes it to the job summary (UI),
emits an ``::error::`` annotation, and emails the owner via Resend.

Usage (CI):
  python scripts/failure_notice.py --log pipeline.log --data-dir data

Env (optional): RESEND_API_KEY, FAILURE_NOTICE_EMAIL, GITHUB_SERVER_URL,
GITHUB_REPOSITORY, GITHUB_RUN_ID, GITHUB_STEP_SUMMARY.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from pathlib import Path

import httpx

# Mirrors run_pipeline.CRITICAL_STAGES; kept local so this script stays
# importable even if the pipeline package is broken.
CRITICAL_STAGES = {
    "fetch",
    "filter",
    "summarize",
    "render_check",
    "publish",
    "translate_fa",
}

ERROR_MARKERS = (
    "[ERROR]",
    "Traceback (most recent call last)",
    "RuntimeError:",
    "Error:",
    "critical failure",
)
CONTEXT_AFTER_MATCH = 6
MAX_EXCERPT_LINES = 40
DEFAULT_OWNER_EMAIL = "k1monfared@gmail.com"
DEFAULT_FROM_ADDR = "USrael War Daily Brief <usreal_war_daily_brief@k1monfared.com>"


def latest_run_meta(data_dir: str) -> dict | None:
    """Return the run_meta.json of the most recently modified run, or None."""
    runs_dir = Path(data_dir) / "runs"
    if not runs_dir.is_dir():
        return None
    candidates = sorted(
        (p for p in runs_dir.iterdir() if p.is_dir() and not p.is_symlink()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for run_path in candidates:
        meta_path = run_path / "run_meta.json"
        if meta_path.exists():
            try:
                return json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
    return None


def _extract_error_excerpt(
    log_text: str, max_lines: int = MAX_EXCERPT_LINES
) -> list[str]:
    """Pull error and traceback lines (plus a little context) from a log."""
    lines = log_text.splitlines()
    picked: list[str] = []
    for i, line in enumerate(lines):
        if any(marker in line for marker in ERROR_MARKERS):
            picked.extend(lines[i : i + CONTEXT_AFTER_MATCH])
    seen: set[str] = set()
    unique: list[str] = []
    for line in picked:
        if line not in seen:
            seen.add(line)
            unique.append(line)
    return unique[-max_lines:]


def summarize(data_dir: str, log_text: str) -> dict:
    """Build a failure summary from run_meta.json and the pipeline log."""
    meta = latest_run_meta(data_dir) or {}
    run_id = meta.get("run_id")
    stages = meta.get("stages", {}) if isinstance(meta.get("stages"), dict) else {}
    failed = sorted(
        name
        for name, info in stages.items()
        if isinstance(info, dict) and info.get("status") == "failed"
    )
    failed_critical = [s for s in failed if s in CRITICAL_STAGES]

    errors = list(meta.get("errors", []))
    if not errors:
        errors = [
            f"{name}: {stages[name].get('error', '')}".strip()
            for name in failed
            if stages.get(name, {}).get("error")
        ]

    if not failed and not errors:
        summary_line = "Pipeline failed before writing run_meta.json (see excerpt)."
    elif failed_critical:
        summary_line = "Critical stages failed: " + ", ".join(failed_critical)
    else:
        summary_line = "Non-critical stage failure(s): " + ", ".join(failed)

    return {
        "run_id": run_id,
        "failed_stages": failed,
        "failed_critical": failed_critical,
        "errors": errors,
        "excerpt": _extract_error_excerpt(log_text),
        "summary_line": summary_line,
    }


def render_text(summary: dict, run_url: str | None = None) -> str:
    lines = [
        "Daily Brief pipeline FAILED",
        f"Run: {summary.get('run_id') or 'unknown'}",
        summary["summary_line"],
    ]
    if run_url:
        lines.append(f"Details: {run_url}")
    if summary["errors"]:
        lines.append("")
        lines.append("Errors:")
        lines.extend(f"  - {e}" for e in summary["errors"][:10])
    if summary["excerpt"]:
        lines.append("")
        lines.append("Log excerpt (last lines):")
        lines.extend(summary["excerpt"])
    return "\n".join(lines)


def render_markdown(summary: dict, run_url: str | None = None) -> str:
    lines = [
        "## Daily Brief pipeline failed",
        "",
        f"- **Run:** `{summary.get('run_id') or 'unknown'}`",
        f"- **Status:** {summary['summary_line']}",
    ]
    if run_url:
        lines.append(f"- **Details:** [workflow run]({run_url})")
    if summary["errors"]:
        lines.extend(["", "### Errors", ""])
        lines.extend(f"- {e}" for e in summary["errors"][:10])
    if summary["excerpt"]:
        lines.extend(["", "### Log excerpt (last lines)", "", "```"])
        lines.extend(summary["excerpt"])
        lines.append("```")
    return "\n".join(lines)


def _load_config(config_path: str) -> dict:
    try:
        import yaml

        return yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _owner_email(config: dict) -> str:
    return (
        os.environ.get("FAILURE_NOTICE_EMAIL")
        or (config.get("empty_brief") or {}).get("owner_email")
        or DEFAULT_OWNER_EMAIL
    )


def _from_addr(config: dict) -> str:
    return (config.get("mailer") or {}).get("from_addr") or DEFAULT_FROM_ADDR


def send_failure_email(
    owner_email: str,
    from_addr: str,
    subject: str,
    text: str,
    markdown: str,
) -> dict:
    """Send the failure summary via Resend /emails. Raises without a key."""
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("RESEND_API_KEY not set; cannot send failure notice")
    html_body = (
        '<div style="font-family: sans-serif; line-height: 1.5;">'
        f"<pre style=\"white-space: pre-wrap;\">{html.escape(markdown)}</pre>"
        "</div>"
    )
    payload = {
        "from": from_addr,
        "to": owner_email,
        "subject": subject,
        "text": text,
        "html": html_body,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=30) as client:
        resp = client.post("https://api.resend.com/emails", headers=headers, json=payload)
    if resp.status_code < 200 or resp.status_code >= 300:
        raise RuntimeError(f"Resend email failed ({resp.status_code}): {resp.text}")
    return resp.json()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default="pipeline.log")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    log_text = ""
    if Path(args.log).exists():
        log_text = Path(args.log).read_text(encoding="utf-8", errors="replace")

    summary = summarize(args.data_dir, log_text)

    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    run_url = (
        f"{server}/{repo}/actions/runs/{run_id}"
        if server and repo and run_id
        else None
    )

    markdown = render_markdown(summary, run_url)
    text = render_text(summary, run_url)

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        try:
            with open(step_summary, "a", encoding="utf-8") as handle:
                handle.write(markdown + "\n")
        except OSError:
            pass

    # One-line annotation: shows on the run page and in the failed-check UI.
    print(
        f"::error::Daily Brief failed. {summary['summary_line']} "
        f"(run {summary.get('run_id') or 'unknown'})"
    )

    config = _load_config(args.config)
    owner = _owner_email(config)
    subject = f"Daily Brief FAILED: {summary['summary_line']}"
    try:
        send_failure_email(owner, _from_addr(config), subject, text, markdown)
        print(f"Failure notice emailed to {owner}")
    except Exception as exc:  # never mask the original failure
        print(f"::warning::Could not email failure notice: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
