"""Build ``docs/_data/failures.json``: failed days, their logs, and backfills.

Reads:
* GitHub Actions failures for the Daily Brief workflow (via ``gh``), mapped to
  the brief date in the schedule timezone.
* The run database (``data/run_metrics.jsonl``) for recorded failed stages.
* Backfilled posts (frontmatter ``backfilled: true``) and the commit that added
  them (via ``git log``).

Writes a single view used by ``/failures/`` and the dashboard counts. Meant to
run on its own schedule so it records failures even when the pipeline aborts.

Usage:
  python scripts/build_failures.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import load_config
from stages.metrics import load_run_records

DEFAULT_OUT = "docs/_data/failures.json"
BACKFILLED_RE = re.compile(r"^\s*backfilled:\s*true\s*$", re.M | re.I)
DEFAULT_REPO = "k1monfared/news_reader"


def _repo() -> str:
    return os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO)


def _run_git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def run_date(created_at: str, tz_offset_hours: int = -7) -> str | None:
    """Map a GitHub run's created_at (UTC) to the brief date in the schedule tz."""
    if not created_at:
        return None
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(timezone(timedelta(hours=tz_offset_hours)))
    return local.strftime("%Y-%m-%d")


def list_failed_runs(workflow: str, limit: int = 100) -> list[dict]:
    """Failed runs for a workflow via gh; [] when gh is unavailable."""
    try:
        result = subprocess.run(
            [
                "gh", "run", "list", f"--workflow={workflow}", "--status=failure",
                f"--limit={limit}",
                "--json", "databaseId,createdAt,conclusion,url,event,headBranch",
            ],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        print(f"WARNING: gh run list failed: {result.stderr.strip()}", file=sys.stderr)
        return []
    try:
        return json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return []


def scan_backfilled(*dirs: Path) -> dict[str, list[str]]:
    """Map date -> post paths whose frontmatter has backfilled: true."""
    out: dict[str, list[str]] = {}
    for directory in dirs:
        if not directory.is_dir():
            continue
        for path in directory.glob("*-daily-brief.md"):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            # Only inspect the frontmatter block.
            if text.startswith("---"):
                end = text.find("\n---", 3)
                frontmatter = text[: end if end != -1 else 400]
            else:
                frontmatter = text[:400]
            if BACKFILLED_RE.search(frontmatter):
                out.setdefault(path.name[:10], []).append(str(path))
    return out


def commit_for_path(path: str) -> dict | None:
    """The commit that last touched a path (via git log)."""
    out = _run_git("log", "-1", "--format=%H%x09%s%x09%cI", "--", path)
    if not out.strip():
        return None
    parts = out.strip().split("\t")
    if len(parts) < 3:
        return None
    commit_hash, message, when = parts[0], parts[1], parts[2]
    return {
        "hash": commit_hash,
        "short": commit_hash[:8],
        "message": message,
        "date": when,
        "url": f"https://github.com/{_repo()}/commit/{commit_hash}",
    }


def fix_commits_for_date(date_str: str, limit: int = 3) -> list[dict]:
    """Commits whose message mentions the date (candidate fix commits)."""
    out = _run_git(
        "log", "--all", "--format=%H%x09%s%x09%cI", f"--grep={date_str}", f"-n{limit}"
    )
    commits = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        commits.append({
            "hash": parts[0],
            "short": parts[0][:8],
            "message": parts[1],
            "date": parts[2],
            "url": f"https://github.com/{_repo()}/commit/{parts[0]}",
        })
    return commits


def build_payload(
    failed_runs_by_date: dict[str, dict],
    records_by_date: dict[str, dict],
    backfilled_by_date: dict[str, list[str]],
    commit_lookup,
    fix_lookup,
) -> dict:
    """Assemble the failures view from prepared inputs (pure)."""
    failed_dates: list[dict] = []
    all_failed = set(failed_runs_by_date) | {
        d for d, r in records_by_date.items() if r.get("status") == "failed"
    }
    for date_str in sorted(all_failed):
        run = failed_runs_by_date.get(date_str, {})
        rec = records_by_date.get(date_str, {})
        posts = backfilled_by_date.get(date_str, [])
        backfill_commit = None
        if posts:
            backfill_commit = commit_lookup(posts[0])
        failed_dates.append({
            "date": date_str,
            "run_url": run.get("url"),
            "run_id": run.get("databaseId"),
            "created_at": run.get("createdAt"),
            "failed_stages": rec.get("failed_stages", []),
            "errors": rec.get("errors", []),
            "backfilled": bool(posts),
            "backfill_commit": backfill_commit,
            "fix_commits": fix_lookup(date_str) if posts else [],
        })

    backfilled_dates = [
        {"date": d, "posts": paths, "commit": commit_lookup(paths[0])}
        for d, paths in sorted(backfilled_by_date.items())
    ]

    failed_set = {d["date"] for d in failed_dates}
    backfilled_set = {d["date"] for d in backfilled_dates}
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "counts": {
            "failed": len(failed_dates),
            "backfilled": len(backfilled_dates),
            "failed_and_backfilled": len(failed_set & backfilled_set),
            "failed_not_backfilled": sorted(failed_set - backfilled_set),
            "backfilled_without_failed": sorted(backfilled_set - failed_set),
        },
        "failed_dates": failed_dates,
        "backfilled_dates": backfilled_dates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args()

    config = load_config(args.config)
    site_dir = Path(config.publish.get("site_dir", "docs"))
    metrics_cfg = config.metrics or {}
    db_file = metrics_cfg.get("db_file", "data/run_metrics.jsonl")

    runs = list_failed_runs("Daily Brief")
    failed_runs_by_date: dict[str, dict] = {}
    for run in runs:
        date_str = run_date(run.get("createdAt", ""))
        if date_str and date_str not in failed_runs_by_date:
            failed_runs_by_date[date_str] = run

    records = load_run_records(db_file)
    records_by_date = {r.get("target_date"): r for r in records if r.get("target_date")}

    backfilled = scan_backfilled(site_dir / "_posts", site_dir / "_fa_posts")

    payload = build_payload(
        failed_runs_by_date,
        records_by_date,
        backfilled,
        commit_lookup=commit_for_path,
        fix_lookup=fix_commits_for_date,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        f"Wrote {out}: {payload['counts']['failed']} failed, "
        f"{payload['counts']['backfilled']} backfilled"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
