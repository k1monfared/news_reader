# Dashboard redesign (operator console) and metric corrections

Date: 2026-10-13
Status: draft, awaiting review

## Context

The `/dashboard/` page is one long scroll that mixes three audiences and publishes several numbers that mislead or contradict each other. Investigation of the published `docs/_data/dashboard.json` confirmed concrete defects:

1. `days_running` (230, a calendar span) sits next to `briefs_en` (222) with no explanation. Eight days have no EN brief: seven mid-August days with no run record (`2026-08-01, 08, 10, 12, 13, 14, 15`) and `2026-08-25`, which is absent entirely.
2. "2059 links processed" is `funnel.fetched` summed over only the 7 days with run records, while "2192 source links cited" is unique cited URLs summed over 214 days with posts. Different metric, different coverage, adjacent on the page.
3. "9 subscribers now" (last recorded Resend audience count) vs "7 total ever subscribed" (`distinct_emails` from the subscribe proxy). Different sources, presented as if comparable.
4. "210 LLM calls / 2,831,695 tokens" covers only the 8 days that have audit data (2026-10-06 onward), not the 230-day span.
5. Cost shows the summed actual (1 day so far, $0.1218) as if it were the all-time total, with a `/day` sub that is the single-day average. The extrapolated estimate (`rates.cost_usd.estimated_total`) exists but is hidden.
6. "Failed days: 0" counts only days whose current status is `failed`. Today's failed run was later superseded, so it reads success. The real history is in `failures.json`: 19 failed, 16 backfilled, 11 repaired.
7. The per-day cost chart y-axis renders `1 / 0 / 0` because `dashboard.js` floors `max` at 1 and computes `mid = floor(max/2)`, which is wrong for sub-$1 values.

A live example of the operator need: Al Jazeera fell from ~97% of briefs (Mar-Jun) to 0 (since 2026-10-06). The cause was downstream (its items lack the 22 hardcoded Iran keywords, so the fail-closed pre-filter drops them), but nothing on the dashboard surfaced it. The dashboard cannot currently answer "is a source quietly contributing less".

The site is public but the dashboard is operator-facing (confirmed with the owner). Scope is the English dashboard only.

## Goals

- Rebuild the dashboard as an operator console that answers six questions: run health, cost and usage, reliability and failures, content output, sources and models, audience.
- Split it into a hub overview plus five focused subpages, with a sub-nav.
- Correct the misleading metrics and label coverage honestly.
- Add a per-source funnel so a source's contribution collapse (the Al Jazeera case) becomes visible.
- Fix the fractional chart axis.

## Non-goals

- No changes to the pipeline stages' selection behavior. In particular Al Jazeera keeps its current RSS feed unchanged, and the Iran keyword list is unchanged. This work only makes the outcome observable.
- No new services, no client-side framework. Static Jekyll reading `_data` JSON, consistent with the current site.
- No Farsi dashboard.

## Decisions

1. Hub and spoke: `/dashboard/` overview plus `/dashboard/cost/`, `/dashboard/reliability/`, `/dashboard/content/`, `/dashboard/sources/`, `/dashboard/audience/`, with a sub-nav across all six.
2. Operator audience, so engineering detail is welcome.
3. One `dashboard.json` with per-page sections, as today.
4. Cost is broken down by stage and model by recording per-call cost in the audit.
5. Add a per-source funnel: fetched, prefilter_passed, included, posted, down.
6. Extrapolated cost includes an explicit retry/backfill allowance derived from `failures.json` (backfilled dates plus failed-not-backfilled dates), not a fixed factor.
7. Al Jazeera and all sources remain configured as-is.

## Design

### Data layer

`stages/metrics.py` keeps building `docs/_data/dashboard.json`. New/changed inputs:

**Per-call cost in the audit.** `llm_client.AuditedLLMClient.call` already computes `call_cost`. Add `"cost_usd": call_cost` to the summary line it appends to `audit/llm_calls.jsonl`. `read_audit` then sums cost per stage and per model. The run record's `tokens.by_stage[stage]` gains `cost_usd`, and a new `cost` block carries `by_stage`, `by_model`, and `total`.

**Per-source funnel.** A new per-run file `source_stats.json` accumulates across stages:

```json
{
  "aljazeera": {"fetched": 25, "down": false, "prefilter_passed": 1, "included": 0, "posted": 0},
  "bbc": {"fetched": 35, "down": false, "prefilter_passed": 7, "included": 5, "posted": 3}
}
```

- `stages/fetch.py` writes `fetched` and `down` per source (it already computes `items_by_source` and `sources_down`).
- `stages/filter.py` adds `prefilter_passed` (items that cleared `_has_iran_signal`) and `included` (LLM INCLUDE, after post-filter), keyed by `item.source`.
- `stages/summarize.py` adds `posted` for sources whose items reached the final report (it already iterates included items, each with `.source` and `.related_sources`).

A small shared helper (`stages/source_stats.py`: `update_source_stats(run_dir, source, **counters)` and `merge_update`) keeps each stage's write atomic and additive. Stages that never touch a source's key leave it absent, which the dashboard renders as "not recorded".

**Run record additions** (`build_run_record`):

