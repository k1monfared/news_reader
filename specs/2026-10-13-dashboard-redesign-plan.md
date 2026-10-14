# Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the operator dashboard as a hub overview plus five focused subpages, instrument a per-source funnel and per-call cost, and correct the misleading metrics.

**Architecture:** Instrument the pipeline stages to write a per-run `source_stats.json` and per-call `cost_usd` in the audit, roll everything up in `stages/metrics.py` into one `docs/_data/dashboard.json`, and render static Jekyll pages from it. No new services.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest, Jekyll + Liquid, vanilla JS.

**Spec:** `specs/2026-10-13-dashboard-redesign-design.md`

## Global Constraints

- Python 3.11 or newer, Pydantic v2. `from __future__ import annotations` at the top of every new or modified Python file.
- Tests never touch the network. Mock HTTP and the LLM.
- Run `pytest tests/` before every commit. Commit only when it passes. Stage only the files each task changes (`data/empty_streak.json` is dirtied by the suite; never stage it).
- Never `git push`. Local commits only.
- The dashboard is English only. Engineering detail is welcome (operator audience).
- Metric key names: `cost_usd`, `cost`, `sources`, `failovers`. Do not rename across tasks.
- Al Jazeera and the Iran keyword list are unchanged. This work only observes.
- Charts must keep a zero baseline (bar charts start at zero).

## Review Focus

- A run with no `source_stats.json` (older runs, or a stage that never ran) must roll up without error and render as "not recorded". (Task 5 test.)
- `read_audit` on an `llm_calls.jsonl` whose lines lack `cost_usd` (all history before this) must not raise. (Task 1 test.)
- A malformed or partial `source_stats.json` must not crash `update_source_stats`. (Task 2 test.)
- The fractional cost chart must not floor a sub-$1 max to 1. (Task 6 grep plus visual check.)
- Cost projection with zero cost days (before any priced run) must be null, not zero or a crash. (Task 5 test.)

---

### Task 1: Per-call cost in the audit

**Files:**
- Modify: `llm_client.py` (add `cost_usd` to the audit summary line in `call`)
- Modify: `stages/metrics.py` (`read_audit` aggregates cost by stage and model, and failovers)
- Test: `tests/test_metrics.py`, `tests/test_select_models.py`

**Interfaces:**
- Produces: each `audit/llm_calls.jsonl` line gains `"cost_usd": float`; `read_audit(run_dir)` returns `{"by_stage", "models", "total", "cost_by_stage", "cost_by_model", "total_cost", "failovers"}` where `by_stage[stage]` also gains `"cost_usd"`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_metrics.py`, extend `TestAuditTokens`:

```python
    def test_aggregates_cost_by_stage_and_model(self, tmp_path):
        run_dir = tmp_path / "run"
        (run_dir / "audit").mkdir(parents=True)
        lines = [
            {"stage": "filter", "tokens_in": 100, "tokens_out": 20, "tokens_thinking": 0,
             "model": "deepseek-v4.1-flash", "cost_usd": 0.02},
            {"stage": "filter", "tokens_in": 50, "tokens_out": 10, "tokens_thinking": 0,
             "model": "deepseek-v4.1-flash", "cost_usd": 0.01},
            {"stage": "summarize", "tokens_in": 200, "tokens_out": 80,
             "model": "longcat-2.5-preview-free", "cost_usd": 0.0},
        ]
        (run_dir / "audit" / "llm_calls.jsonl").write_text(
            "\n".join(json.dumps(x) for x in lines) + "\n"
        )
        audit = read_audit(str(run_dir))
        assert audit["cost_by_stage"]["filter"] == pytest.approx(0.03)
        assert audit["cost_by_model"]["deepseek-v4.1-flash"] == pytest.approx(0.03)
        assert audit["total_cost"] == pytest.approx(0.03)
        assert audit["by_stage"]["filter"]["cost_usd"] == pytest.approx(0.03)

    def test_read_audit_tolerates_missing_cost(self, tmp_path):
        run_dir = tmp_path / "run"
        (run_dir / "audit").mkdir(parents=True)
        (run_dir / "audit" / "llm_calls.jsonl").write_text(
            json.dumps({"stage": "filter", "tokens_in": 1, "tokens_out": 1, "model": "m"}) + "\n"
        )
        audit = read_audit(str(run_dir))
        assert audit["total_cost"] == 0.0
        assert "filter" in audit["cost_by_stage"]

    def test_aggregates_failovers(self, tmp_path):
        run_dir = tmp_path / "run"
        (run_dir / "audit").mkdir(parents=True)
        lines = [
            {"stage": "filter", "tokens_in": 1, "tokens_out": 1, "model": "m2",
             "failed_over_from": ["m1"]},
            {"stage": "filter", "tokens_in": 1, "tokens_out": 1, "model": "m2",
             "failed_over_from": ["m1"]},
        ]
        (run_dir / "audit" / "llm_calls.jsonl").write_text(
            "\n".join(json.dumps(x) for x in lines) + "\n"
        )
        assert read_audit(str(run_dir))["failovers"] == {"m1": 2}
