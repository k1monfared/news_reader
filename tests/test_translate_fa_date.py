"""Tests for the Farsi-only regeneration driver (scripts/translate_fa_for_date.py)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.translate_fa_for_date as mod


class _FakeLLM:
    total_cost = 0.0
    prompt_versions_used: dict = {}


class _FakeHTTP:
    def __init__(self, *_a, **_k):
        self.closed = False

    def close(self):
        self.closed = True


def _config_with_site(sample_config, site_dir: Path):
    config = sample_config.model_copy(deep=True)
    config.publish["site_dir"] = str(site_dir)
    if not config.translate_fa:
        config.translate_fa = {}
    config.translate_fa["enabled"] = True
    return config


def _install_fakes(monkeypatch, config, recorder):
    monkeypatch.setattr(mod, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(mod, "AuditedLLMClient", lambda *a, **k: _FakeLLM())
    monkeypatch.setattr(mod, "AuditedHTTPClient", lambda *a, **k: _FakeHTTP())

    def fake_run(run_dir, cfg, llm, http):
        recorder["run_dir"] = run_dir
        return {"status": "published", "fa_post": "x"}

    monkeypatch.setattr(mod, "run_translate_fa", fake_run)


def test_publishes_farsi_for_existing_english(tmp_path, monkeypatch, sample_config):
    site = tmp_path / "site"
    (site / "_posts").mkdir(parents=True)
    (site / "_posts" / "2026-10-03-daily-brief.md").write_text(
        "---\nlayout: post\n---\n\nbody\n"
    )
    recorder: dict = {}
    _install_fakes(monkeypatch, _config_with_site(sample_config, site), recorder)
    data_dir = tmp_path / "data"
    monkeypatch.setattr(
        sys, "argv", ["x", "--date", "2026-10-03", "--data-dir", str(data_dir)]
    )

    assert mod.main() == 0
    assert "2026-10-03" in recorder["run_dir"]
    assert (Path(recorder["run_dir"]) / "run_meta.json").exists()


def test_missing_english_post_fails(tmp_path, monkeypatch, sample_config):
    site = tmp_path / "site"
    (site / "_posts").mkdir(parents=True)
    recorder: dict = {}
    _install_fakes(monkeypatch, _config_with_site(sample_config, site), recorder)
    monkeypatch.setattr(
        sys, "argv", ["x", "--date", "2026-01-01", "--data-dir", str(tmp_path / "data")]
    )

    assert mod.main() == 1
    assert "run_dir" not in recorder


def test_invalid_date_rejected(tmp_path, monkeypatch, sample_config):
    site = tmp_path / "site"
    (site / "_posts").mkdir(parents=True)
    _install_fakes(monkeypatch, _config_with_site(sample_config, site), {})
    monkeypatch.setattr(
        sys, "argv", ["x", "--date", "not-a-date", "--data-dir", str(tmp_path / "data")]
    )

    assert mod.main() == 2
