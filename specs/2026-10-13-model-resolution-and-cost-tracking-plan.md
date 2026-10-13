# Model Resolution and Daily Cost Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the pipeline on `deepseek-v4.1-flash` as primary, discover free models live from the Go catalog each run as the backstop, and track and display dollar cost per run and per day.

**Architecture:** `run_pipeline` resolves the model chain before it builds `AuditedLLMClient`. A new `model_discovery.py` fetches the catalog, probes free models, and writes `models.json` as a fallback cache. The client then tries the paid primary first, then the discovered free models. Cost is priced per served model from a flat peak-rate table, aggregated in the metrics record, and shown on the dashboard.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest, Jekyll + Liquid (dashboard), GitHub Actions (cron).

**Spec:** `specs/2026-10-13-model-resolution-and-cost-tracking-design.md`

## Global Constraints

- Python 3.11 or newer, Pydantic v2.
- `from __future__ import annotations` at the top of every new or modified Python file.
- Tests never touch the network. Mock HTTP and the catalog.
- Run `pytest tests/` before every commit. Commit only when it passes.
- Primary model id is exactly `deepseek-v4.1-flash`.
- Free backstop only. No paid models other than the primary.
- Cost rates are flat peak rates: `deepseek-v4.1-flash` $0.30 in, $1.20 out per million tokens. Free models are absent from the table and price at $0.
- Daily cron is `7 13 * * *` (6:07am PDT).

## Review Focus

- **Catalog reachable but zero free models answer:** discovery returns an empty list and `models.json` must stay as it was, so the last known good set remains the fallback. (Task 3 test.)
- **Catalog fetch raises (DNS, timeout, 5xx):** discovery must return an empty list and never raise into the run. (Task 1 test.)
- **A model id absent from the pricing table:** cost is `$0` and no error is raised. (Task 4 test.)
- **A pricing entry missing its output rate:** cost computation must not raise `KeyError`. (Task 4 test.)
- **A run with no recorded cost (older run, or a test with a null client):** `cost_usd` is `0`, not `None` or a missing key. (Task 5 test.)

---

### Task 1: Free-model discovery module

**Files:**
- Create: `model_discovery.py`
- Modify: `scripts/probe_models.py` (add an optional `timeout_s` to `_http` and `probe_chat`)
- Test: `tests/test_model_discovery.py`

**Interfaces:**
- Consumes: `scripts.probe_models.discover(base) -> list[str]`, `scripts.probe_models.probe_chat(base, key, model, session_id="", max_tokens=5, timeout_s=60) -> tuple[int, str]`, `scripts.probe_models.classify(status, body, route) -> tuple[str, bool]`, `scripts.select_models.write_models_json(path, bases, selected, probes) -> None`.
- Produces:
  - `discover_free_models(base_url: str, api_key: str, session_id: str = "", *, timeout_s: int = 10) -> list[dict]`
  - `refresh_models_file(path: str, base_url: str, api_key: str, session_id: str = "", *, timeout_s: int = 10) -> list[dict]`

- [ ] **Step 1: Add an optional timeout to the probe helpers**

In `scripts/probe_models.py`, change `_http` and `probe_chat` to accept a timeout, defaulting to the existing constant so behavior is unchanged.

```python
def _http(method: str, url: str, headers: dict, payload: dict | None, timeout_s: int = TIMEOUT_S) -> tuple[int, str]:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"User-Agent": "news_reader-phase0-probe/1.0", **headers}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body
    except Exception as e:  # network failure, DNS, timeout
        return -1, json.dumps({"error": {"type": "TRANSPORT", "message": str(e)}})
```

```python
def probe_chat(base: str, key: str, model: str, session_id: str = "",
               max_tokens: int = 5, timeout_s: int = TIMEOUT_S) -> tuple[int, str]:
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    if session_id:
        headers["x-opencode-session"] = session_id
    return _http(
        "POST",
        f"{base}/chat/completions",
        headers,
        {"model": model, "max_tokens": max_tokens,
         "messages": [{"role": "user", "content": "Reply with the word ok."}]},
        timeout_s=timeout_s,
    )
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_model_discovery.py`:

```python
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
            lambda base: ["free-a", "free-b", "free-c", "paid-x"],
        )
        responses = {
            "free-a": _ok("ok"),
            "free-b": (403, json.dumps({"error": {"message": "MissingSessionID", "type": "x"}})),
            "free-c": (200, json.dumps({"choices": [{"message": {"content": ""}}]})),
        }
        monkeypatch.setattr(
            model_discovery,
            "probe_chat",
            lambda base, key, model, session_id="", max_tokens=5, timeout_s=10: responses[model],
        )
        assert discover_free_models(GO, "k") == [{"id": "free-a", "base_url": GO}]

    def test_catalog_failure_returns_empty(self, monkeypatch):
        def boom(base):
            raise RuntimeError("dns failure")

        monkeypatch.setattr(model_discovery, "discover", boom)
        assert discover_free_models(GO, "k") == []

    def test_empty_key_returns_empty(self):
        assert discover_free_models(GO, "") == []

    def test_probe_exception_skips_model(self, monkeypatch):
        monkeypatch.setattr(model_discovery, "discover", lambda base: ["free-a", "free-b"])

        def probe(base, key, model, session_id="", max_tokens=5, timeout_s=10):
            if model == "free-a":
                raise RuntimeError("timeout")
            return _ok("ok")

        monkeypatch.setattr(model_discovery, "probe_chat", probe)
        assert discover_free_models(GO, "k") == [{"id": "free-b", "base_url": GO}]


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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_model_discovery.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'model_discovery'`

- [ ] **Step 4: Write `model_discovery.py`**