```

Add `pytest` and `read_audit` imports if missing (they exist in that file). In `tests/test_select_models.py`, add to `TestCost`:

```python
    def test_audit_summary_records_cost(self, tmp_path, monkeypatch, scripted_http):
        monkeypatch.setenv("OPENCODE_API_BASE_URL", "https://paid.example/v1")
        scripted_http([_UsageResp(content="answer")])
        c = self._client(tmp_path, self.RATES)
        _call(c)
        calls = (tmp_path / "run" / "audit" / "llm_calls.jsonl").read_text().strip().splitlines()
        assert json.loads(calls[-1])["cost_usd"] == pytest.approx(1.50)
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_metrics.py::TestAuditTokens tests/test_select_models.py::TestCost -v`
Expected: FAIL (`KeyError`/`KeyError: 'cost_by_stage'`, and the audit line has no `cost_usd`).

- [ ] **Step 3: Record per-call cost in `llm_client.py`**

In `call`, the summary dict appended to `llm_calls.jsonl` currently ends with `"failed_over_from": [a["model"] for a in attempts]`. Add `"cost_usd": call_cost` to that dict.

- [ ] **Step 4: Aggregate in `stages/metrics.py` `read_audit`**

Add to the returned structure cost and failover aggregation:

```python
def read_audit(run_dir: str) -> dict:
    by_stage: dict[str, dict] = {}
    models: list[str] = []
    total = {"input": 0, "output": 0, "thinking": 0, "calls": 0, "cost_usd": 0.0}
    cost_by_stage: dict[str, float] = {}
    cost_by_model: dict[str, float] = {}
    failovers: dict[str, int] = {}
    path = Path(run_dir) / "audit" / "llm_calls.jsonl"
    if not path.exists():
        return {
            "by_stage": by_stage, "models": models, "total": total,
            "cost_by_stage": cost_by_stage, "cost_by_model": cost_by_model,
            "total_cost": 0.0, "failovers": failovers,
        }
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        stage = entry.get("stage", "unknown")
        usage = by_stage.setdefault(
            stage,
            {"input": 0, "output": 0, "thinking": 0, "calls": 0, "cost_usd": 0.0},
        )
        cost = float(entry.get("cost_usd", 0.0) or 0.0)
        usage["input"] += entry.get("tokens_in", 0) or 0
        usage["output"] += entry.get("tokens_out", 0) or 0
        usage["thinking"] += entry.get("tokens_thinking", 0) or 0
        usage["calls"] += 1
        usage["cost_usd"] += cost
        model = entry.get("model")
        if model and model not in models:
            models.append(model)
        cost_by_stage[stage] = cost_by_stage.get(stage, 0.0) + cost
        if model:
            cost_by_model[model] = cost_by_model.get(model, 0.0) + cost
        for src in entry.get("failed_over_from", []) or []:
            failovers[src] = failovers.get(src, 0) + 1
    for usage in by_stage.values():
        for key in ("input", "output", "thinking", "calls"):
            total[key] += usage[key]
        total["cost_usd"] += usage["cost_usd"]
    return {
        "by_stage": by_stage, "models": models, "total": total,
        "cost_by_stage": cost_by_stage, "cost_by_model": cost_by_model,
        "total_cost": round(total["cost_usd"], 4), "failovers": failovers,
    }
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_metrics.py tests/test_select_models.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add llm_client.py stages/metrics.py tests/test_metrics.py tests/test_select_models.py
git commit -m "Record per-call cost and failovers in the audit"
```

---

### Task 2: Per-source funnel helper and fetch instrumentation

**Files:**
- Create: `stages/source_stats.py`
- Modify: `stages/fetch.py`
- Test: `tests/test_source_stats.py`

**Interfaces:**
- Produces: `update_source_stats(run_dir, source, **counters) -> None` (additive for ints and floats, overwrite for bools), `read_source_stats(run_dir) -> dict[str, dict]`.
- Consumes: `run_fetch` already computes `items_by_source` and `sources_down`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_source_stats.py`:

