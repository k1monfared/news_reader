"""Tests for the free-model selection loop.

Covers the pure selection logic in scripts/select_models.py and the runtime
that consumes models.json (AuditedLLMClient preferring the selected free
models before the static config chain, switching base per entry).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm_client as llm_client_module
from llm_client import AuditedLLMClient
from scripts.select_models import (
    _account_or_transport_failure,
    candidates_for_base,
    select_from_probes,
    write_models_json,
)

GO = "https://opencode.ai/zen/go/v1"
ZEN = "https://opencode.ai/zen/v1"


# ---------------------------------------------------------------------------
# Pure selection logic
# ---------------------------------------------------------------------------


class TestSelection:
    def test_keeps_only_ok_chat_models(self):
        probes = [
            {"id": "free-a", "base_url": GO, "outcome": "OK", "route": "chat/completions"},
            {"id": "free-b", "base_url": GO, "outcome": "GATED", "route": "chat/completions"},
            {"id": "free-c", "base_url": ZEN, "outcome": "EMPTY_CONTENT", "route": "chat/completions"},
        ]
        assert select_from_probes(probes) == [{"id": "free-a", "base_url": GO}]

    def test_skips_responses_only_models(self):
        probes = [
            {"id": "free-r", "base_url": GO, "outcome": "OK", "route": "responses"},
            {"id": "free-c", "base_url": GO, "outcome": "OK", "route": "chat/completions"},
        ]
        selected = select_from_probes(probes)
        assert selected == [{"id": "free-c", "base_url": GO}]

    def test_deduplicates(self):
        probes = [
            {"id": "free-a", "base_url": GO, "outcome": "OK", "route": "chat/completions"},
            {"id": "free-a", "base_url": GO, "outcome": "OK", "route": "chat/completions"},
        ]
        assert len(select_from_probes(probes)) == 1

    def test_dedup_by_id_across_bases(self):
        """A model served on both catalogs is kept once, on the first base."""
        probes = [
            {"id": "space-bunny-free", "base_url": GO, "outcome": "OK", "route": "chat/completions"},
            {"id": "space-bunny-free", "base_url": ZEN, "outcome": "OK", "route": "chat/completions"},
        ]
        assert select_from_probes(probes) == [{"id": "space-bunny-free", "base_url": GO}]

    def test_seeds_when_catalog_empty(self):
        assert "longcat-2.5-preview-free" in candidates_for_base(GO, [])
        assert "big-pickle" in candidates_for_base(ZEN, [])

    def test_live_ids_put_alongside_seeds(self):
        candidates = candidates_for_base(GO, ["new-free", "paid-model"])
        assert "new-free" in candidates
        assert "paid-model" not in candidates
        assert "space-bunny-free" in candidates

    def test_write_round_trip(self, tmp_path):
        out = tmp_path / "models.json"
        write_models_json(
            str(out),
            [GO],
            [{"id": "free-a", "base_url": GO}],
            [{"id": "free-a", "base_url": GO, "outcome": "OK", "route": "chat/completions"}],
        )
        data = json.loads(out.read_text())
        assert data["selected"] == [{"id": "free-a", "base_url": GO}]
        assert data["bases_probed"] == [GO]

    def test_account_or_transport_failure_detected(self):
        assert _account_or_transport_failure("HTTP_401_OTHER")
        assert _account_or_transport_failure("HTTP_402_OTHER")
        assert _account_or_transport_failure("HTTP_-1_OTHER")
        assert not _account_or_transport_failure("GATED")
        assert not _account_or_transport_failure("OK")

    def test_main_preserves_file_on_key_rejection(self, tmp_path, monkeypatch):
        import scripts.select_models as sm

        out = tmp_path / "models.json"
        out.write_text(json.dumps({"selected": [{"id": "old", "base_url": GO}]}))
        monkeypatch.setenv("OPENCODE_API_KEY", "fake")
        monkeypatch.setattr(
            sm,
            "probe_base",
            lambda *a, **k: ([
                {
                    "id": "free-a",
                    "base_url": GO,
                    "route": "chat/completions",
                    "outcome": "HTTP_401_OTHER",
                    "status": 401,
                }
            ], True),
        )
        monkeypatch.setattr(sys, "argv", ["select_models.py", "--out", str(out)])

        assert sm.main() == 3
        assert json.loads(out.read_text())["selected"][0]["id"] == "old"


# ---------------------------------------------------------------------------
# Runtime consumption of models.json
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, status_code=200, content="ok"):
        self.status_code = status_code
        self._content = content
        self.text = json.dumps({"raw": True})

    def json(self):
        return {
            "choices": [{"message": {"content": self._content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }


@pytest.fixture(autouse=True)
def fake_api_key(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key-dummy")


@pytest.fixture
def scripted_http(monkeypatch):
    state: dict = {"calls": []}

    def install(script):
        state["calls"] = []
        idx = {"i": 0}

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def post(self, url, headers=None, json=None):
                state["calls"].append({"url": url, "model": json["model"]})
                item = script[min(idx["i"], len(script) - 1)]
                idx["i"] += 1
                return item

        monkeypatch.setattr(
            llm_client_module.httpx, "Client", lambda *a, **k: Client()
        )
        monkeypatch.setattr(llm_client_module.time, "sleep", lambda s: None)
        return state

    return install


def _make_client(tmp_path, selected_file, default="paid-model", fallbacks=None):
    return AuditedLLMClient(
        str(tmp_path / "run"),
        {"max_cost_per_run_usd": 1.0},
        {
            "default": default,
            "fallbacks": fallbacks or [],
            "selected_models_file": str(selected_file),
        },
    )


def _call(client):
    return client.call(
        stage="test", prompt_name="p", prompt_version=1, system="s", user_message="u"
    )


class TestRuntimeSelection:
    def test_selected_free_model_tried_first_on_its_base(
        self, tmp_path, monkeypatch, scripted_http
    ):
        monkeypatch.setenv("OPENCODE_API_BASE_URL", "https://paid.example/v1")
        selected_file = tmp_path / "models.json"
        selected_file.write_text(json.dumps({
            "selected": [{"id": "longcat-2.5-preview-free", "base_url": GO}]
        }))
        http = scripted_http([_Resp(200, content="free answer")])

        out = _call(_make_client(tmp_path, selected_file))

        assert out == "free answer"
        assert http["calls"][0] == {
            "url": f"{GO}/chat/completions",
            "model": "longcat-2.5-preview-free",
        }

    def test_falls_back_to_configured_primary_base(
        self, tmp_path, monkeypatch, scripted_http
    ):
        monkeypatch.setenv("OPENCODE_API_BASE_URL", "https://paid.example/v1")
        selected_file = tmp_path / "models.json"
        selected_file.write_text(json.dumps({
            "selected": [{"id": "longcat-2.5-preview-free", "base_url": GO}]
        }))
        http = scripted_http([_Resp(400), _Resp(200, content="paid answer")])

        out = _call(_make_client(tmp_path, selected_file))

        assert out == "paid answer"
        assert http["calls"] == [
            {"url": f"{GO}/chat/completions", "model": "longcat-2.5-preview-free"},
            {"url": "https://paid.example/v1/chat/completions", "model": "paid-model"},
        ]

    def test_missing_selected_file_uses_static_chain(
        self, tmp_path, monkeypatch, scripted_http
    ):
        monkeypatch.setenv("OPENCODE_API_BASE_URL", "https://paid.example/v1")
        http = scripted_http([_Resp(200, content="paid answer")])

        out = _call(_make_client(tmp_path, tmp_path / "does-not-exist.json"))

        assert out == "paid answer"
        assert http["calls"] == [
            {"url": "https://paid.example/v1/chat/completions", "model": "paid-model"},
        ]

    def test_malformed_selected_file_is_ignored(
        self, tmp_path, monkeypatch, scripted_http
    ):
        monkeypatch.setenv("OPENCODE_API_BASE_URL", "https://paid.example/v1")
        selected_file = tmp_path / "models.json"
        selected_file.write_text("{not valid json")
        scripted_http([_Resp(200, content="paid answer")])

        assert _call(_make_client(tmp_path, selected_file)) == "paid answer"

    def test_paid_first_stage_orders_paid_before_free(
        self, tmp_path, monkeypatch, scripted_http
    ):
        monkeypatch.setenv("OPENCODE_API_BASE_URL", "https://paid.example/v1")
        selected_file = tmp_path / "models.json"
        selected_file.write_text(json.dumps({
            "selected": [{"id": "longcat-2.5-preview-free", "base_url": GO}]
        }))
        http = scripted_http([_Resp(200, content="paid answer")])
        client = AuditedLLMClient(
            str(tmp_path / "run"),
            {"max_cost_per_run_usd": 1.0},
            {
                "default": "paid-model",
                "fallbacks": [],
                "selected_models_file": str(selected_file),
                "paid_first_stages": ["editorial"],
            },
        )

        out = client.call(
            stage="editorial",
            prompt_name="p",
            prompt_version=1,
            system="s",
            user_message="u",
        )

        assert out == "paid answer"
        assert http["calls"][0] == {
            "url": "https://paid.example/v1/chat/completions",
            "model": "paid-model",
        }
