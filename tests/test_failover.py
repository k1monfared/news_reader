"""Tests for runtime model failover, decoupled EN/FA deploys, and
critical-stage exit codes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm_client as llm_client_module
from llm_client import AuditedLLMClient, ModelUnavailableError
import run_pipeline as rp_module
from run_pipeline import critical_failures


# ---------------------------------------------------------------------------
# Fake httpx transport
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code=200, content="ok", text=None):
        self.status_code = status_code
        self._content = content
        self.text = text if text is not None else json.dumps({"raw": True})

    def json(self):
        return {
            "choices": [
                {
                    "message": {"content": self._content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }


@pytest.fixture(autouse=True)
def fake_api_key(monkeypatch):
    """AuditedLLMClient refuses to start without a key; use a dummy one."""
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key-dummy")


@pytest.fixture
def scripted_http(monkeypatch):
    """Patch llm_client.httpx.Client to serve a scripted response sequence.

    Each script entry is a FakeResponse or an exception instance, consumed
    one per POST call; the last entry repeats once exhausted. Returns a
    holder whose ``calls`` list records every POST payload.
    """
    state: dict = {"calls": []}

    def install(script):
        state["calls"] = []
        idx = {"i": 0}

        class ScriptedClient:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def post(self, url, headers=None, json=None):
                state["calls"].append({"url": url, "payload": json})
                item = script[min(idx["i"], len(script) - 1)]
                idx["i"] += 1
                if isinstance(item, Exception):
                    raise item
                return item

        monkeypatch.setattr(
            llm_client_module.httpx, "Client", lambda *a, **k: ScriptedClient()
        )
        monkeypatch.setattr(llm_client_module.time, "sleep", lambda s: None)
        return state

    return install


def make_client(tmp_path, fallbacks):
    return AuditedLLMClient(
        str(tmp_path / "run"),
        {"max_cost_per_run_usd": 1.0},
        {"default": "model-a", "fallbacks": fallbacks},
    )


def read_calls_jsonl(run_dir: Path) -> list[dict]:
    lines = (run_dir / "audit" / "llm_calls.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# ---------------------------------------------------------------------------
# Failover behavior
# ---------------------------------------------------------------------------


class TestFailover:
    def test_permanent_error_fails_over_to_next_model(self, tmp_path, scripted_http):
        """A 400 from the primary must skip straight to the fallback."""
        http = scripted_http([
            FakeResponse(400, text='{"error": "Model is unavailable"}'),
            FakeResponse(200, content="hello"),
        ])
        client = make_client(tmp_path, ["model-b"])

        out = client.call(
            stage="summarize",
            prompt_name="p",
            prompt_version=1,
            system="s",
            user_message="u",
            model="model-a",
        )

        assert out == "hello"
        requested = [c["payload"]["model"] for c in http["calls"]]
        assert requested == ["model-a", "model-b"]

        entries = read_calls_jsonl(tmp_path / "run")
        assert entries[-1]["model"] == "model-b"
        assert entries[-1]["failed_over_from"] == ["model-a"]

        outputs = list((tmp_path / "run" / "audit" / "llm_outputs").glob("*.json"))
        saved = json.loads(outputs[-1].read_text())
        assert saved["failed_over_from"][0]["model"] == "model-a"
        assert "unavailable" in saved["failed_over_from"][0]["error"].lower()

    def test_exhausted_retries_fail_over_to_next_model(self, tmp_path, scripted_http):
        """Persistent 429s on the primary exhaust retries, then fall over."""
        http = scripted_http([
            # Three attempts against model-a all rate limited...
            FakeResponse(429, text='{"error": "Rate limit exceeded"}'),
            FakeResponse(429, text='{"error": "Rate limit exceeded"}'),
            FakeResponse(429, text='{"error": "Rate limit exceeded"}'),
            # ...then model-b answers.
            FakeResponse(200, content="fallback answer"),
        ])
        client = make_client(tmp_path, ["model-b"])

        out = client.call(
            stage="filter",
            prompt_name="p",
            prompt_version=1,
            system="s",
            user_message="u",
            model="model-a",
        )

        assert out == "fallback answer"
        requested = [c["payload"]["model"] for c in http["calls"]]
        assert requested == ["model-a"] * 3 + ["model-b"]
        entries = read_calls_jsonl(tmp_path / "run")
        assert entries[-1]["failed_over_from"] == ["model-a"]

    def test_empty_content_retries_then_fails_over(self, tmp_path, scripted_http):
        """Empty content (reasoning-only responses) retries, then fails over."""
        http = scripted_http([
            FakeResponse(200, content=""),
            FakeResponse(200, content="   "),
            FakeResponse(200, content=""),
            FakeResponse(200, content="recovered"),
        ])
        client = make_client(tmp_path, ["model-b"])

        out = client.call(
            stage="editorial",
            prompt_name="p",
            prompt_version=1,
            system="s",
            user_message="u",
            model="model-a",
        )

        assert out == "recovered"
        entries = read_calls_jsonl(tmp_path / "run")
        assert entries[-1]["model"] == "model-b"

    def test_all_models_failing_raises(self, tmp_path, scripted_http):
        """When every model in the chain fails, a RuntimeError surfaces."""
        scripted_http([FakeResponse(400, text="unavailable")])
        client = make_client(tmp_path, ["model-b", "model-c"])

        with pytest.raises(RuntimeError, match="all 3 models"):
            client.call(
                stage="summarize",
                prompt_name="p",
                prompt_version=1,
                system="s",
                user_message="u",
                model="model-a",
            )

    def test_chain_deduplicates_primary(self, tmp_path, scripted_http):
        """The primary appearing in fallbacks must not be tried twice."""
        http = scripted_http([
            FakeResponse(400, text="unavailable"),
            FakeResponse(200, content="ok"),
        ])
        client = make_client(tmp_path, ["model-b", "model-a", "model-c"])
        # model-c never reached because model-b answers on first try.
        client.call(
            stage="summarize",
            prompt_name="p",
            prompt_version=1,
            system="s",
            user_message="u",
            model="model-a",
        )
        requested = [c["payload"]["model"] for c in http["calls"]]
        assert requested == ["model-a", "model-b"]

    def test_default_model_used_when_none_passed(self, tmp_path, scripted_http):
        """Callers that omit `model` get models.default as the chain head."""
        http = scripted_http([FakeResponse(200, content="ok")])
        client = make_client(tmp_path, ["model-b"])
        client.call(
            stage="translate",
            prompt_name="p",
            prompt_version=1,
            system="s",
            user_message="u",
        )
        assert http["calls"][0]["payload"]["model"] == "model-a"


# ---------------------------------------------------------------------------
# Decoupled deploys
# ---------------------------------------------------------------------------


class TestDecoupledDeploys:
    def _write_report(self, run_dir: Path) -> None:
        (run_dir / "report.md").write_text("# Draft\n")

    def test_publish_commits_even_when_translate_fa_enabled(
        self, tmp_path, monkeypatch, sample_config
    ):
        """publish must push English on its own; no deferred 'staged' status."""
        import stages.publish as publish_module

        run_id = "2026-08-21-080000"
        run_dir = tmp_path / run_id
        run_dir.mkdir()
        self._write_report(run_dir)

        captured: dict = {}
        monkeypatch.setattr(
            publish_module, "_git_publish", lambda date_str: captured.update(date=date_str)
        )
        config = sample_config.model_copy(deep=True)
        config.translate_fa["enabled"] = True
        # Keep the post write out of the real docs/ tree.
        site_dir = tmp_path / "site"
        config.publish["site_dir"] = str(site_dir)

        result = publish_module.run_publish(str(run_dir), config, None, None)

        assert result["status"] == "published"
        assert captured["date"] == run_id[:10]
        assert (site_dir / "_posts" / f"{run_id[:10]}-daily-brief.md").exists()

    def test_translate_fa_commits_only_farsi_paths(self, monkeypatch, sample_config):
        """translate_fa git add/commit must cover Farsi artifacts only."""
        import stages.translate_fa as tfa_module

        recorded: list[list[str]] = []

        def fake_git(*args):
            recorded.append(list(args))
            # `diff --cached --quiet` returning 1 means staged changes exist,
            # so the commit and push paths actually run.
            if args[0] == "diff":
                return _rc(1)
            return _rc(0)

        monkeypatch.setattr(tfa_module, "_run_git", fake_git)

        tfa_module._git_commit_push("2026-08-21")

        adds = [r for r in recorded if r[0] == "add"]
        assert adds == [["add", "docs/_fa_posts/", "docs/_data/source_biases_fa.json"]]
        commits = [r for r in recorded if r[0] == "commit"]
        assert commits and commits[0][-1] == "Daily brief: 2026-08-21 (fa)"
        pushes = [r for r in recorded if r[0] == "push"]
        assert pushes == [["push", "origin", "master"]]

    def test_translate_fa_uses_own_model_key(self, tmp_path, monkeypatch, sample_config):
        """Translation reads models.translate_fa before falling back to default."""
        import stages.translate_fa as tfa_module

        seen: dict = {}

        class FakeTemplate:
            version = 3

            def render(self, **kwargs):
                return "system", kwargs.get("brief_markdown", ""), self.version

        monkeypatch.setattr(tfa_module, "load_prompt", lambda name, d=None: FakeTemplate())

        class RecordingClient:
            def call(self, **kwargs):
                seen.update(kwargs)
                return "ترجمه فارسی"

        config = sample_config.model_copy(deep=True)
        config.models["translate_fa"] = "farsi-special-model"

        body = tfa_module._translate_brief(
            "## Military Operations\n- bullet",
            {"Military Operations": "عملیات"},
            RecordingClient(),
            config,
        )
        assert body == "ترجمه فارسی\n"
        assert seen["model"] == "farsi-special-model"
        assert seen["stage"] == "translate_fa"

        # Without the override it falls back to default.
        del config.models["translate_fa"]
        tfa_module._translate_brief("body", {}, RecordingClient(), config)
        assert seen["model"] == config.models["default"]


def _rc(code):
    class R:
        returncode = code
        stderr = ""
        stdout = ""
    return R()


# ---------------------------------------------------------------------------
# Critical-stage exit codes
# ---------------------------------------------------------------------------


class TestCriticalFailures:
    def _write_meta(self, data_dir: Path, run_id: str, stages: dict) -> None:
        run_path = data_dir / "runs" / run_id
        run_path.mkdir(parents=True, exist_ok=True)
        meta = {
            "run_id": run_id,
            "started_at": "2026-08-21T08:00:00-07:00",
            "finished_at": "2026-08-21T08:15:00-07:00",
            "errors": [],
            "stages": stages,
        }
        (run_path / "run_meta.json").write_text(json.dumps(meta))

    def test_failed_critical_stage_listed(self, tmp_path):
        self._write_meta(tmp_path, "2026-08-21-080000", {
            "publish": {"status": "failed"},
            "mailer": {"status": "failed"},
        })
        failed = critical_failures(str(tmp_path), "2026-08-21-080000")
        assert failed == ["publish"]

    def test_farsi_failure_isolated(self, tmp_path):
        self._write_meta(tmp_path, "2026-08-21-080000", {
            "publish": {"status": "completed"},
            "translate_fa": {"status": "failed"},
        })
        failed = critical_failures(str(tmp_path), "2026-08-21-080000")
        assert failed == ["translate_fa"]

    def test_healthy_run_has_no_failures(self, tmp_path):
        stages = {name: {"status": "completed"} for name in sorted(rp_module.CRITICAL_STAGES)}
        self._write_meta(tmp_path, "2026-08-21-080000", stages)
        assert critical_failures(str(tmp_path), "2026-08-21-080000") == []

    def test_missing_meta_treated_as_total_failure(self, tmp_path):
        failed = critical_failures(str(tmp_path), "2026-08-21-080000")
        assert failed == sorted(rp_module.CRITICAL_STAGES)


# ---------------------------------------------------------------------------
# ModelUnavailableError semantics
# ---------------------------------------------------------------------------


class TestModelUnavailableError:
    def test_is_runtime_error(self):
        """So legacy `except RuntimeError` handlers still catch it."""
        assert issubclass(ModelUnavailableError, RuntimeError)