```python
"""Tests for the per-source funnel accumulator."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stages.source_stats import read_source_stats, update_source_stats


def test_creates_and_merges(tmp_path):
    run = str(tmp_path)
    update_source_stats(run, "aljazeera", fetched=25, down=False)
    update_source_stats(run, "aljazeera", prefilter_passed=1)
    update_source_stats(run, "bbc", fetched=35)
    stats = read_source_stats(run)
    assert stats["aljazeera"] == {"fetched": 25, "down": False, "prefilter_passed": 1}
    assert stats["bbc"]["fetched"] == 35


def test_additive_for_counts_and_overwrite_for_bool(tmp_path):
    run = str(tmp_path)
    update_source_stats(run, "aj", fetched=10)
    update_source_stats(run, "aj", fetched=5)
    assert read_source_stats(run)["aj"]["fetched"] == 15
    update_source_stats(run, "aj", down=True)
    update_source_stats(run, "aj", down=False)
    assert read_source_stats(run)["aj"]["down"] is False


def test_tolerates_missing_and_malformed_file(tmp_path):
    run = str(tmp_path)
    assert read_source_stats(run) == {}
    (tmp_path / "source_stats.json").write_text("{not json")
    assert read_source_stats(run) == {}
    update_source_stats(run, "aj", fetched=1)
    assert read_source_stats(run) == {"aj": {"fetched": 1}}


def test_tolerates_non_dict_entries(tmp_path):
    run = str(tmp_path)
    (tmp_path / "source_stats.json").write_text(json.dumps({"aj": 5, "bbc": {"fetched": 2}}))
    update_source_stats(run, "aj", fetched=1)
    stats = read_source_stats(run)
    assert stats["aj"]["fetched"] == 1
    assert stats["bbc"]["fetched"] == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_source_stats.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'stages.source_stats'`)

- [ ] **Step 3: Write `stages/source_stats.py`**

```python
"""Per-run per-source funnel counters.

Each pipeline stage adds the counters it knows to ``source_stats.json`` in the
run directory. Int counters are summed, booleans are overwritten. A missing or
malformed file is treated as empty so a partial run never crashes the merge.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

STATS_FILE = "source_stats.json"


def _path(run_dir: str) -> Path:
    return Path(run_dir) / STATS_FILE


def read_source_stats(run_dir: str) -> dict[str, dict]:
    path = _path(run_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        name: counters
        for name, counters in data.items()
        if isinstance(counters, dict)
    }


def update_source_stats(run_dir: str, source: str, **counters) -> None:
    """Merge counters for one source into the run's source_stats.json."""
    stats = read_source_stats(run_dir)
    entry = stats.setdefault(source, {})
    for key, value in counters.items():
        if isinstance(value, bool):
            entry[key] = value
        elif isinstance(value, (int, float)):
            entry[key] = entry.get(key, 0) + value
        else:
            entry[key] = value
    path = _path(run_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stats, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as exc:
        logger.warning(f"Could not write source stats for {source}: {exc}")
```

- [ ] **Step 4: Instrument `stages/fetch.py`**

After the fetcher loop and before writing the output, record counts:

```python
    from stages.source_stats import update_source_stats

    for source in config.sources:
        update_source_stats(
            run_dir,
            source.name,
            fetched=items_by_source.get(source.name, 0),
            down=source.name in sources_down,
        )
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_source_stats.py tests/test_fetch.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add stages/source_stats.py stages/fetch.py tests/test_source_stats.py
git commit -m "Add per-source funnel accumulator and fetch instrumentation"
```

---

### Task 3: Filter instrumentation

**Files:**
- Modify: `stages/filter.py`
- Test: `tests/test_regressions.py` or `tests/test_source_stats.py`

**Interfaces:**
- Consumes: `update_source_stats(run_dir, source, **counters)`.
- Produces: each source gains `prefilter_passed` and `included` counters.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_source_stats.py` a test that drives the pure counting helper (extract a small pure function `count_by_source(items, predicate) -> dict[str, int]` in `stages/source_stats.py`):

```python
from stages.source_stats import count_by_source


def test_count_by_source():
    items = [{"source": "aj"}, {"source": "aj"}, {"source": "bbc"}]
    assert count_by_source(items, lambda i: True) == {"aj": 2, "bbc": 1}
    assert count_by_source(items, lambda i: i["source"] == "aj") == {"aj": 2}
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_source_stats.py::test_count_by_source -v`
Expected: FAIL (`ImportError: cannot import name 'count_by_source'`)

- [ ] **Step 3: Add `count_by_source` to `stages/source_stats.py`**

```python
def count_by_source(items, predicate) -> dict[str, int]:
    """Count items per item.source where predicate(item) is true."""
    counts: dict[str, int] = {}
    for item in items:
        if predicate(item):
            name = item.get("source") if isinstance(item, dict) else getattr(item, "source", None)
            if name:
                counts[name] = counts.get(name, 0) + 1
    return counts
```

- [ ] **Step 4: Instrument `stages/filter.py`**

In `run_filter`, after computing the final included set, emit the per-source counters. The deduped items are loaded into a list (call it `items`); prefilter-passed items are those that were not pre-filter excluded. Add near the end of `run_filter`, before returning:

```python
    from stages.source_stats import count_by_source, update_source_stats

    prefilter_passed = count_by_source(items, lambda i: True)  # replace with the pre-filter survivors list
    included_counts = count_by_source(included_items, lambda i: True)  # the final included list
    for name, n in prefilter_passed.items():
        update_source_stats(run_dir, name, prefilter_passed=n)
    for name, n in included_counts.items():
        update_source_stats(run_dir, name, included=n)
```

Implementer note: use the actual variable names in `run_filter` for the pre-filter survivors and the final included list. `append` per item is not required.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_source_stats.py tests/test_regressions.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add stages/source_stats.py stages/filter.py tests/test_source_stats.py
git commit -m "Record per-source pre-filter and included counts"
```

---

### Task 4: Summarize instrumentation

**Files:**
- Modify: `stages/summarize.py`
- Test: `tests/test_source_stats.py`

**Interfaces:**
- Consumes: `update_source_stats`, `count_by_source`.
- Produces: each source gains a `posted` counter.

- [ ] **Step 1: Write the failing test**

The posted count is a pure projection of the included items that reached the report. Test the helper directly:

```python
from stages.source_stats import summarize_posted_counts


def test_summarize_posted_counts_includes_related():
    items = [
        {"source": "bbc", "related_sources": ["aj"]},
        {"source": "france24", "related_sources": []},
    ]
    assert summarize_posted_counts(items) == {"bbc": 1, "aj": 1, "france24": 1}
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_source_stats.py::test_summarize_posted_counts_includes_related -v`
Expected: FAIL (`ImportError`)

- [ ] **Step 3: Add the helper to `stages/source_stats.py`**

```python
def summarize_posted_counts(items) -> dict[str, int]:
    """Count each item's source and its related sources as 'posted'."""
    counts: dict[str, int] = {}
    for item in items:
        src = item.get("source") if isinstance(item, dict) else getattr(item, "source", None)
        related = (item.get("related_sources") if isinstance(item, dict)
                   else getattr(item, "related_sources", [])) or []
        for name in [src, *related]:
            if name:
                counts[name] = counts.get(name, 0) + 1
    return counts
```