```python
"""Resolve the free-model backstop from the live catalog at run start.

The pipeline's primary model is a paid one. This module discovers the free
models that currently answer on the Go catalog, so the backstop never goes
stale. ``refresh_models_file`` writes the result to ``models.json``, the same
file ``AuditedLLMClient`` reads, and leaves it untouched when discovery finds
nothing so the last known good set survives a catalog outage.
"""

from __future__ import annotations

import logging

from scripts.probe_models import classify, discover, probe_chat
from scripts.select_models import write_models_json

logger = logging.getLogger(__name__)


def discover_free_models(
    base_url: str,
    api_key: str,
    session_id: str = "",
    *,
    timeout_s: int = 10,
) -> list[dict]:
    """Probe the catalog's free models and return the ones that answer.

    Returns ``[{"id": ..., "base_url": ...}]`` for free models that returned
    non-empty content on ``/chat/completions``. Returns ``[]`` on any catalog
    or transport failure.
    """
    if not api_key:
        return []
    try:
        live_ids = discover(base_url)
    except Exception as exc:
        logger.warning(f"Model discovery failed for {base_url}: {exc}")
        return []

    selected: list[dict] = []
    for model in sorted(i for i in live_ids if i.endswith("-free")):
        try:
            status, body = probe_chat(
                base_url, api_key, model, session_id, timeout_s=timeout_s
            )
        except Exception as exc:
            logger.warning(f"Probe failed for {model}: {exc}")
            continue
        outcome, _retry = classify(status, body, "chat/completions")
        if outcome == "OK":
            selected.append({"id": model, "base_url": base_url})
    return selected


def refresh_models_file(
    path: str,
    base_url: str,
    api_key: str,
    session_id: str = "",
    *,
    timeout_s: int = 10,
) -> list[dict]:
    """Discover free models and cache them in ``models.json``.

    Writes the file only when at least one free model answered, so a failed
    discovery leaves the existing fallback cache in place. Returns the
    discovered list.
    """
    selected = discover_free_models(
        base_url, api_key, session_id, timeout_s=timeout_s
    )
    if selected:
        write_models_json(path, [base_url], selected, probes=[])
    return selected
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_model_discovery.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Commit**

```bash
git add model_discovery.py scripts/probe_models.py tests/test_model_discovery.py
git commit -m "Add live free-model discovery with models.json fallback cache"
```

---

### Task 2: Primary-first failover chain

**Files:**
- Modify: `llm_client.py` (chain assembly, remove `paid_first_stages`)
- Test: `tests/test_select_models.py` (runtime section)

**Interfaces:**
- Consumes: nothing new.
- Produces: `AuditedLLMClient.call` now tries the config primary before the selected free models for every stage.

- [ ] **Step 1: Update the runtime tests to expect primary-first**

In `tests/test_select_models.py`, replace the three order-sensitive tests in `TestRuntimeSelection` with these.

```python
    def test_primary_tried_before_selected_free_model(
        self, tmp_path, monkeypatch, scripted_http
    ):
        monkeypatch.setenv("OPENCODE_API_BASE_URL", "https://paid.example/v1")
        selected_file = tmp_path / "models.json"
        selected_file.write_text(json.dumps({
            "selected": [{"id": "longcat-2.5-preview-free", "base_url": GO}]
        }))
        http = scripted_http([_Resp(200, content="paid answer")])

        out = _call(_make_client(tmp_path, selected_file))

        assert out == "paid answer"
        assert http["calls"][0] == {
            "url": "https://paid.example/v1/chat/completions",
            "model": "paid-model",
        }

    def test_falls_back_to_selected_free_after_primary(
        self, tmp_path, monkeypatch, scripted_http
    ):
        monkeypatch.setenv("OPENCODE_API_BASE_URL", "https://paid.example/v1")
        selected_file = tmp_path / "models.json"
        selected_file.write_text(json.dumps({
            "selected": [{"id": "longcat-2.5-preview-free", "base_url": GO}]
        }))
        http = scripted_http([_Resp(400), _Resp(200, content="free answer")])

        out = _call(_make_client(tmp_path, selected_file))

        assert out == "free answer"
        assert http["calls"] == [
            {"url": "https://paid.example/v1/chat/completions", "model": "paid-model"},
            {"url": f"{GO}/chat/completions", "model": "longcat-2.5-preview-free"},
        ]

    def test_chain_is_primary_first_for_all_stages(
        self, tmp_path, monkeypatch, scripted_http
    ):
        monkeypatch.setenv("OPENCODE_API_BASE_URL", "https://paid.example/v1")
        selected_file = tmp_path / "models.json"
        selected_file.write_text(json.dumps({
            "selected": [{"id": "longcat-2.5-preview-free", "base_url": GO}]
        }))
        http = scripted_http([_Resp(200, content="paid answer")])

        out = _call(_make_client(tmp_path, selected_file))

        assert out == "paid answer"
        assert http["calls"][0]["model"] == "paid-model"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_select_models.py::TestRuntimeSelection -v`
Expected: FAIL, the first call is the free model instead of the primary.

- [ ] **Step 3: Flip the chain in `llm_client.py`**

Remove the `paid_first_stages` loading block (around lines 155-157):

```python
        # Probe-verified free models written by scripts/select_models.py.
        # When the key is absent (as in most tests) the static chain is used.
        self._selected_file: str = (models_cfg or {}).get(
            "selected_models_file", ""
        )
```

Delete these lines:

```python
        self._paid_first_stages: set[str] = set(
            (models_cfg or {}).get("paid_first_stages", [])
        )
```

Then replace the chain assembly in `call()` (around lines 273-285):

```python
        # The paid/config primary is tried first, then the probe-verified free
        # models (each on its own catalog base). Dedup by (base, model) so a
        # model is never tried twice.
        primary = model or self._primary_default
        raw_chain: list[tuple[str, str]] = [(self._base_url, primary)]
        raw_chain.extend((self._base_url, m) for m in self._fallbacks)
        raw_chain.extend(self._selected_models)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_select_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add llm_client.py tests/test_select_models.py
