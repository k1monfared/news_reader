"""Regression tests for failures observed over the life of the pipeline.

Each test locks in a fix that was made in response to a real incident, or
verifies the automation added for a gap (account-funds 402, Farsi unclosed
tags, foreign-script leakage). Keeping these green means the incidents that
produced them cannot silently return.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm_client as llm_client_module
from llm_client import AuditedLLMClient
import stages.editorial as editorial_module
import stages.empty_streak as empty_streak_module
import stages.summarize as summarize_module
from stages.empty_streak import is_empty_brief, update_streak_and_notify
from stages.translate_fa import (
    _autoclose_unclosed_tags,
    _reasoning_leak_issues,
    _validate_fa_body,
)


# ---------------------------------------------------------------------------
# Fake HTTP transport (mirrors tests/test_failover.py)
# ---------------------------------------------------------------------------


class JsonResponse:
    def __init__(self, status_code=200, content="ok", body=None, invalid_json=False):
        self.status_code = status_code
        self._content = content
        self._body = body
        self._invalid_json = invalid_json
        self.text = json.dumps(body if body is not None else {"raw": True})

    def json(self):
        if self._invalid_json:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        if self._body is not None:
            return self._body
        return {
            "choices": [{"message": {"content": self._content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
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


def make_client(tmp_path, fallbacks, default="model-a"):
    return AuditedLLMClient(
        str(tmp_path / "run"),
        {"max_cost_per_run_usd": 1.0},
        {"default": default, "fallbacks": fallbacks},
    )


def _call(client, model="model-a"):
    return client.call(
        stage="test",
        prompt_name="p",
        prompt_version=1,
        system="s",
        user_message="u",
        model=model,
    )


# ---------------------------------------------------------------------------
# Account-level 402: free-model fallback (Oct 4 incident)
# ---------------------------------------------------------------------------


class TestAccountFundsFallback:
    def test_402_on_paid_falls_over_to_free_model(self, tmp_path, scripted_http):
        """A 402 (account out of funds) must skip the paid model and let a
        free model later in the chain answer."""
        http = scripted_http([
            JsonResponse(402, body={"error": {"message": "Insufficient account funds"}}),
            JsonResponse(200, content="free answer"),
        ])
        client = make_client(
            tmp_path,
            fallbacks=["longcat-2.5-preview-free"],
            default="deepseek-v4-flash",
        )

        out = _call(client, model="deepseek-v4-flash")

        assert out == "free answer"
        requested = [c["payload"]["model"] for c in http["calls"]]
        assert requested == ["deepseek-v4-flash", "longcat-2.5-preview-free"]

        entries = [
            json.loads(line)
            for line in (tmp_path / "run" / "audit" / "llm_calls.jsonl")
            .read_text()
            .splitlines()
            if line.strip()
        ]
        assert entries[-1]["model"] == "longcat-2.5-preview-free"
        assert entries[-1]["failed_over_from"] == ["deepseek-v4-flash"]

    def test_all_models_402_raises(self, tmp_path, scripted_http):
        """If every model is out of funds, the call raises (stage fails)."""
        scripted_http([
            JsonResponse(402, body={"error": {"message": "Insufficient account funds"}})
        ])
        client = make_client(tmp_path, ["deepseek-v4-pro"], default="deepseek-v4-flash")
        with pytest.raises(RuntimeError, match="all 2 models"):
            _call(client, model="deepseek-v4-flash")


# ---------------------------------------------------------------------------
# Malformed LLM responses (Sep 1 incident, run 33531731150)
# ---------------------------------------------------------------------------


class TestMalformedResponse:
    def test_missing_choices_retries_then_fails_over(self, tmp_path, scripted_http):
        """A 200 with no 'choices' is retryable, not a KeyError crash."""
        http = scripted_http([
            JsonResponse(200, body={"unexpected": "shape"}),
            JsonResponse(200, body={"unexpected": "shape"}),
            JsonResponse(200, body={"unexpected": "shape"}),
            JsonResponse(200, content="recovered"),
        ])
        client = make_client(tmp_path, ["model-b"])

        out = _call(client)

        assert out == "recovered"
        assert [c["payload"]["model"] for c in http["calls"]] == [
            "model-a", "model-a", "model-a", "model-b",
        ]

    def test_invalid_json_retries_then_fails_over(self, tmp_path, scripted_http):
        scripted_http([
            JsonResponse(200, invalid_json=True),
            JsonResponse(200, content="recovered"),
        ])
        client = make_client(tmp_path, ["model-b"])

        assert _call(client) == "recovered"


class TestEmptyContentAttempts:
    def test_empty_content_attempts_configurable(self, tmp_path, scripted_http):
        """llm_empty_content_attempts controls how many tries an empty reply gets."""
        http = scripted_http([
            JsonResponse(200, content=""),
            JsonResponse(200, content="recovered"),
        ])
        client = AuditedLLMClient(
            str(tmp_path / "run"),
            {"max_cost_per_run_usd": 1.0, "llm_empty_content_attempts": 2},
            {"default": "model-a", "fallbacks": ["model-b"]},
        )

        assert _call(client) == "recovered"
        assert [c["payload"]["model"] for c in http["calls"]] == ["model-a", "model-a"]


# ---------------------------------------------------------------------------
# Farsi translation: unclosed tags + foreign-script leakage (Oct 3 incident)
# ---------------------------------------------------------------------------


class TestFarsiAutoclose:
    # A realistic Farsi body: enough Persian text that the Latin tag
    # attributes do not dominate the latin-ratio heuristic.
    UNCLOSED_BODY = (
        "**تحول کلیدی:** امروز رویداد مهمی در منطقه رخ داد و منابع مختلف "
        "آن را پوشش دادند.\n\n"
        '<details markdown="block">\n'
        '<summary markdown="span">**خلاصه.** چرا مهم است. *(BBC)*\n\n'
        "زمینه: توضیح بیشتر درباره این رویداد و پیامدهای آن.\n\n"
    )

    def test_autoclose_closes_in_reverse_order(self):
        fixed, repairs = _autoclose_unclosed_tags(
            self.UNCLOSED_BODY, ["unclosed tag(s): details, summary"]
        )
        assert fixed.index("</summary>") < fixed.index("</details>")
        assert repairs == ["autoclosed unclosed tag(s): details, summary"]

        _, _, issues = _validate_fa_body(fixed, "", allow_autoclose=False)
        assert issues == []

    def test_validate_blocks_unclosed_until_autoclose(self):
        _, _, blocking = _validate_fa_body(
            self.UNCLOSED_BODY, "", allow_autoclose=False
        )
        assert any("unclosed tag" in i for i in blocking)

        _, repairs, blocking = _validate_fa_body(
            self.UNCLOSED_BODY, "", allow_autoclose=True
        )
        assert blocking == []
        assert any("autoclosed" in r for r in repairs)

    def test_autoclose_ignores_other_damage(self):
        body = "text with a stray </details>\n"
        fixed, repairs = _autoclose_unclosed_tags(body, ["unbalanced closing tag </details>"])
        assert fixed == body
        assert repairs == []

    def test_foreign_script_flagged(self):
        body = "ترجمه درست است. Лишний текст здесь."
        issues = _reasoning_leak_issues(body, "A long enough English body. " * 40)
        assert any("Cyrillic" in i for i in issues)

    def test_clean_translation_not_flagged(self):
        body = "**تحول کلیدی:** اتفاق افتاد. *(BBC)*\n\nزمینه: توضیح کوتاه.\n"
        assert _reasoning_leak_issues(body, "**Key development:** something. *(BBC)*") == []


# ---------------------------------------------------------------------------
# Editorial / summarize must not ship a silent unedited draft (Aug 21 fix)
# ---------------------------------------------------------------------------


class _RaisingClient:
    def call(self, **kwargs):
        raise RuntimeError("all models down")


class _FakeTemplate:
    version = 1

    def render(self, **kwargs):
        return "system", "user", self.version


class TestEditorialDegraded:
    def test_editorial_failure_records_degraded(self, tmp_path, monkeypatch):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "report.md").write_text("# Draft brief\n")

        monkeypatch.setattr(editorial_module, "load_prompt", lambda *a, **k: _FakeTemplate())

        result = editorial_module.run_editorial(
            str(run_dir), _config_stub(), _RaisingClient(), None
        )

        assert result["status"] == "completed"
        assert result["degraded"] is True
        assert (run_dir / "report_edited.md").read_text() == "# Draft brief\n"


class _FakeItem:
    included = True
    primary_category = "Other"
    fetch_id = "x1"
    source = "aljazeera"
    sole_source_flag = False
    story_status = "new"
    title_en = "Headline"
    text_en = "Body"
    source_url = "https://example.com"
    related_sources = []
    development_note = None
    story_timeline = []


class TestSummarizePropagates:
    def test_summarize_failure_raises(self, tmp_path, monkeypatch):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        monkeypatch.setattr(summarize_module, "_load_items", lambda p: [_FakeItem()])

        with pytest.raises(RuntimeError, match="all models down"):
            summarize_module.run_summarize(
                str(run_dir), _config_stub(), _RaisingClient(), None
            )


# ---------------------------------------------------------------------------
# Empty brief handling (Sep 1 fix)
# ---------------------------------------------------------------------------


class TestEmptyBrief:
    def test_zero_included_is_empty(self, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "filtered_items.json").write_text(
            json.dumps([{"included": False}, {"included": False}])
        )
        assert is_empty_brief(str(run_dir), str(tmp_path / "site"), "2026-09-01") is True

    def test_placeholder_report_is_empty(self, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "report.md").write_text("No significant developments reported today.\n")
        assert is_empty_brief(str(run_dir), str(tmp_path / "site"), "2026-09-01") is True

    def test_nonempty_is_not_empty(self, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "filtered_items.json").write_text(json.dumps([{"included": True}]))
        assert is_empty_brief(str(run_dir), str(tmp_path / "site"), "2026-09-01") is False

    def test_streak_notifies_on_day_three(self, tmp_path, monkeypatch, sample_config):
        state_file = tmp_path / "streak.json"
        state_file.write_text(json.dumps({
            "consecutive_empty": 2,
            "last_date": "2026-01-01",
            "last_empty_date": "2026-01-01",
            "notifications_sent": [],
            "stopped": False,
            "history": [],
        }))
        sent: dict = {}
        monkeypatch.setattr(
            empty_streak_module,
            "_send_owner_email",
            lambda *a, **k: sent.setdefault("called", True),
        )
        monkeypatch.setattr(empty_streak_module, "_git_commit_file", lambda *a, **k: None)

        config = sample_config.model_copy(deep=True)
        config.empty_brief = dict(config.empty_brief)
        config.empty_brief["state_file"] = str(state_file)

        result = update_streak_and_notify(True, "2026-01-02", config, str(tmp_path))

        assert result["consecutive_empty"] == 3
        assert result.get("notification_sent") == 3
        assert sent.get("called") is True


def _config_stub():
    from models import PipelineConfig

    return PipelineConfig(
        sources=[],
        models={},
        buckets=[],
        schedule={},
        budget={},
        pipeline={},
        publish={},
        paths={"prompts_dir": "prompts"},
    )