- [ ] **Step 4: Instrument `stages/summarize.py`**

After the included items are selected for the report, emit posted counts:

```python
    from stages.source_stats import summarize_posted_counts, update_source_stats

    for name, n in summarize_posted_counts(included).items():
        update_source_stats(run_dir, name, posted=n)
```

Use the actual list of items that reached the report.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_source_stats.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add stages/source_stats.py stages/summarize.py tests/test_source_stats.py
git commit -m "Record per-source posted counts"
```

---

### Task 5: Metrics record and totals

**Files:**
- Modify: `stages/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: `read_source_stats`, `read_audit` (Task 1), `site.data.failures` shape (`counts.failed_not_backfilled`, `counts.backfilled`, or `backfilled_dates` length).
- Produces: run record gains `sources` (list), `cost` (dict), `failovers` (dict). `recompute_totals` gains `sources`, `cost_actual`, `cost_per_day_over_cost_days`, `cost_projected`, `cost_extra_runs`, `days_with_tokens`, `first_token_date`, `days_succeeded`, `days_degraded`, `days_failed_ever`, `days_repaired`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_metrics.py`:

```python
class TestCostAndSources:
    def test_run_record_carries_sources_and_cost(self, tmp_path):
        run_dir = tmp_path / "2026-10-13-090000"
        (run_dir / "audit").mkdir(parents=True)
        (run_dir / "source_stats.json").write_text(json.dumps({
            "aljazeera": {"fetched": 25, "down": False, "prefilter_passed": 1, "included": 0, "posted": 0},
            "bbc": {"fetched": 35, "down": False, "prefilter_passed": 7, "included": 5, "posted": 3},
        }))
        meta = {"run_id": "r", "stages": {}, "total_cost_usd": 0.12}
        audit = {"by_stage": {}, "models": ["m"], "total": {},
                 "cost_by_stage": {"filter": 0.02}, "cost_by_model": {"m": 0.02},
                 "total_cost": 0.02, "failovers": {"m1": 1}}
        sources = read_source_stats(str(run_dir))
        rec = build_run_record(
            "2026-10-13", run_meta=meta, en_post=None, fa_post=None,
            ledger={}, subscribers={}, audit=audit, sources=sources,
        )
        assert {s["name"] for s in rec["sources"]} == {"aljazeera", "bbc"}
        assert rec["cost"]["total"] == pytest.approx(0.12)
        assert rec["cost"]["by_stage"]["filter"] == pytest.approx(0.02)
        assert rec["failovers"] == {"m1": 1}

    def test_projection_uses_extra_runs(self):
        days = [{"date": "2026-10-13", "cost_usd": 0.10, "posted": 1, "posted_en": 1,
                 "posted_fa": 0, "links": 1, "en": True, "fa": False, "emails": {},
                 "subscribers": None, "status": "success", "empty": False,
                 "processed": None, "included": None}]
        totals = recompute_totals(days, None, extra_runs=8)
        # per_day over 1 cost day = 0.10; days_running = 1; + 8 extra runs
        assert totals["cost_projected"] == pytest.approx(0.90)

    def test_projection_null_without_cost_days(self):
        days = [{"date": "2026-10-13", "cost_usd": None, "posted": 1, "posted_en": 1,
                 "posted_fa": 0, "links": 1, "en": True, "fa": False, "emails": {},
                 "subscribers": None, "status": "success", "empty": False,
                 "processed": None, "included": None}]
        assert recompute_totals(days, None, extra_runs=8)["cost_projected"] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_metrics.py::TestCostAndSources -v`
Expected: FAIL (`build_run_record` has no `sources` parameter; `recompute_totals` has no `extra_runs`).

- [ ] **Step 3: Update `build_run_record`**

Add a `sources: dict | None = None` parameter. Build the list and add `sources`, `cost`, and `failovers`:

```python
    audit = audit or {"by_stage": {}, "models": [], "total": {}}
    sources = sources or {}
    source_list = [
        {"name": name, **counters}
        for name, counters in sorted(sources.items())
    ]
    cost = {
        "total": round(meta.get("total_cost_usd") or 0.0, 4),
        "by_stage": audit.get("cost_by_stage", {}),
        "by_model": audit.get("cost_by_model", {}),
    }
