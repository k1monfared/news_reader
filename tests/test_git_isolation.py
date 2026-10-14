"""Regression test: the test suite must never commit or push.

``run_mailer`` (and the empty-brief streak tracker it calls) used to run a real
``git add``/``git commit``/``git push origin master`` during ``pytest tests``,
pushing "Update empty streak" commits to the real repository.  The autouse
``_no_real_git`` guard in ``conftest.py`` no-ops ``_git_commit_file`` on every
stage module that defines it.

This test proves the property rather than trusting the fixture: it captures the
repository HEAD, runs the exact code path that used to push, and asserts HEAD
did not move.  It also asserts the module's ``_git_commit_file`` is not the real
implementation, so removing the guard fails the test even if a temp state file
happens not to produce a commit.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import progress as progress_module
import stages.empty_streak as empty_streak_module
import stages.mailer as mailer_module
import stages.metrics as metrics_module
import stages.publish as publish_module
import stages.translate_fa as tfa_module
from run_pipeline import get_timezone_offset

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Captured at import time, before any autouse fixture runs, so these are the
# real implementations regardless of test ordering.
_REAL_GIT_COMMIT_FILE = empty_streak_module._git_commit_file
_REAL_MAILER_COMMIT_FILE = mailer_module._git_commit_file
_REAL_METRICS_COMMIT_FILE = metrics_module._git_commit_file
_REAL_PROGRESS_COMMIT_FILE = progress_module._git_commit_file
_REAL_GIT_PUBLISH = publish_module._git_publish
_REAL_RUN_GIT = tfa_module._run_git


def _head_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        check=True,
    )
    return result.stdout.strip()


def test_guard_neutralises_every_push_capable_helper():
    """Every module that can push is no-oped at its low-level git helper."""
    assert empty_streak_module._git_commit_file is not _REAL_GIT_COMMIT_FILE
    assert mailer_module._git_commit_file is not _REAL_MAILER_COMMIT_FILE
    assert metrics_module._git_commit_file is not _REAL_METRICS_COMMIT_FILE
    assert progress_module._git_commit_file is not _REAL_PROGRESS_COMMIT_FILE
    assert publish_module._git_publish is not _REAL_GIT_PUBLISH
    assert tfa_module._run_git is not _REAL_RUN_GIT

    # The no-op runner looks like a clean tree, so publish/translate_fa bail
    # out before any real git command runs.
    assert publish_module._git_publish("2026-10-13").returncode == 0
    assert tfa_module._run_git("diff", "--cached", "--quiet").returncode == 0


def test_no_commit_on_empty_streak_path(tmp_path, monkeypatch, sample_config):
    """The streak path must not move HEAD and must not use real git."""
    before = _head_sha()

    state_file = tmp_path / "streak.json"
    state_file.write_text(json.dumps({
        "consecutive_empty": 0,
        "last_date": None,
        "last_empty_date": None,
        "notifications_sent": ["3", "7", "14", "28"],
        "stopped": False,
        "history": [],
    }))

    monkeypatch.setattr(
        empty_streak_module, "_send_owner_email", lambda *a, **k: None
    )

    config = sample_config.model_copy(deep=True)
    config.empty_brief = dict(config.empty_brief)
    config.empty_brief["state_file"] = str(state_file)
    config.empty_brief["notifications_sent"] = ["3", "7", "14", "28"]

    tz = get_timezone_offset(config.schedule.get("timezone", "America/Vancouver"))
    today = datetime.now(tz).strftime("%Y-%m-%d")

    empty_streak_module.update_streak_and_notify(False, today, config, str(tmp_path))

    # The guard is in force: the module helper is not the real function.
    assert empty_streak_module._git_commit_file is not _REAL_GIT_COMMIT_FILE

    # Calling it touches nothing.
    empty_streak_module._git_commit_file(str(state_file), "should be a no-op")

    assert _head_sha() == before