- `sources`: the per-source funnel merged to a list.
- `cost`: `by_stage`, `by_model`, `total` (from the audit), plus a per-day `cost_usd` as today.
- `failovers`: model failover counts by model (from the audit's `failed_over_from`), for the Sources and models page.
- `status` already exists per day.

**Totals additions** (`recompute_totals`):

- Coverage: `days_with_tokens`, `days_with_cost`, `first_token_date`, `tokens_days_covered`, so token/call cards can say "since 2026-10-06".
- `cost_actual` (sum over cost days), `cost_per_day_over_cost_days`, and `cost_projected` = `cost_per_day_over_cost_days * (days_running + extra_runs)`. `extra_runs` is derived from `failures.json`: the number of backfilled dates plus the number of failed-not-backfilled dates. Each backfilled day is an extra run whose cost is not in the one-run-per-day baseline, and each failed-not-backfilled day is a run that consumed tokens without producing a brief. The page states the count and the derivation, so the projection is auditable rather than a magic multiplier.
- Reliability: `days_success`, `days_degraded`, `days_failed_ever` (from `failures.json` failed dates, not just current status), `days_repaired` (failed and backfilled).

The dashboard page templates read `site.data.failures` for the failure history, as `/failures/` already does, rather than duplicating it.

### Pages and navigation

A new `docs/_includes/dashnav.html` renders tabs: Overview, Cost, Reliability, Content, Sources, Audience. Each dashboard page includes it under the H1.

- **`/dashboard/` (overview).** Live run banner (as today). Today's run condensed: status, duration, entries, links, cost, emails. Headline strip with honest labels: briefs vs days running (with a "N days without a brief" note), all-time cost actual and projected, subscribers current, failures repaired. A source-health line flagging any source whose last-7-day prefilter pass rate is 0 (this is the Al Jazeera signal). Link cards to the five subpages.
- **`/dashboard/cost/`.** Today's cost, per-day cost chart (fixed axis), cumulative cost. Cost by stage and by model. Tokens and calls per day and per stage, with a "since <first_token_date>" note. Averages table (per day/week/month over days-with-data) and the projected total with the retry/backfill note.
- **`/dashboard/reliability/`.** Status over time (success, degraded, failed, repaired). Failure summary from `failures.json` (failed, backfilled, repaired, failed-not-backfilled), linking to `/failures/`.
- **`/dashboard/content/`.** Entries posted EN/FA, links cited, story mix (new, continuation, development), category distribution, biases. Link metrics grouped here with consistent coverage labels so they stop being compared across incompatible day sets.
- **`/dashboard/sources/`.** The per-source funnel table (fetched, prefilter_passed, included, posted, down) for today and a per-day trend, sources down over time, and model usage plus failover counts.
- **`/dashboard/audience/`.** Subscribers current (by audience, EN/FA) and total-ever (labeled as proxy distinct emails), email sends EN/FA, deliveries, growth.

The top nav keeps a single "Dashboard" entry pointing at the overview.

### Metric definitions (fixes)

1. Label calendar span vs posts, and show the missing-brief count explicitly.
2. Keep link-cited metrics on the Content page, over their true coverage, separately from fetched counts.
3. Label subscriber current and ever by source.
4. Prefix token/call totals with "since <first_token_date>" and show days covered.
5. Cost card shows actual, average over cost days, and projected total with the retry/backfill allowance.
6. Failed-days figure uses `failures.json` failed dates (including repaired ones), with repaired shown alongside.
7. `dashboard.js` chart axis supports fractional metrics: do not floor `max` at 1 when the metric is fractional (cost), compute `mid = max / 2` without integer flooring, and format axis labels to a few decimals for the cost chart.

## Files touched

- `llm_client.py` (per-call cost in the audit summary)
- `stages/source_stats.py` (new shared helper)
- `stages/fetch.py`, `stages/filter.py`, `stages/summarize.py` (per-source funnel)
- `stages/metrics.py` (record and total additions)
- `config.yaml` (dashboard/metrics config, if any labels need to be configurable)
- `docs/dashboard.md` (becomes the overview), `docs/dashboard/cost.md`, `reliability.md`, `content.md`, `sources.md`, `audience.md` (new)
- `docs/_includes/dashnav.html` (new)
- `docs/assets/js/dashboard.js` (fractional axis; new charts)
- `docs/assets/css/style.css` (sub-nav and any new classes)
- `tests/` (new and updated)

## Testing

- `read_audit` sums per-stage and per-model cost from a synthetic `llm_calls.jsonl` that includes `cost_usd`.
- `source_stats.update_source_stats` merges additively and is idempotent per key.
- `build_run_record` carries `sources`, `cost`, and `failovers`; `recompute_totals` exposes coverage, cost actual/per-day/projected (with the retry factor), and reliability counts including repaired days.
- Fetch/filter/summarize write the expected per-source keys, with the funnel counts derived from fixtures.
- Existing tests updated where record shape changed.
- Jekyll render is not exercised locally (no toolchain); the pages are verified by grep and by the next publish, noted as an open verification like the earlier cost card.

## Risks and open questions

- The per-source funnel's `posted` count depends on summarize attributing finalized items to sources. Related sources contribute to a cluster, so `posted` counts the primary's source plus any related sources that appear. This is an approximation and should be labeled as "cited" rather than "owned".
- `source_stats.json` is new per-run state; a stage that runs but writes nothing (e.g., all sources down) must not break the merge.
- Historical runs have no per-source funnel, so the Sources trend will be empty until data accumulates. The page must render that honestly.
- The retry/backfill allowance is derived from `failures.json` counts, so the projection depends on that file being current. It is refreshed by the daily Build Failures workflow; the cost page states `extra_runs` and its breakdown so the number is auditable.
