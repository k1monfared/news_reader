# Model resolution and daily cost tracking

Date: 2026-10-13
Status: draft, awaiting review

## Context

The daily brief failed on 2026-10-13 (run `2026-10-13-110833`) at the
`render_check` stage, so publish, translate_fa, and mailer were skipped. The
proximate cause was a model outage. Every model in the failover chain was
unavailable or returned empty content:

- `longcat-2.5-preview-free` returned empty content and later 429s.
- `space-bunny-free` was delisted from the Go catalog (400, "Model is unavailable").
- `deepseek-v4-flash` and `deepseek-v4-pro` returned reasoning-only empty content
  on the long editorial prompt.

Two structural weaknesses surfaced:

1. The pipeline's model list goes stale. `models.json` refreshes weekly and only
   covers free models. `config.yaml` holds a hand-maintained fallback list
   (`space-bunny-free`, `deepseek-v4-flash`, `deepseek-v4-pro`), and nothing
   prunes a delisted id or adds a newly available model.
2. Cost is invisible. `AuditedLLMClient` hardcodes token prices to zero, so
   `run_meta.total_cost_usd` is always `0.0`, `data/run_metrics.jsonl` stores no
   cost, and the dashboard shows tokens only. `COSTS.md` is a historical
   write-up from the Poe/Claude era.

`deepseek-v4.1-flash` is reliable and cheap on the Go catalog (Go plan, peak
rates $0.30 per M input and $1.20 per M output, off-peak half that). The most
recent six successful runs averaged about 278k input tokens and 38k output
tokens, which is roughly $0.13 per run at peak and about $4 per month for daily
runs. The Go plan includes an $80 per month allowance for this model, so it fits
comfortably.

## Goals

- Run the pipeline on `deepseek-v4.1-flash` as the primary model.
- Keep the free-model machinery as a self-refreshing backstop that works when
  the primary fails, is delisted, or becomes too expensive.
- Make the backstop self-maintaining so the model list is never hand-edited
  again.
- Track and display dollar cost per run and per day on the dashboard.
- Move the daily run to a 6am PT start.

## Non-goals

- No changes to prompts, stage logic, or the editorial flow.
- No peak/off-peak rate precision (a single conservative rate per model).
- No backfill of historical costs into old `run_metrics.jsonl` records.
- No paid models as backstops (the backstop is free models only).

## Decisions

1. Primary model: `deepseek-v4.1-flash` for both English and Farsi stages.
2. Backstop: free models discovered live from the Go catalog each run, probed
   for liveness, free only. No other paid models.
3. Discovery placement: resolve models in `run_pipeline` before the client is
   constructed, not inside the client and not as a pipeline stage.
4. Fallback cache: the run writes the discovered free models to `models.json`
   and commits it. If discovery fails, the client uses the last committed
   `models.json`. The weekly `Select Free Models` workflow stays as a safety
   refresh.
5. Cost rates: flat peak rates from a config table. Free models are absent from
   the table and price at $0.
6. Schedule: daily cron at 13:07 UTC (6:07am PDT).

## Design

### Section 1: Model resolution

**`config.yaml` `models`:**

```yaml
models:
  default: deepseek-v4.1-flash
  translate_fa: deepseek-v4.1-flash
  fallbacks: []
  selected_models_file: models.json
```

- `fallbacks` becomes empty. The free backstop is the discovered set, not a
  static list.
- `paid_first_stages` is removed. It existed to send long prompts to paid models
  before free ones. The primary is now paid and always first, so the flag has no
  meaning.

**New `model_discovery.py` at the repo root:**

```python
def discover_free_models(
    base_url: str,
    api_key: str,
    session_id: str = "",
    *,
    timeout_s: int = 10,
) -> list[dict]:
    """Probe the catalog's free models and return the ones that answer.

    Returns [{"id": ..., "base_url": ...}] for free models that returned
    non-empty content on /chat/completions. Returns [] on any failure.
    """
```

- Fetches `{base_url}/models` (reuses `discover`).
- Keeps ids ending in `-free`.
- Probes each on `/chat/completions` with a tiny prompt (reuses `probe_chat`
  and `classify`).
- The probe and classify helpers live in `scripts/probe_models.py`. Because
  `scripts` is already imported as a namespace package (the tests do
  `from scripts.select_models import ...`), `model_discovery` imports them
  directly. No code is moved.
- Free only, no paid probing. The Go catalog lists three free models today
  (`longcat-2.5-preview-free`, `step-5-preview-free`, `cat-jam-free`), so the
  probe cost is a few hundred milliseconds.

**`run_pipeline.run_pipeline` (`run_pipeline.py:200`):**

- Before constructing `AuditedLLMClient`, call `discover_free_models` with the
  resolved base, key, and session id from the environment.
- Write the result to `models.json` in the schema the client already reads:

```json
{
  "generated_at": "2026-10-13T13:07:00+00:00",
  "bases_probed": ["https://opencode.ai/zen/go/v1"],
  "selected": [{"id": "longcat-2.5-preview-free", "base_url": "https://opencode.ai/zen/go/v1"}],
  "probes": []
}
```

- If discovery returns an empty list, leave the existing `models.json`
  untouched. That keeps the last known good set as the fallback cache.
- Commit `models.json` at the end through the existing `_git_commit_file` helper
  (the same one metrics uses for its commits).

**`llm_client.AuditedLLMClient`:**

- The chain becomes **primary first, then `selected_models`**. This flips the
  current default (which put free models first). The `paid_first_stages` branch
  is deleted.
- The loading of `selected_models_file` is unchanged.

**Resulting behavior:**

- Normal: `deepseek-v4.1-flash`, then the freshly discovered free Go models.
- Discovery down: `deepseek-v4.1-flash`, then the last committed `models.json`.
- Both unavailable: `deepseek-v4.1-flash` only.

### Section 2: Cost tracking

**`config.yaml`:** a flat peak-rate table. Free models are absent and price at
$0.

```yaml
pricing:
  deepseek-v4.1-flash: { input: 0.30, output: 1.20 }
  deepseek-v4-flash:   { input: 0.30, output: 1.20 }
  deepseek-v4-pro:     { input: 1.32, output: 3.96 }
```

Rates are dollars per million tokens. The three paid rows are included so a
future paid failover prices correctly even though the current backstop is free
only.

**`llm_client.AuditedLLMClient`:**

- Load `models.pricing` in `__init__`.
- Add `_cost_for(model, tokens_in, tokens_out) -> float`, defaulting to `0.0`
  for any model not in the table. A missing paid row therefore understates cost
  rather than breaking the run, which is the safe direction for a backstop.
- Replace the hardcoded `_INPUT_COST_PER_M` and `_OUTPUT_COST_PER_M` usage in
  `call()` with a lookup on the **served** model (the model that actually
  answered after failover). A free-model fallback therefore bills $0, and a paid
  failover bills at the paid rate.
- Bill `tokens_out` as the completion total. Reasoning tokens are included in
  `tokens_out` by the provider, so they are not added a second time.
- `total_cost` keeps its current meaning and the per-run budget cap becomes
  meaningful again.

**`stages/metrics.py`:**

- `build_run_record` adds `cost_usd` (rounded to 4 decimals) from
  `run_meta.total_cost_usd`.
- `_day_from_record` carries `cost_usd`. `_day_from_posts` sets it to `None`.
- `recompute_totals` adds a `cost_usd` sum and a `rates.cost_usd` entry using the
  existing `_rate` helper.

**`docs/dashboard.md` and `docs/assets/js/dashboard.js`:**

- Add a "Cost this run" card next to the token card.
- Add a `cost_usd` per-day metric mapping in the JS.
- Add an all-time cost figure in the totals area.

### Section 3: Schedule

**`.github/workflows/daily-brief.yml`:** cron `7 11 * * *` becomes
`7 13 * * *`. That is 6:07am PDT. In PST (roughly November to March) it lands at
5:07am. The comment is updated to state the drift, matching the existing style
that already documents the offset.

**`config.yaml`:** `schedule.run_hour` changes from `8` to `6` for consistency.
Nothing reads this field today, so it is documentation only.

## Files touched

- `config.yaml` (models, pricing, schedule)
- `model_discovery.py` (new)
- `run_pipeline.py` (discovery call, models.json write and commit)
- `llm_client.py` (chain order, cost lookup)
- `stages/metrics.py` (cost in record, days, totals)
- `docs/dashboard.md`, `docs/assets/js/dashboard.js` (cost display)
- `.github/workflows/daily-brief.yml` (cron)
- `tests/` (new and updated tests)

## Testing

- `discover_free_models`: mocked `discover`/`probe_chat`, covering a mix of OK,
  429, 403, and empty-content responses, and a total network failure returning
  `[]`.
- `_cost_for`: paid model, free or unknown model, and zero tokens.
- `call()`: verify the served model's rate is applied after a failover (mock a
  first model failing and a second answering).
- `metrics`: `build_run_record` carries `cost_usd`, and `recompute_totals`
  aggregates it.
- `test_select_models.py`: update the chain-order assertions to primary-first.
- Run `pytest tests/` before any commit.

## Risks and open questions

- The pricing table is hand-maintained and can drift from the catalog like the
  old model list did. Peak rates are conservative, so drift understates cost
  rather than overstating it. A future improvement could fetch rates from a
  source, but the catalog `/models` endpoint returned no pricing.
- Committing `models.json` every run adds one commit per day. This mirrors the
  existing per-run metrics commit, so it is not a new pattern.
- The 6am PT cron cannot follow daylight saving. It is off by one hour in PST.
  This matches how the existing schedule already accepts a fixed UTC offset.
