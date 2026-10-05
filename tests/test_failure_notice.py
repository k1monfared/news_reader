"""Tests for the post-failure notification summary (scripts/failure_notice.py).

GitHub's built-in failure email carries no log summary, so the workflow runs
failure_notice.py on failure. These tests cover the pure summary/rendering
logic and the no-key guard without any network access.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.failure_notice import (
    _extract_error_excerpt,
    render_markdown,
    render_text,
    send_failure_email,
    summarize,
)


def _write_meta(data_dir: Path, run_id: str, stages: dict, errors: list[str]) -> None:
    run_path = data_dir / "runs" / run_id
    run_path.mkdir(parents=True, exist_ok=True)
    (run_path / "run_meta.json").write_text(json.dumps({
        "run_id": run_id,
        "started_at": "2026-10-04T09:44:00-07:00",
        "errors": errors,
        "stages": stages,
    }))


LOG = """\
2026-10-04 16:44:00,076 [INFO] pipeline: Running stage: summarize
2026-10-04 16:44:35,248 [ERROR] pipeline: Stage summarize failed: all 2 models failed
Traceback (most recent call last):
  File "/app/stages/summarize.py", line 129, in run_summarize
    report_body = llm_client.call(
RuntimeError: Stage summarize: all 2 models in failover chain failed
2026-10-04 16:44:36,513 [ERROR] pipeline: Pipeline finished with critical failures
"""


class TestSummarize:
    def test_picks_failed_critical_stage(self, tmp_path):
        data_dir = tmp_path / "data"
        _write_meta(
            data_dir,
            "2026-10-04-094400",
            {
                "summarize": {"status": "failed", "error": "all 2 models failed"},
                "publish": {"status": "skipped"},
            },
            ["summarize: all 2 models failed"],
        )

        summary = summarize(str(data_dir), LOG)

        assert summary["run_id"] == "2026-10-04-094400"
        assert summary["failed_critical"] == ["summarize"]
        assert "summarize" in summary["summary_line"]
        assert summary["excerpt"]

    def test_missing_meta_falls_back_to_log(self, tmp_path):
        summary = summarize(str(tmp_path / "data"), LOG)

        assert summary["run_id"] is None
        assert "run_meta" in summary["summary_line"].lower()
        assert any("Traceback" in line for line in summary["excerpt"])

    def test_excerpt_is_deduplicated(self):
        excerpt = _extract_error_excerpt(LOG)
        assert excerpt.count("Traceback (most recent call last):") == 1
        assert any("RuntimeError" in line for line in excerpt)

    def test_non_critical_failure_labeled(self, tmp_path):
        data_dir = tmp_path / "data"
        _write_meta(
            data_dir,
            "2026-10-04-094400",
            {"editorial": {"status": "failed", "error": "boom"}},
            ["editorial: boom"],
        )
        summary = summarize(str(data_dir), "")
        assert summary["failed_critical"] == []
        assert "editorial" in summary["summary_line"]


class TestRendering:
    def _summary(self, tmp_path):
        data_dir = tmp_path / "data"
        _write_meta(
            data_dir,
            "2026-10-04-094400",
            {"summarize": {"status": "failed", "error": "insufficient funds"}},
            ["summarize: insufficient funds"],
        )
        return summarize(str(data_dir), LOG)

    def test_text_contains_run_and_errors(self, tmp_path):
        text = render_text(self._summary(tmp_path), "https://example.com/runs/1")
        assert "2026-10-04-094400" in text
        assert "insufficient funds" in text
        assert "https://example.com/runs/1" in text

    def test_markdown_contains_stage_and_excerpt(self, tmp_path):
        md = render_markdown(self._summary(tmp_path), None)
        assert "summarize" in md
        assert "RuntimeError" in md


class TestEmailGuard:
    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="RESEND_API_KEY"):
            send_failure_email("owner@example.com", "from@example.com", "s", "t", "m")
