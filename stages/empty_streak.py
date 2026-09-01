"""Empty brief streak tracking and owner notifications.

The brief is considered empty when the pipeline found 0 Iran war items to
report (filter yielded 0 included, or report.md is the empty placeholder).
An empty brief must not send broadcasts to subscribers. Instead a consecutive
counter is kept in git-tracked data/empty_streak.json. Notifications are sent
to the owner on days 3, 7, 14, 28 of the streak, then the pipeline stops
completely.

State file is committed so fresh GitHub runners see the streak.
Stopping is implemented as a flag file data/PIPELINE_STOPPED plus an attempt
to disable the GitHub Actions workflow via API (requires actions: write).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


def is_empty_brief(run_dir: str, site_dir: str, date_str: str) -> bool:
    """Return True if the brief for date_str is empty (0 Iran war items)."""
    run_path = Path(run_dir)
    # Primary signal: filtered_items.json included count
    filtered_path = run_path / "filtered_items.json"
    if filtered_path.exists():
        try:
            data = json.loads(filtered_path.read_text(encoding="utf-8"))
            included = sum(1 for item in data if item.get("included"))
            if included == 0:
                return True
        except (json.JSONDecodeError, OSError):
            pass

    # Secondary: report.md placeholder (only exact match counts)
    report_path = run_path / "report.md"
    if report_path.exists():
        try:
            text = report_path.read_text(encoding="utf-8").strip()
            if text == "No significant developments reported today.":
                return True
        except OSError:
            pass

    # Tertiary: published post exists but body is placeholder
    post_path = Path(site_dir) / "_posts" / f"{date_str}-daily-brief.md"
    if post_path.exists():
        try:
            raw = post_path.read_text(encoding="utf-8")
            # Split frontmatter
            if raw.startswith("---"):
                end = raw.find("\n---", 3)
                if end != -1:
                    body = raw[end + 4 :].strip()
                    if body == "No significant developments reported today.":
                        return True
                    if body == "":
                        return True
        except OSError:
            pass

    return False


def load_streak(state_file: str) -> dict:
    """Load streak state, returning defaults if missing or corrupt."""
    path = Path(state_file)
    defaults = {
        "consecutive_empty": 0,
        "last_date": None,
        "last_empty_date": None,
        "notifications_sent": [],
        "stopped": False,
        "history": [],
    }
    if not path.exists():
        return defaults
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # Ensure required keys
        for k, v in defaults.items():
            if k not in data:
                data[k] = v
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Could not load streak file {state_file}: {e}, using defaults")
        return defaults


def save_streak(state_file: str, streak: dict) -> None:
    """Persist streak state to disk."""
    path = Path(state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(streak, indent=2) + "\n", encoding="utf-8")


def _git_commit_file(file_path: str, message: str) -> None:
    """Add, commit, and push a single file. Failures are logged, not raised."""
    try:
        subprocess.run(["git", "add", file_path], check=False, capture_output=True, timeout=30)
        # Check if there is anything to commit
        result = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True, timeout=30)
        if result.returncode == 0:
            logger.info(f"No changes to commit for {file_path}")
            return
        commit = subprocess.run(["git", "commit", "-m", message], capture_output=True, text=True, timeout=30)
        if commit.returncode != 0:
            logger.warning(f"git commit failed for {file_path}: {commit.stderr}")
            return
        push = subprocess.run(["git", "push", "origin", "master"], capture_output=True, text=True, timeout=30)
        if push.returncode != 0:
            logger.warning(f"git push failed for {file_path}: {push.stderr}")
        else:
            logger.info(f"Pushed {file_path}: {message}")
    except Exception as e:
        logger.warning(f"Git commit for {file_path} failed: {e}", exc_info=True)


def _send_owner_email(
    owner_email: str,
    from_addr: str,
    subject: str,
    html: str,
    text: str,
) -> None:
    """Send a single transactional email via Resend /emails endpoint."""
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if not api_key:
        logger.error("RESEND_API_KEY not set, cannot send owner notification")
        raise RuntimeError("RESEND_API_KEY not set")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "from": from_addr,
        "to": owner_email,
        "subject": subject,
        "html": html,
        "text": text,
    }
    with httpx.Client(timeout=30) as client:
        resp = client.post("https://api.resend.com/emails", headers=headers, json=payload)
        if resp.status_code < 200 or resp.status_code >= 300:
            logger.error(f"Resend single email failed ({resp.status_code}): {resp.text}")
            raise RuntimeError(f"Resend email failed ({resp.status_code}): {resp.text}")
        data = resp.json()
        logger.info(f"Owner notification sent to {owner_email}: id={data.get('id')}")


def _attempt_disable_workflow() -> None:
    """Try to disable the Daily Brief workflow via GitHub API."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        logger.info("GITHUB_TOKEN not set, skipping workflow disable")
        return
    # Preferred: use gh CLI if available
    try:
        result = subprocess.run(
            ["gh", "workflow", "disable", "Daily Brief"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            logger.info("Disabled Daily Brief workflow via gh CLI")
            return
        logger.warning(f"gh workflow disable failed: {result.stderr}")
    except FileNotFoundError:
        logger.info("gh CLI not found, trying API directly")

    # Fallback: direct API call. Need workflow id.
    try:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }
        with httpx.Client(timeout=30) as client:
            # List workflows to find Daily Brief
            resp = client.get("https://api.github.com/repos/k1monfared/news_reader/actions/workflows", headers=headers)
            if resp.status_code != 200:
                logger.warning(f"Failed to list workflows: {resp.text}")
                return
            workflows = resp.json().get("workflows", [])
            wf = next((w for w in workflows if w.get("name") == "Daily Brief"), None)
            if not wf:
                logger.warning("Daily Brief workflow not found for disabling")
                return
            wf_id = wf["id"]
            disable = client.put(
                f"https://api.github.com/repos/k1monfared/news_reader/actions/workflows/{wf_id}/disable",
                headers=headers,
            )
            if disable.status_code in (204, 200):
                logger.info(f"Disabled workflow {wf_id} via API")
            else:
                logger.warning(f"Disable workflow API failed: {disable.status_code} {disable.text}")
    except Exception as e:
        logger.warning(f"Attempt to disable workflow failed: {e}", exc_info=True)


def update_streak_and_notify(
    is_empty: bool,
    date_str: str,
    config: object,
    run_dir: str,
) -> dict:
    """Update the empty streak file, send owner notification if needed, and handle stop.

    Returns a dict describing the action for run_meta.
    """
    empty_cfg = getattr(config, "empty_brief", None) or {}
    # Support both dict and object
    if isinstance(empty_cfg, dict):
        enabled = empty_cfg.get("enabled", True)
        owner_email = empty_cfg.get("owner_email", "k1monfared@gmail.com")
        notify_days = empty_cfg.get("notify_days", [3, 7, 14, 28])
        stop_after = empty_cfg.get("stop_after_days", 28)
        state_file = empty_cfg.get("state_file", "data/empty_streak.json")
        stopped_flag = empty_cfg.get("stopped_flag", "data/PIPELINE_STOPPED")
        from_addr = getattr(config, "mailer", {}).get("from_addr", "USrael War Daily Brief <usreal_war_daily_brief@k1monfared.com>") if isinstance(getattr(config, "mailer", {}), dict) else "USrael War Daily Brief <usreal_war_daily_brief@k1monfared.com>"
        if isinstance(getattr(config, "mailer", {}), dict):
            from_addr = config.mailer.get("from_addr", from_addr)
    else:
        enabled = getattr(empty_cfg, "get", lambda k, d: d)("enabled", True) if hasattr(empty_cfg, "get") else True
        owner_email = "k1monfared@gmail.com"
        notify_days = [3, 7, 14, 28]
        stop_after = 28
        state_file = "data/empty_streak.json"
        stopped_flag = "data/PIPELINE_STOPPED"
        from_addr = "USrael War Daily Brief <usreal_war_daily_brief@k1monfared.com>"

    if not enabled:
        return {"streak_enabled": False}

    # Load existing streak
    streak = load_streak(state_file)
    if streak.get("stopped"):
        logger.info(f"Pipeline already stopped (streak file shows stopped). Skipping streak update for {date_str}")
        return {"streak": streak, "is_empty": is_empty, "already_stopped": True}

    # Calculate new streak
    now_consecutive = streak.get("consecutive_empty", 0)
    last_date_str = streak.get("last_empty_date") or streak.get("last_date")

    if is_empty:
        # Check if consecutive: last empty was yesterday
        if last_date_str:
            try:
                last = datetime.strptime(last_date_str, "%Y-%m-%d")
                current = datetime.strptime(date_str, "%Y-%m-%d")
                delta = (current - last).days
                if delta == 1:
                    now_consecutive += 1
                elif delta > 1:
                    # Gap, reset streak to 1
                    logger.info(f"Gap of {delta} days since last empty {last_date_str}, resetting streak to 1")
                    now_consecutive = 1
                    streak["notifications_sent"] = []
                else:
                    # Same day re-run, don't increment again
                    logger.info(f"Streak already updated for {date_str}, not incrementing")
                    # Don't double count same date
                    pass
            except ValueError:
                now_consecutive = 1 if now_consecutive == 0 else now_consecutive + 1
        else:
            now_consecutive = 1 if now_consecutive == 0 else now_consecutive + 1

        # Only update last_empty_date if this is a new date
        if last_date_str != date_str:
            streak["consecutive_empty"] = now_consecutive
            streak["last_empty_date"] = date_str
            streak["last_date"] = date_str
            streak.setdefault("history", []).append({"date": date_str, "empty": True, "consecutive": now_consecutive})
        else:
            # Same date, ensure count is correct but don't duplicate history
            streak["consecutive_empty"] = now_consecutive
    else:
        if now_consecutive != 0:
            logger.info(f"Empty streak broken by non-empty brief on {date_str} (was {now_consecutive}), resetting to 0")
        streak["consecutive_empty"] = 0
        streak["last_date"] = date_str
        # Keep last_empty_date for reference but reset notifications
        streak["notifications_sent"] = []
        streak.setdefault("history", []).append({"date": date_str, "empty": False, "consecutive": 0})

    # Save streak file immediately
    save_streak(state_file, streak)
    _git_commit_file(state_file, f"Update empty streak: {date_str} empty={is_empty} consecutive={streak['consecutive_empty']}")

    result = {
        "is_empty": is_empty,
        "consecutive_empty": streak["consecutive_empty"],
        "notify_days": notify_days,
        "stopped": streak.get("stopped", False),
    }

    # Check if we should notify owner
    if is_empty and now_consecutive in notify_days and now_consecutive not in streak.get("notifications_sent", []):
        # Build notification email
        next_notify = next((d for d in sorted(notify_days) if d > now_consecutive), None)
        if streak.get("stopped"):
            next_info = "No further notifications (pipeline already stopped)."
        elif next_notify:
            next_info = f"Next notification will be at {next_notify} consecutive empty days."
        else:
            next_info = f"This was the final scheduled notification at {now_consecutive} days."

        stop_info = f"Pipeline will stop completely after {stop_after} consecutive empty days (no more scheduled runs)."

        subject = f"Daily Brief empty for {now_consecutive} consecutive days ({date_str})"

        # List of all notify days for transparency
        schedule_str = ", ".join(str(d) for d in sorted(notify_days))
        html = f"""
        <div style="font-family: sans-serif; line-height: 1.6; max-width: 600px;">
        <h2>Daily Brief empty streak: {now_consecutive} days</h2>
        <p>Today's brief for <strong>{date_str}</strong> was empty (no Iran war items to report).</p>
        <p>Consecutive empty days: <strong>{now_consecutive}</strong></p>
        <p>Notification schedule: {schedule_str} days. {next_info}</p>
        <p>{stop_info}</p>
        <p>Current streak file: <code>{state_file}</code></p>
        <p>All empty briefs do NOT send broadcasts to subscribers. This notification is sent only to you.</p>
        <p>After {stop_after} consecutive empty days, the pipeline will create <code>{stopped_flag}</code> and attempt to disable the GitHub Actions workflow, stopping all future runs. To restart, delete that flag file and re-enable the workflow.</p>
        <p>History: {json.dumps(streak.get('history', [])[-5:], indent=2)}</p>
        </div>
        """
        text = f"""Daily Brief empty streak: {now_consecutive} days

Today's brief for {date_str} was empty (no Iran war items).
Consecutive empty days: {now_consecutive}
Notification schedule: {schedule_str} days. {next_info}
{stop_info}

All empty briefs do not send broadcasts.
After {stop_after} days the pipeline stops completely. To restart, delete {stopped_flag} and re-enable workflow.
"""

        try:
            _send_owner_email(owner_email, from_addr, subject, html, text)
            streak["notifications_sent"] = sorted(set(streak.get("notifications_sent", []) + [now_consecutive]))
            save_streak(state_file, streak)
            _git_commit_file(state_file, f"Mark notification sent for {now_consecutive} days")
            result["notification_sent"] = now_consecutive
            logger.info(f"Owner notification sent for streak {now_consecutive}")
        except Exception as e:
            logger.error(f"Failed to send owner notification for streak {now_consecutive}: {e}", exc_info=True)
            result["notification_error"] = str(e)

    # Check if we should stop completely
    if streak.get("consecutive_empty", 0) >= stop_after and not streak.get("stopped"):
        logger.info(f"Streak reached {stop_after} days, stopping pipeline")
        streak["stopped"] = True
        streak["stopped_at"] = date_str
        save_streak(state_file, streak)
        _git_commit_file(state_file, f"Pipeline stopped after {stop_after} consecutive empty days")

        # Create stopped flag file
        try:
            Path(stopped_flag).parent.mkdir(parents=True, exist_ok=True)
            Path(stopped_flag).write_text(f"Pipeline stopped on {date_str} after {stop_after} consecutive empty briefs.\nTo restart: remove this file and re-enable GitHub Actions workflow.\n")
            _git_commit_file(stopped_flag, f"Create pipeline stopped flag after {stop_after} empty days")
        except Exception as e:
            logger.warning(f"Failed to create stopped flag: {e}")

        # Attempt to disable workflow
        _attempt_disable_workflow()

        # Send final owner email indicating stopped
        try:
            subject = f"Daily Brief PIPELINE STOPPED after {stop_after} empty days ({date_str})"
            html = f"""
            <div style="font-family: sans-serif; line-height: 1.6; max-width: 600px;">
            <h2>Pipeline stopped</h2>
            <p>The Daily Brief has been empty for <strong>{stop_after}</strong> consecutive days (last date {date_str}).</p>
            <p>Per your configuration, the pipeline has been stopped completely. No more scheduled runs will produce briefs or emails.</p>
            <p>To restart: delete <code>{stopped_flag}</code> and <code>{state_file}</code> or set <code>stopped: false</code>, and re-enable the GitHub Actions workflow "Daily Brief".</p>
            <p>Workflow disable attempted via API. If it failed, manually disable it in GitHub: Actions -&gt; Daily Brief -&gt; Disable workflow.</p>
            </div>
            """
            text = f"Pipeline stopped after {stop_after} empty days (last {date_str}). Delete {stopped_flag} and re-enable workflow to restart."
            _send_owner_email(owner_email, from_addr, subject, html, text)
            logger.info("Final stopped notification sent")
        except Exception as e:
            logger.error(f"Failed to send stopped notification: {e}")

        result["stopped_now"] = True

    return result
