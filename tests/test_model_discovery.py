"""Tests for live free-model discovery and the models.json fallback cache."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import model_discovery
from model_discovery import discover_free_models, refresh_models_file

GO = "https://opencode.ai/zen/go/v1"


def _ok(content="ok"):
    return (200, json.dumps({"choices": [{"message": {"content": content}}]}))


class TestDiscover:
    def test_keeps_only_ok_free_models(self, monkeypatch):
        monkeypatch.setattr(
            model_discovery,
            "discover",
            lambda base: ["alpha-free", "beta-free", "gamma-free", "paid-x"],
        )
        responses = {
            "alpha-free": _ok("ok"),
            "beta-free": (403, json.dumps({"error": {"message": "MissingSessionID", "type": "x"}})),
            "gamma-free": (200, json.dumps({"choices": [{"message": {"content": ""}}]})),
        }
        monkeypatch.setattr(
            model_discovery,
            "probe_chat",
            lambda base, key, model, session_id="", max_tokens=5, timeout_s=10: responses[model],
        )
        assert discover_free_models(GO, "k") == [{"id": "alpha-free", "base_url": GO}]

    def test_catalog_failure_returns_empty(self, monkeypatch):
        def boom(base):
            raise RuntimeError("dns failure")

        monkeypatch.setattr(model_discovery, "discover", boom)
        assert discover_free_models(GO, "k") == []

    def test_empty_key_returns_empty(self):
        assert discover_free_models(GO, "") == []

    def test_probe_exception_skips_model(self, monkeypatch):
        monkeypatch.setattr(model_discovery, "discover", lambda base: ["alpha-free", "beta-free"])

        def probe(base, key, model, session_id="", max_tokens=5, timeout_s=10):
            if model == "alpha-free":
                raise RuntimeError("timeout")
            return _ok("ok")

        monkeypatch.setattr(model_discovery, "probe_chat", probe)
        assert discover_free_models(GO, "k") == [{"id": "beta-free", "base_url": GO}]


class TestRefreshFile:
    def test_writes_when_nonempty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            model_discovery,
            "discover_free_models",
            lambda *a, **k: [{"id": "free-a", "base_url": GO}],
        )
        out = tmp_path / "models.json"
        refresh_models_file(str(out), GO, "k")
        data = json.loads(out.read_text())
        assert data["selected"] == [{"id": "free-a", "base_url": GO}]
        assert data["bases_probed"] == [GO]

    def test_leaves_file_when_empty(self, tmp_path, monkeypatch):
        out = tmp_path / "models.json"
        out.write_text(json.dumps({"selected": [{"id": "old", "base_url": GO}]}))
        monkeypatch.setattr(model_discovery, "discover_free_models", lambda *a, **k: [])
        refresh_models_file(str(out), GO, "k")
        assert json.loads(out.read_text())["selected"][0]["id"] == "old"


class TestConfig:
    def test_primary_is_deepseek_v41_flash(self):
        import yaml
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        cfg = yaml.safe_load((root / "config.yaml").read_text())
        assert cfg["models"]["default"] == "deepseek-v4.1-flash"
        assert cfg["models"]["translate_fa"] == "deepseek-v4.1-flash"
        assert cfg["models"]["fallbacks"] == []
        assert "paid_first_stages" not in cfg["models"]