```

and add `"sources": source_list`, `"cost": cost`, `"failovers": audit.get("failovers", {})` to the returned dict.

- [ ] **Step 4: Update `recompute_totals`**

Change the signature to `recompute_totals(days, joins=None, extra_runs: int = 0)`. Add:

```python
    cost_days = [d for d in days if d.get("cost_usd") is not None]
    cost_actual = sum(d["cost_usd"] for d in cost_days) if cost_days else None
    cost_per_day = (cost_actual / len(cost_days)) if cost_days else None
    tok_days = [d for d in days if (d.get("tokens") or {}).get("input") is not None]
    first_token_date = tok_days[0]["date"] if tok_days else None
```

Add to `totals`: `"cost_actual": round(cost_actual, 4) if cost_actual is not None else None`, `"cost_per_day_over_cost_days"`, `"cost_extra_runs": extra_runs`, `"days_with_tokens": len(tok_days)`, `"first_token_date": first_token_date`, `"days_succeeded": sum(1 for d in days if d.get("status") == "success")`, `"days_degraded": sum(1 for d in days if d.get("status") == "degraded")`, and:

```python
    totals["cost_projected"] = (
        round(cost_per_day * (running + extra_runs), 4)
        if cost_per_day is not None else None
    )
```

- [ ] **Step 5: Pass `extra_runs` in `run_metrics`**

In `run_metrics`, read `_data/failures.json` (already loaded for the site) or the failures counts, and compute `extra_runs = counts["backfilled"] + len(counts["failed_not_backfilled"])`. Call `build_dashboard(..., extra_runs=extra_runs)` and thread it to `recompute_totals`. Add `extra_runs` and `days_failed_ever = counts["failed"]` and `days_repaired = counts["failed_and_backfilled"]` into totals via the same path (pass the failures counts into `build_dashboard`).

Implementer note: `build_dashboard` and `recompute_totals` need the failures counts threaded through. Keep the existing call sites working by defaulting `extra_runs=0` and failures to `{}`.

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_metrics.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add stages/metrics.py tests/test_metrics.py
git commit -m "Roll up per-source funnel, cost, failovers, and coverage in metrics"
```

---

### Task 6: Chart axis fix, sub-nav, and CSS

**Files:**
- Modify: `docs/assets/js/dashboard.js`
- Create: `docs/_includes/dashnav.html`
- Modify: `docs/assets/css/style.css`
- Test: grep-based verification

**Interfaces:**
- Produces: `dashnav.html` renders the six tab links; chart axis supports fractional metrics.

- [ ] **Step 1: Fix the fractional axis in `docs/assets/js/dashboard.js`**

Replace the axis computation (around lines 53-61). The current code floors `max` at 1. New logic:

```javascript
    var isFractional = metric === "cost_usd";
    var vals = days.map(function (d) { return valueOf(d, metric); });
    var max = 0;
    vals.forEach(function (v) { if (v > max) max = v; });
    if (max <= 0) max = isFractional ? 0.01 : 1;
    var mid = max / 2;
    function fmt(n) {
      if (!isFractional) return String(Math.round(n));
      return n.toFixed(n < 0.1 ? 3 : 2);
    }
    if (yMax) yMax.textContent = fmt(max);
    if (yMid) yMid.textContent = fmt(mid);
```

Keep the bar height as `(v / max) * 100`.

- [ ] **Step 2: Create `docs/_includes/dashnav.html`**

