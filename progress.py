"""Live run progress: a status file updated as each pipeline stage runs.

GitHub only exposes a job's log after it finishes, so mid-run visibility needs
a file that is written and pushed as stages complete. ``Progress`` writes
``docs/_data/run_status.json`` (served by the dashboard) and commits it after
each stage. Commits are best-effort: a push failure never fails the run.

Set ``progress.enabled: false`` in config.yaml to disable, or
``progress.commit: false`` to write the file without committing.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_commit_file(path: str, message: str) -> None:
    try:
        subprocess.run(
            ["git", "add", path], check=False, capture_output=True, timeout=30
        )
        staged = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], capture_output=True, timeout=30
        )
        if staged.returncode == 0:
            return
        subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True, text=True, timeout=30,
        )
        subprocess.run(
            ["git", "push", "origin", "master"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as exc:  # noqa: BLE001 - progress must never break a run
        logger.warning(f"Progress commit failed: {exc}")


class Progress:
    """Track pipeline stage progress in a committed status file."""

    def __init__(
        self,
        run_id: str,
        target_date: str,
        stage_order: list[str],
        status_file: str = "docs/_data/run_status.json",
        commit: bool = True,
        enabled: bool = True,
    ) -> None:
        self.run_id = run_id
        self.target_date = target_date
        self.stage_order = list(stage_order)
        self.path = Path(status_file)
        self.commit = commit
        self.enabled = enabled
        self.started_at = _now()
        self.status = "running"
        self.current: str | None = None
        self.stages: list[dict] = []

    # ------------------------------------------------------------------
    def _write(self) -> None:
        if not self.enabled:
            return
        payload = {
            "run_id": self.run_id,
            "target_date": self.target_date,
            "status": self.status,
            "started_at": self.started_at,
            "updated_at": _now(),
            "current_stage": self.current,
            "total_stages": len(self.stage_order),
            "stages": self.stages,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            logger.warning(f"Could not write progress file: {exc}")
            return
        if self.commit:
            _git_commit_file(
                str(self.path),
                f"Run progress: {self.run_id} "
                f"{self.current or self.status}",
            )

    def _set(self, name: str, status: str, duration_s=None, error=None) -> None:
        for stage in reversed(self.stages):
            if stage["name"] == name:
                stage["status"] = status
                if duration_s is not None:
                    stage["duration_s"] = duration_s
                if error:
                    stage["error"] = error
                stage["finished_at"] = _now()
                return
        self.stages.append({
            "name": name,
            "status": status,
            "duration_s": duration_s,
            "error": error,
            "finished_at": _now(),
        })

    # ------------------------------------------------------------------
    def start(self) -> None:
        self.current = self.stage_order[0] if self.stage_order else None
        self._write()

    def stage_done(
        self, name: str, status: str = "completed", duration_s=None, error=None
    ) -> None:
        self._set(name, status, duration_s, error)
        idx = self.stage_order.index(name) if name in self.stage_order else -1
        self.current = (
            self.stage_order[idx + 1] if 0 <= idx < len(self.stage_order) - 1 else None
        )
        self._write()

    def finish(self, status: str = "completed") -> None:
        self.status = status
        self.current = None
        self._write()
