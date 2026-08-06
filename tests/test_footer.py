"""Tests for the model footer appended to published posts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stages.publish import _models_used, build_model_footer


class TestModelsUsed:
    def test_models_from_audit_in_order(self, tmp_path, sample_config):
        """Distinct models appear in first-call order from the audit log."""
        run_dir = tmp_path / "run"
        (run_dir / "audit").mkdir(parents=True)
        calls = run_dir / "audit" / "llm_calls.jsonl"
        calls.write_text(
            "\n".join(
                json.dumps({"model": m})
                for m in ("deepseek-v4-flash-free", "gemini-2.5-pro", "deepseek-v4-flash-free")
            )
        )
        assert _models_used(str(run_dir), sample_config) == [
            "deepseek-v4-flash-free",
            "gemini-2.5-pro",
        ]

    def test_empty_audit_falls_back_to_config(self, tmp_path, sample_config):
        """Missing audit log falls back to the config default model."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        assert _models_used(str(run_dir), sample_config) == ["deepseek-v4-flash-free"]

    def test_missing_audit_file(self, tmp_path, sample_config):
        """No audit directory at all still yields the config default."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        assert _models_used(str(run_dir), sample_config) == ["deepseek-v4-flash-free"]


class TestBuildModelFooter:
    def test_footer_format(self, tmp_path, sample_config):
        """Footer renders the date and model line."""
        run_dir = tmp_path / "run" / "2026-08-06-080000"
        (run_dir / "audit").mkdir(parents=True)
        (run_dir / "audit" / "llm_calls.jsonl").write_text(
            json.dumps({"model": "deepseek-v4-flash-free"}) + "\n"
        )
        footer = build_model_footer(str(run_dir), sample_config)
        assert footer == "\n---\n\n*Generated on 2026-08-06 using deepseek-v4-flash-free*\n"