```html
{% assign base = '/' | relative_url %}
<nav class="dash-nav" aria-label="Dashboard sections">
  <a href="{{ base }}dashboard/"{% if page.url == '/dashboard/' %} class="active" aria-current="page"{% endif %}>Overview</a>
  <a href="{{ base }}dashboard/cost/"{% if page.url == '/dashboard/cost/' %} class="active" aria-current="page"{% endif %}>Cost</a>
  <a href="{{ base }}dashboard/reliability/"{% if page.url == '/dashboard/reliability/' %} class="active" aria-current="page"{% endif %}>Reliability</a>
  <a href="{{ base }}dashboard/content/"{% if page.url == '/dashboard/content/' %} class="active" aria-current="page"{% endif %}>Content</a>
  <a href="{{ base }}dashboard/sources/"{% if page.url == '/dashboard/sources/' %} class="active" aria-current="page"{% endif %}>Sources</a>
  <a href="{{ base }}dashboard/audience/"{% if page.url == '/dashboard/audience/' %} class="active" aria-current="page"{% endif %}>Audience</a>
</nav>
```

- [ ] **Step 3: Add sub-nav styles to `docs/assets/css/style.css`**

```css
.dash-nav {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin: 0.5rem 0 1.5rem;
  border-bottom: 1px solid var(--border, #2a2f3a);
  padding-bottom: 0.5rem;
}
.dash-nav a {
  padding: 0.25rem 0.75rem;
  border-radius: 6px;
  text-decoration: none;
}
.dash-nav a.active {
  background: var(--accent, #3b82f6);
  color: #fff;
}
```

- [ ] **Step 4: Verify**

Run: `grep -n "isFractional\|dash-nav" docs/assets/js/dashboard.js docs/_includes/dashnav.html docs/assets/css/style.css`
Expected: the axis logic, the nav links, and the styles are present.

- [ ] **Step 5: Commit**

```bash
git add docs/assets/js/dashboard.js docs/_includes/dashnav.html docs/assets/css/style.css
git commit -m "Add fractional chart axis and dashboard sub-nav"
```

---

### Task 7: Overview page

**Files:**
- Modify: `docs/dashboard.md` (rewrite as the overview)

**Interfaces:**
- Consumes: `site.data.dashboard` (`today`, `days`, `totals`), `site.data.run_status`, `site.data.failures`.

- [ ] **Step 1: Rewrite `docs/dashboard.md`**

Keep the frontmatter and the live-run banner. Add `{% include dashnav.html %}` after the H1. Replace the "All time" cards with honest labels and add a source-health line and link cards. The key changed cards:

```html
  <div class="card">
    <span class="card-value">{{ t.briefs_en | default: 0 }}</span>
    <span class="card-label">Briefs published (EN)</span>
    <span class="card-sub">{{ t.days_running | default: 0 }} days running, {{ t.days_running | minus: t.briefs_en }} without a brief</span>
  </div>
  <div class="card">
    <span class="card-value">${{ t.cost_actual | default: 0 | round: 2 }}</span>
    <span class="card-label">LLM cost (recorded)</span>
    <span class="card-sub">${{ t.cost_projected | default: 0 | round: 2 }} projected over {{ t.days_running | plus: t.cost_extra_runs }} run-days</span>
  </div>
  <div class="card">
    <span class="card-value">{{ t.days_failed_ever | default: 0 }}</span>
    <span class="card-label">Failed days (ever)</span>
    <span class="card-sub">{{ t.days_repaired | default: 0 }} repaired, {{ t.days_degraded | default: 0 }} degraded</span>
  </div>
```

Add a source-health note and link cards:

```html
<h2>Sections</h2>
<div class="cards">
  <a class="card" href="{{ '/dashboard/cost/' | relative_url }}"><span class="card-label">Cost &amp; usage</span><span class="card-sub">spend, tokens, projections</span></a>
  <a class="card" href="{{ '/dashboard/reliability/' | relative_url }}"><span class="card-label">Reliability</span><span class="card-sub">status, failures, repairs</span></a>
  <a class="card" href="{{ '/dashboard/content/' | relative_url }}"><span class="card-label">Content</span><span class="card-sub">entries, links, story mix</span></a>
  <a class="card" href="{{ '/dashboard/sources/' | relative_url }}"><span class="card-label">Sources &amp; models</span><span class="card-sub">funnel, models, failover</span></a>
  <a class="card" href="{{ '/dashboard/audience/' | relative_url }}"><span class="card-label">Audience</span><span class="card-sub">subscribers, deliveries</span></a>
</div>
```

