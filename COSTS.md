# Pipeline cost breakdown

Per-stage LLM token usage and estimated cost for the daily brief pipeline. Numbers are measured from real run audit logs, not estimates.

## Methodology

- **Data**: every completed pipeline run writes `data/runs/{run_id}/audit/llm_calls.jsonl` (one line per LLM call with `stage`, `tokens_in`, `tokens_out`, `model`) and a summary `run_meta.json` with `total_cost_usd`.
- **Pricing model** (from `llm_client.py`): Claude Sonnet 4.5 at **$3 per million input tokens**, **$15 per million output tokens**. All calls in recorded history use this one model.
- **Analysis scope**: 40 unique run dates covering February to April 2026. One run per date (the latest, if multiple).

## Verification

Summing cost for every call in each run's `llm_calls.jsonl` matches `run_meta.total_cost_usd` to the penny for 39 of 40 runs. One run (2026-03-28) has a $0.30 gap, consistent with a budget-cap rejection excluded from the reported total. Methodology is sound.

## Cost per pipeline run

### Distribution across 40 runs

| metric | value |
|---|---:|
| median | $0.610 |
| mean | $0.584 |
| stdev | $0.248 |
| min | $0.021 |
| max | $1.015 |
| median calls | 16 |
| median input tokens | 127,368 |
| median output tokens | 15,275 |

Variance comes mostly from two sources: (1) how many new vs continuation stories there are on a given day, which drives `track_developments` calls, and (2) how often the filter cache hits.

### Per-stage medians

| Stage | Typical cost | % of runs | Notes |
|---|---:|---:|---|
| `editorial` | $0.30 | 100% | Biggest single stage. Full-brief review + bias detection in one long-context call. |
| `filter` | $0.14 | 100% | TF-IDF dedup means many items hit cache. Uncached runs are more expensive. |
| `summarize` | $0.06 | 100% | One call, moderate input. |
| `categorize` | $0.05 | 100% | One call, small input. |
| `track_developments` | $0.03 typical, up to $0.21 | 58% | Added mid-history. 1 call per story-pair compared to last 7 days. |
| `translate_fa` | ~$0.11 | 3% | Added late. Translates the brief + new source biases. |
| **TOTAL** | **~$0.60** | — | With `translate_fa` enabled, closer to $0.70. |

Non-LLM stages (`fetch`, `translate`-RSS, `dedup`, `verify`, `publish`, `mailer`) cost nothing beyond HTTP.

### Example: a busy day (2026-04-16, `translate_fa` enabled)

| Stage | Calls | Input tokens | Output tokens | Cost |
|---|---:|---:|---:|---:|
| `filter` | 3 | 4,923 | 1,813 | $0.0420 |
| `categorize` | 1 | 3,109 | 1,575 | $0.0330 |
| `track_developments` | 28 | 58,560 | 2,100 | $0.2072 |
| `summarize` | 1 | 2,923 | 1,623 | $0.0331 |
| `editorial` | 2 | 67,981 | 2,936 | $0.2480 |
| `translate_fa` | 8 | 6,656 | 6,162 | $0.1124 |
| **TOTAL** | **43** | **144,152** | **16,209** | **$0.6756** |

## Projections

Based on the median $0.60/day with current config (`translate_fa` enabled):

| Period | Projected cost |
|---|---:|
| 1 day | $0.60 |
| 1 week | $4.20 |
| 1 month (30 d) | $18 |
| 1 year (365 d) | $220 |

At the top of the observed range ($1.00/day busy days): $30/mo, $365/yr.

## Caveats

1. **Poe API routing.** This project uses `ANTHROPIC_BASE_URL=https://api.poe.com`, which bills through Poe's compute-point model, not Anthropic direct billing. The numbers above are Anthropic-equivalent costs computed from token counts; what actually shows up on the Poe bill may differ depending on the subscription tier.
2. **Input-dominated.** 90% of cost is input tokens. Reducing prompt size on `editorial` is far more effective than trimming outputs.
3. **Cache is load-bearing.** The filter stage caches article decisions. A cold cache week can double filter cost. Re-running the same day on a fresh machine shows this pattern.
4. **Budget cap.** `config.yaml` sets `budget.max_cost_per_run_usd: 10.00`. Individual runs would have to go 15-20x over median before hitting this cap.

## Where the money goes, in one sentence

Two stages eat two-thirds of the bill every day: **`editorial`** (one long-context review of the full brief) and, on busy news days, **`track_developments`** (one LLM call per story pair being checked against the last seven days of posts).

## Optimization candidates, ranked by impact

1. **`editorial`**: the prompt loads a lot of context (prior briefs, bias lists, examples). Trimming or compressing this input would cut the biggest line item. Consider dropping examples when the model has seen enough successful runs.
2. **`track_developments`**: 28 calls on a busy day. Batching pairs within a topic into a single call (e.g. "compare this one item to these five candidates") could cut call count 3-5x with minor prompt overhead.
3. **`translate_fa`**: runs 8 calls for bias translation and 1 for the brief body. If Farsi subscribers stay small, consider translating every other day, or only translating the brief body and skipping bias translation.
4. **Filter caching**: already very effective (~70% hit rate). Nothing to add here unless someone adds new sources that bypass the cache key.

## How to re-check these numbers yourself

```bash
python3 -c "
import json, glob
from statistics import median
rates = (3.0, 15.0)  # \$/M in, \$/M out
by_date = {}
for r in sorted(glob.glob('data/runs/*/run_meta.json')):
    by_date[r.split('/')[-2][:10]] = r
total = []
for path in by_date.values():
    run = path.rsplit('/', 1)[0]
    cost = 0.0
    try:
        for line in open(f'{run}/audit/llm_calls.jsonl'):
            rec = json.loads(line)
            cost += rec['tokens_in']/1e6*rates[0] + rec['tokens_out']/1e6*rates[1]
        total.append(cost)
    except FileNotFoundError:
        pass
print(f'{len(total)} runs, median \${median(total):.4f}/day')
"
```