git commit -m "Try the paid primary before free models for every stage"
```

---

### Task 3: Wire discovery into the run and switch the primary model

**Files:**
- Modify: `config.yaml` (`models` section, lines 123-138)
- Modify: `run_pipeline.py` (imports, discovery call, models.json commit)
- Test: `tests/test_model_discovery.py` (already covers `refresh_models_file`; add the config assertion here)

**Interfaces:**
- Consumes: `model_discovery.refresh_models_file(path, base_url, api_key, session_id) -> list[dict]`.
- Produces: the run writes and commits `models.json` before the client is built.

- [ ] **Step 1: Write the failing config test**

Append to `tests/test_model_discovery.py`:

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_model_discovery.py::TestConfig -v`
Expected: FAIL, the default is `longcat-2.5-preview-free`.

- [ ] **Step 3: Update `config.yaml`**

Replace lines 123-138 (the `models:` block and its trailing comments) with:

```yaml
models:
  default: deepseek-v4.1-flash
  translate_fa: deepseek-v4.1-flash
  # The free-model backstop is discovered live at run start by
  # model_discovery.py and cached here. A failed discovery leaves the existing
  # file in place, so the last known good free models remain the fallback.
  fallbacks: []
  selected_models_file: models.json
  # Flat peak token rates in USD per million, keyed by model id. Models absent
  # from the table (the free backstops) are priced at $0.
  pricing:
    deepseek-v4.1-flash: { input: 0.30, output: 1.20 }
    deepseek-v4-flash:   { input: 0.30, output: 1.20 }
    deepseek-v4-pro:     { input: 1.32, output: 3.96 }
```

- [ ] **Step 4: Wire discovery into `run_pipeline.py`**

Add `import os` after `import logging` in the import block.

Immediately before `llm_client = AuditedLLMClient(...)` (line 200), insert:

```python
    from model_discovery import refresh_models_file

    models_path = (
        getattr(config, "models", None) or {}
    ).get("selected_models_file", "models.json")
    base_url = os.environ.get("OPENCODE_API_BASE_URL", "")
    api_key = os.environ.get("OPENCODE_API_KEY", "")
    session_id = os.environ.get("OPENCODE_SESSION_ID", "")
    if base_url and api_key:
        discovered = refresh_models_file(models_path, base_url, api_key, session_id)
        logger.info(
            "Discovered free models: "
            + (", ".join(m["id"] for m in discovered) or "none")
        )
```

Then, next to the existing metrics commit block (after `run_metrics(...)`, around line 337), add:

```python
    try:
        from stages.empty_streak import _git_commit_file

        _git_commit_file(models_path, f"Refresh model list: {target_date}")
    except Exception as e:
        logger.warning(f"models.json commit failed (non-fatal): {e}")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_model_discovery.py tests/test_select_models.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add config.yaml run_pipeline.py tests/test_model_discovery.py
git commit -m "Discover free models at run start and set deepseek-v4.1-flash primary"
```

---

### Task 4: Price the served model

**Files:**
- Modify: `llm_client.py` (pricing load, `_cost_for`, cost computation)
- Test: `tests/test_select_models.py` (new `TestCost` class)