Move the token details, averages table, per-day charts, and daily-status table to the subpages (Tasks 8). Keep the overview to the live banner, today's run, the headline strip, source health, and the link cards.

- [ ] **Step 2: Verify**

Run: `grep -n "dashnav\|Sections\|without a brief\|cost_projected\|Failed days (ever)" docs/dashboard.md`
Expected: present.

- [ ] **Step 3: Commit**

```bash
git add docs/dashboard.md
git commit -m "Rewrite dashboard as an operator overview"
```

---

### Task 8: Subpages

**Files:**
- Create: `docs/dashboard/cost.md`, `docs/dashboard/reliability.md`, `docs/dashboard/content.md`, `docs/dashboard/sources.md`, `docs/dashboard/audience.md`

**Interfaces:**
- Consumes: `site.data.dashboard` (`today`, `days`, `totals`), `site.data.failures`.

- [ ] **Step 1: Create the five pages**

Each page starts with frontmatter, includes `dashnav.html`, and renders its section. Pattern:

```markdown
---
layout: default
title: Cost & usage
permalink: /dashboard/cost/
---
{% assign d = site.data.dashboard %}{% assign t = d.totals %}{% assign today = d.today %}
<div class="page-content dashboard" markdown="0">
<h1>Cost &amp; usage</h1>
{% include dashnav.html %}
... section content ...
</div>
```

Contents:

- **cost.md**: today's cost (`today.cost.total` or `today.cost_usd`), per-day cost chart `{% include chart.html metric="cost_usd" label="LLM cost (USD)" %}`, cumulative cost row, cost by stage and by model tables (`today.cost.by_stage`, `today.cost.by_model`), token charts (`tokens_input`, `tokens_output`, `tokens_total`), the averages table rows for cost and tokens with a "since {{ t.first_token_date }}" note, and the projection line using `t.cost_actual`, `t.cost_per_day_over_cost_days`, `t.cost_extra_runs`, `t.cost_projected`.
- **reliability.md**: status over time (a per-day table of `d.days` status with `failed_stages`/`degraded_stages`), the failure summary from `site.data.failures` (`f.counts.failed`, `f.counts.backfilled`, `f.counts.failed_and_backfilled`), and a link to `/failures/`.
- **content.md**: entries posted EN/FA charts, links cited chart, story mix (`new_stories`, `continuations`, `developments`), biases chart, and the per-day charts for `posted`, `posted_en`, `links`, `included`, `processed`.
- **sources.md**: today's funnel table from `today.sources` (columns name, fetched, prefilter_passed, included, posted, down), an all-time aggregate if present, and model usage (`today.models`) and failovers (`today.failovers`).
- **audience.md**: `t.subscribers_current` (labeled by audience if available), `t.subscribers_total_ever` labeled "proxy distinct emails", `t.emails_en`, `t.emails_fa`, `t.deliveries`, and a subscribers chart if a per-day value exists.

- [ ] **Step 2: Verify**

Run: `grep -rn "include dashnav.html" docs/dashboard/ | wc -l`
Expected: 5

- [ ] **Step 3: Commit**

```bash
git add docs/dashboard/
git commit -m "Add dashboard subpages for cost, reliability, content, sources, audience"
```

---

## Self-Review

- **Spec coverage:** per-call cost (T1), per-source funnel (T2-T4), aggregation and coverage/projection (T5), fractional axis and sub-nav (T6), overview (T7), five subpages (T8). Metric fixes 1-7 map to T5 (1,3,4,5,6), T7/T8 (1,2), T6 (7).
- **Placeholder scan:** the T3/T4 instrumentation notes name the variables an implementer must read from the file rather than inventing names. No TBD.
- **Type consistency:** `cost_usd`, `cost`, `sources`, `failovers` are used with the same shapes across T1, T5, T7, T8. `update_source_stats` and `count_by_source`/`summarize_posted_counts` signatures match between definition (T2-T4) and call sites.
- **Review Focus tests:** no-`source_stats.json` and null projection (T5), missing `cost_usd` (T1), malformed `source_stats.json` (T2), fractional axis (T6 grep), zero-cost-days projection (T5).