**Interfaces:**
- Consumes: `models.pricing` from config, keyed by model id with `input` and `output` rates in USD per million.
- Produces: `AuditedLLMClient._cost_for(model, tokens_in, tokens_out) -> float`, and `total_cost` now reflects the served model.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_select_models.py`:

```python
class _UsageResp:
    def __init__(self, content="ok", tokens_in=1_000_000, tokens_out=1_000_000):
        self.status_code = 200
        self._content = content
        self._tokens_in = tokens_in
        self._tokens_out = tokens_out
        self.text = json.dumps({"raw": True})

    def json(self):
        return {
            "choices": [{"message": {"content": self._content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": self._tokens_in, "completion_tokens": self._tokens_out},
        }


class TestCost:
    RATES = {"deepseek-v4.1-flash": {"input": 0.30, "output": 1.20}}

    def _client(self, tmp_path, pricing):
        return AuditedLLMClient(
            str(tmp_path / "run"),
            {"max_cost_per_run_usd": 10.0},
            {"default": "deepseek-v4.1-flash", "fallbacks": [], "pricing": pricing},
        )

    def test_known_model(self, tmp_path):
        c = self._client(tmp_path, self.RATES)
        assert c._cost_for("deepseek-v4.1-flash", 1_000_000, 1_000_000) == pytest.approx(1.50)

    def test_free_and_unknown_are_zero(self, tmp_path):
        c = self._client(tmp_path, self.RATES)
        assert c._cost_for("longcat-2.5-preview-free", 1_000_000, 1_000_000) == 0.0
        assert c._cost_for("mystery", 1_000_000, 1_000_000) == 0.0

    def test_missing_output_key(self, tmp_path):
        c = self._client(tmp_path, {"paid-x": {"input": 1.0}})
        assert c._cost_for("paid-x", 1_000_000, 1_000_000) == pytest.approx(1.0)

    def test_zero_tokens(self, tmp_path):
        c = self._client(tmp_path, {"paid-x": {"input": 1.0, "output": 2.0}})
        assert c._cost_for("paid-x", 0, 0) == 0.0

    def test_total_cost_uses_served_model(
        self, tmp_path, monkeypatch, scripted_http
    ):
        monkeypatch.setenv("OPENCODE_API_BASE_URL", "https://paid.example/v1")
        scripted_http([_UsageResp(content="answer")])
        c = self._client(tmp_path, self.RATES)
        _call(c)
        assert c.total_cost == pytest.approx(1.50)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_select_models.py::TestCost -v`
Expected: FAIL with `AttributeError: 'AuditedLLMClient' object has no attribute '_cost_for'`

- [ ] **Step 3: Implement pricing in `llm_client.py`**

Remove the class attributes (lines 98-100):

```python
    # Cost estimates per million tokens (all configured models are free tier)
    _INPUT_COST_PER_M = 0.0
    _OUTPUT_COST_PER_M = 0.0
```

In `__init__`, next to the `selected_models_file` load, add:

```python
        # Flat per-model token rates in USD per million, keyed by model id.
        self._pricing: dict = (models_cfg or {}).get("pricing", {}) or {}
```

Add the helper method after `_load_selected_models`:

```python
    def _cost_for(self, model: str, tokens_in: int, tokens_out: int) -> float:
        """Cost in USD for a call, from the served model's rate.

        Unknown models (the free backstops) are priced at $0. Completion
        tokens already include reasoning tokens, so thinking is not billed
        twice.
        """
        rates = self._pricing.get(model)
        if not isinstance(rates, dict):
            return 0.0
        return (
            (tokens_in / 1_000_000) * float(rates.get("input", 0.0))
            + (tokens_out / 1_000_000) * float(rates.get("output", 0.0))
        )
```

Replace the cost computation in `call()` (lines 380-384):

```python
        call_cost = self._cost_for(served_model or "", tokens_in, tokens_out)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_select_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add llm_client.py tests/test_select_models.py
git commit -m "Price each call by the served model's configured rate"
```

---

### Task 5: Record and aggregate cost in metrics

**Files:**
- Modify: `stages/metrics.py` (`build_run_record`, `_day_from_record`, `_day_from_posts`, `_today_from_day`, `recompute_totals`)
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: `run_meta.total_cost_usd` and each day record's `cost_usd`.
- Produces: `record["cost_usd"]`, `totals["cost_usd"]`, `totals["rates"]["cost_usd"]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_metrics.py`:

```python
class TestCost:
    def test_run_record_carries_cost(self):
        meta = {
            "run_id": "r",
            "stages": {},
            "total_cost_usd": 0.1234,
        }
        rec = build_run_record(
            "2026-10-06", run_meta=meta, en_post=None, fa_post=None,
            ledger={}, subscribers={}, audit={},
        )
        assert rec["cost_usd"] == 0.1234

    def test_run_record_missing_cost_is_zero(self):
        meta = {"run_id": "r", "stages": {}}
        rec = build_run_record(
            "2026-10-06", run_meta=meta, en_post=None, fa_post=None,
            ledger={}, subscribers={}, audit={},
        )
        assert rec["cost_usd"] == 0.0

    def test_totals_sum_cost(self):
        days = [
            {"date": "2026-10-01", "cost_usd": 0.1, "posted": 1, "posted_en": 1,
             "posted_fa": 0, "links": 1, "en": True, "fa": False, "emails": {},
             "subscribers": None, "status": "success", "empty": False,
             "processed": None, "included": None},
            {"date": "2026-10-02", "cost_usd": 0.2, "posted": 1, "posted_en": 1,
             "posted_fa": 0, "links": 1, "en": True, "fa": False, "emails": {},
             "subscribers": None, "status": "success", "empty": False,
             "processed": None, "included": None},
        ]
        totals = recompute_totals(days, None)
        assert round(totals["cost_usd"], 4) == 0.3
        assert round(totals["rates"]["cost_usd"]["per_day"], 4) == 0.15
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_metrics.py::TestCost -v`
Expected: FAIL with `KeyError: 'cost_usd'`

- [ ] **Step 3: Implement cost in `stages/metrics.py`**

In `build_run_record`, add the key to the returned dict (next to `"errors"`):

```python
        "cost_usd": round(meta.get("total_cost_usd") or 0.0, 4),
```

In `_day_from_record`, add to the returned dict:

```python
        "cost_usd": rec.get("cost_usd"),
```

In `_day_from_posts`, add to the returned dict:

```python
        "cost_usd": None,
```

In `_today_from_day`, add to the returned dict:

```python
        "cost_usd": day.get("cost_usd"),
```

In `recompute_totals`, add to the `totals` dict (next to `"llm_calls"`):

```python
        "cost_usd": _sum("cost_usd"),
```

And add to the `totals["rates"]` dict:

```python
        "cost_usd": _rate(totals["cost_usd"], _known("cost_usd")),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_metrics.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add stages/metrics.py tests/test_metrics.py
git commit -m "Record and aggregate per-run LLM cost in metrics"
```

---

### Task 6: Show cost on the dashboard

**Files:**
- Modify: `docs/dashboard.md`
- Modify: `docs/assets/js/dashboard.js`

**Interfaces:**
- Consumes: `today.cost_usd`, `day.cost_usd`, `t.cost_usd`, `t.rates.cost_usd` from `docs/_data/dashboard.json`.
- Produces: visible cost cards, a per-day chart, and an averages row.

- [ ] **Step 1: Add the per-day metric mapping in `dashboard.js`**

In `valueOf`, after the `tokens_total` line, add:

```javascript
    if (metric === "cost_usd") return day.cost_usd || 0;
```

- [ ] **Step 2: Add a "Cost this run" card in `docs/dashboard.md`**

After the input-tokens card (closes at line 81), add:

```html
  <div class="card">
    <span class="card-value">${{ today.cost_usd | default: 0 | round: 2 }}</span>
    <span class="card-label">Cost this run</span>
    <span class="card-sub">flat peak rates</span>
  </div>
```

- [ ] **Step 3: Add an all-time cost card**

After the LLM-tokens card (closes at line 171), add:

```html
  <div class="card">
    <span class="card-value">${{ t.cost_usd | default: 0 | round: 2 }}</span>
    <span class="card-label">LLM cost</span>
    <span class="card-sub">${{ t.rates.cost_usd.per_day | default: 0 | round: 2 }}/day</span>
  </div>
```

- [ ] **Step 4: Add the averages row**

After the "Total tokens" row (line 209), add:

```html
    <tr><td>LLM cost (USD)</td><td>{{ t.rates.cost_usd.days | default: 0 }}</td><td>{{ t.rates.cost_usd.per_day | default: "—" }}</td><td>{{ t.rates.cost_usd.per_week | default: "—" }}</td><td>{{ t.rates.cost_usd.per_month | default: "—" }}</td><td>{{ t.rates.cost_usd.estimated_total | default: "—" }}</td></tr>
```

- [ ] **Step 5: Add the per-day chart**

After the "Total tokens" chart include (line 232), add:

```liquid
{% include chart.html metric="cost_usd" label="LLM cost (USD)" %}
```

- [ ] **Step 6: Verify**

Run: `grep -n "cost_usd" docs/dashboard.md docs/assets/js/dashboard.js`
Expected: the card, averages row, chart include, and JS mapping are present.

If a Jekyll toolchain is available, build the site to confirm the Liquid renders:

```bash
bundle exec jekyll build 2>&1 | tail -5
```

Expected: build succeeds with no Liquid errors. If bundler is not installed, note that the daily publish will render it and check `docs/dashboard/` after the next run.

- [ ] **Step 7: Commit**

```bash
git add docs/dashboard.md docs/assets/js/dashboard.js
git commit -m "Show per-run and all-time LLM cost on the dashboard"
```

---

### Task 7: Move the daily run to 6am PT

**Files:**
- Modify: `.github/workflows/daily-brief.yml` (cron and comment)
- Modify: `config.yaml` (`schedule.run_hour`)

**Interfaces:**
- Consumes: nothing.
- Produces: the scheduled run fires at 13:07 UTC.

- [ ] **Step 1: Update the cron**

In `.github/workflows/daily-brief.yml`, replace the schedule block comments and line 8:

```yaml
on:
  schedule:
    # 6am US Pacific. 13:07 UTC during PDT (Mar-Nov), 14:07 UTC during PST.
    # GitHub fires scheduled jobs in UTC and does not follow DST, so the local
    # start drifts by one hour in standard time. Offset a few minutes to avoid
    # the :00 stampede. GitHub's scheduler has been firing this 4-8h late under
    # load, so completion lands later.
    - cron: '7 13 * * *'
  workflow_dispatch:
```

- [ ] **Step 2: Update the config hour**

In `config.yaml`, change line 280:

```yaml
  run_hour: 6
```

- [ ] **Step 3: Verify**

Run: `grep -n "cron\|run_hour" .github/workflows/daily-brief.yml config.yaml`
Expected: `cron: '7 13 * * *'` and `run_hour: 6`.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/daily-brief.yml config.yaml
git commit -m "Move the daily run to 6am Pacific"
```

---

## Self-Review

**Spec coverage:**
- Primary `deepseek-v4.1-flash`: Task 3, Step 3.
- Free-only backstop, live discovery: Tasks 1 and 3.
- Discovery before the client, fallback cache, commit: Task 3.
- Primary-first chain, `paid_first_stages` removed: Task 2.
- Flat peak-rate cost table and per-served-model pricing: Task 4.
- Cost in run record, days, totals, rates: Task 5.
- Cost on the dashboard (card, per-day metric, averages): Task 6.
- 6am PT cron: Task 7.
- Tests for discovery, cost, metrics, and chain order: Tasks 1, 2, 4, 5.

**Placeholder scan:** No `TBD`, no "add error handling", no "similar to Task N". Every code step carries the actual code.

**Type consistency:** `discover_free_models` and `refresh_models_file` keep the same signatures across Tasks 1 and 3. `_cost_for(model, tokens_in, tokens_out)` is defined in Task 4 and used only there. `cost_usd` is the single key name across Tasks 4, 5, and 6.

**Review Focus tests:** each line in Review Focus maps to a test: Task 3 (`refresh_models_file` leaves the file when empty), Task 1 (catalog failure and probe exception), Task 4 (unknown model, missing output key), Task 5 (missing `total_cost_usd`).
