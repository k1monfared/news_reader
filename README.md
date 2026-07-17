# USrael War on Iran: Daily Brief

**Status**: Beta | **Mode**: Claude Code | **Updated**: 2026-04-20

Automated daily news digest covering the US-Israel war on Iran. A GitHub Actions cron pulls articles from four RSS sources, deduplicates events, filters for importance, categorizes, tracks cross-day story development, runs an editorial + bias-detection pass, verifies every link, writes a Jekyll post, translates it to Farsi, and sends the finished brief as an email broadcast to confirmed subscribers. Everything runs on free tiers: GitHub Actions, GitHub Pages, Cloudflare Workers + KV + D1, Resend. To debias the coverage, the pipeline weights each item by its source's known slant, cross-checks the same event across the four feeds, and flags contradictions and sole-source claims in the brief, so readers see the story rather than a single outlet's framing.

**Live site**: https://k1monfared.github.io/news_reader/ (English) and https://k1monfared.github.io/news_reader/fa/ (Farsi).

## Architecture

```
           +----------------------+
           | GitHub Actions cron  |
           | (.github/workflows)  |
           +----------+-----------+
                      |
                      v
           +----------------------+
           |   run_pipeline.py    |
           |  12 stages in order  |
           +----------+-----------+
                      |
         +------------+------------+
         |            |            |
         v            v            v
  docs/_posts/   docs/_fa_posts/  Resend broadcast
    (Jekyll)      (Jekyll)        to subscribers
         \_______________________/
                     |
                     v
            GitHub Pages rebuild
```

Subscribe form on the Jekyll site posts to a separate Cloudflare Worker (`subscribe-proxy/`, vendored from [newsletter_base](https://github.com/k1monfared/newsletter_base)) that implements double opt-in, rate limiting, a permanent block list, and writes every attempt to a private Cloudflare D1 audit log.

## Pipeline stages

The orchestrator runs each stage in order. Failures in non-critical stages are logged and skipped; the pipeline continues. `config.yaml` toggles optional stages.

| # | Stage | What it does |
|---|---|---|
| 1 | `fetch` | Pulls articles from Al Jazeera, Reuters, France 24, Euronews RSS. |
| 2 | `translate` | Translates any non-English items to English via LLM. |
| 3 | `dedup` | TF-IDF cosine similarity clustering; picks a primary item per cluster. |
| 4 | `filter` | LLM importance classification with source-bias awareness; cached. |
| 5 | `categorize` | LLM topic bucketing into Diplomacy, Military, US Policy, etc. |
| 6 | `track_developments` | LLM compares each item against the last 7 days of posts: new / continuation / development. |
| 7 | `summarize` | LLM writes the brief using the expandable `<details>` format. |
| 8 | `editorial` | LLM reviews for contradictions, flags sole-source claims, detects new source bias patterns. |
| 9 | `verify` | HTTP HEAD checks every URL in the draft. |
| 10 | `publish` | Writes `docs/_posts/{date}-daily-brief.md`. If `translate_fa` is enabled, git commit is deferred so both languages ship together. |
| 11 | `translate_fa` | LLM translates the brief and any new bias entries to Farsi; writes `docs/_fa_posts/`; atomic git commit + push covering both languages. |
| 12 | `mailer` | Sends the brief as a Resend broadcast to the English and Farsi audience segments. Uses `{{{RESEND_UNSUBSCRIBE_URL}}}` per-recipient substitution. |

See `COSTS.md` for measured token usage and daily cost per stage.

## Setup

```bash
git clone https://github.com/k1monfared/news_reader.git
cd news_reader
pip install -r requirements.txt
cp .env.example .env  # then edit
```

`requirements.txt` pulls in [`newsletter-base`](https://github.com/k1monfared/newsletter_base) from GitHub, which provides the `newsletter` and `resend_broadcast` Python packages used by `stages/mailer.py`.

### Environment variables

| Variable | Purpose |
|---|---|
| `ANTHROPIC_AUTH_TOKEN` | LLM auth. This project uses the Poe API as a Claude proxy. |
| `ANTHROPIC_BASE_URL` | `https://api.poe.com` (or an alternative Claude-compatible endpoint). |
| `RESEND_API_KEY` | Only needed by the mailer stage. Omit for dry runs. |

In GitHub Actions these are set as repository secrets under **Settings → Secrets and variables → Actions**.

### Cloudflare side (one-time)

The subscribe flow runs on a Cloudflare Worker. See [`subscribe-proxy/README.md`](subscribe-proxy/README.md) for the setup:

```bash
cd subscribe-proxy
npm install
npx wrangler login
cp wrangler.example.toml wrangler.toml  # edit SITE_NAME, FROM_ADDR, URLs
npx wrangler kv namespace create SUBSCRIBE_KV          # paste id into wrangler.toml
npx wrangler d1 create subscribe-audit                 # paste id into wrangler.toml
npx wrangler d1 execute subscribe-audit --remote --file=./schema.sql
npx wrangler secret put RESEND_API_KEY
npx wrangler secret put AUDIENCE_ID
npx wrangler secret put AUDIENCE_ID_FA
npx wrangler deploy
```

Copy the printed Worker URL into `docs/_config.yml` as `subscribe_proxy_url`.

## Running

### Scheduled (production)

The GitHub Actions workflow at `.github/workflows/daily-brief.yml` runs daily at 5am PDT (12:07 UTC). Manual trigger: **Actions → Daily Brief → Run workflow**.

### Manually, from a local shell

```bash
export ANTHROPIC_AUTH_TOKEN=...
export ANTHROPIC_BASE_URL=https://api.poe.com
export RESEND_API_KEY=re_...   # only if mailer.enabled: true
python run_pipeline.py
```

This runs the full pipeline for today. If any prior days are missing, it backfills them first. On success it commits and pushes the new post(s) and sends the broadcast.

### Preview a specific day to one recipient

```bash
export RESEND_API_KEY=re_...
python scripts/send_mail_to_address.py 2026-04-20 fa someone@example.com
```

Renders the Farsi post for 2026-04-20 and sends it only to that address via Resend's transactional endpoint. Does not touch any audience.

### Re-send a finished post to the full audience

```bash
export RESEND_API_KEY=re_...
python scripts/send_mail_for_date.py 2026-04-20
```

Reuses the English and Farsi post files already on disk. Does not re-run the full pipeline.

### Tests

```bash
pytest tests/
```

Tests use mocked LLM and HTTP clients; no network calls required.

## Project structure

```
config.yaml              # all tunable parameters (sources, buckets, budget, flags)
models.py                # Pydantic data models
llm_client.py            # audited LLM wrapper (tokens, cost, budget cap)
audit_logger.py          # audited HTTP wrapper
prompt_loader.py         # YAML prompt template loader
run_pipeline.py          # orchestrator with per-day backfill
bias_tracker.py          # source bias pattern store
backfill.py              # standalone backfill entry

stages/
  fetch.py               # RSS source fetching
  fetchers/              # per-source fetcher classes behind BaseFetcher
  translate.py           # non-English -> English translation
  dedup.py               # TF-IDF deduplication
  filter.py              # importance filtering
  categorize.py          # topic bucketing
  track_developments.py  # cross-day story tracking
  summarize.py           # report generation
  editorial.py           # editorial review + bias detection
  verify.py              # URL verification
  publish.py             # write Jekyll post
  translate_fa.py        # Farsi translation + atomic commit/push
  mailer.py              # send Resend broadcast via newsletter_base

prompts/*.yaml           # versioned prompt templates
docs/                    # Jekyll site (GitHub Pages builds from here)
  _posts/                # published English daily briefs
  _fa_posts/             # Farsi parallel posts
  _data/                 # i18n strings, source bias records
  _layouts/              # default and post templates
  assets/                # CSS, JS
  subscribe.md, subscribed.md, confirmation-sent.md, blocked.md
  fa/                    # Farsi root + equivalents of the above
  about.md               # project description + bias transparency

subscribe-proxy/         # Cloudflare Worker (vendored from newsletter_base)
  src/worker.ts          # double-opt-in, rate limit, block list, D1 audit
  schema.sql             # D1 table schema
  wrangler.toml          # project-specific ids and URLs (gitignored values)

scripts/
  send_mail_for_date.py  # send the full broadcast for a date (reuses disk posts)
  send_mail_to_address.py  # send one rendered post to one recipient
  consolidate_biases.py
  populate_examples.py
  translate_static_fa.py

tests/                   # pytest suite, mocked LLM + HTTP
data/runs/               # per-run output and audit trails (gitignored)

.github/workflows/       # daily-brief.yml: scheduled pipeline run
schedule/                # legacy local-cron setup; not used in production
```

## What each run saves

Every pipeline run writes a complete paper trail to `data/runs/{run_id}/`. Gitignored, so it lives only on the machine that executed the run. GitHub Actions runs save these to an ephemeral VM that is discarded.

**Article snapshots at each stage** (in the run root):

| File | Contents |
|---|---|
| `raw_items.json` | Every article fetched from the RSS sources: title, text, URL, timestamp, source name, language |
| `translated_items.json` | Same items with `text_en` / `title_en` added if they needed translation |
| `deduped_items.json` | After clustering, with `event_id` and `is_primary` flags |
| `filtered_items.json` | Each item tagged `included` / `excluded` with `confidence` and one-line `filter_reason` |
| `categorized_items.json` | Included items with `primary_category` and `secondary_category` |
| `tracked_items.json` | With `story_status` (new / continuation / development) and `story_timeline` |

**Report drafts** (in the run root):

- `report.md` — first draft from summarize
- `report_edited.md` — after editorial review
- `report_verified.md` — after URL check
- `headlines.txt` — short terminal summary

**LLM and HTTP audit trail** (in `audit/`):

- `llm_calls.jsonl` — one line per LLM call: stage, tokens in/out, duration, call_id, input/output hash
- `llm_inputs/{call_id}.json` — the exact system prompt + user message sent to the model
- `llm_outputs/{call_id}.json` — the model's full response
- `api_calls.jsonl` — every HTTP call to RSS sources, with status code and response size
- `dedup_similarity_matrix.json` — pairwise TF-IDF similarity scores

Each run is roughly 1 to 5 MB.

## Subscribers and mailing list

Subscribers are managed by Resend, not stored in this repo. The block list and pending-confirmation tokens live in a private Cloudflare KV namespace. Every subscribe / confirm / block attempt is logged to a private Cloudflare D1 database. None of this data is accessible via the public site.

To see recent subscribe activity:

```bash
cd subscribe-proxy
npx wrangler d1 execute subscribe-audit --remote \
  --command="SELECT ts, event, outcome, email, ip, country FROM subscribe_logs ORDER BY id DESC LIMIT 20"
```

Bot hunting:

```bash
npx wrangler d1 execute subscribe-audit --remote \
  --command="SELECT ip, country, count(*) AS n FROM subscribe_logs WHERE ts > datetime('now','-24 hours') GROUP BY ip, country ORDER BY n DESC"
```

To view or export the subscriber list, use the Resend dashboard directly, or the [newsletter_base CLI](https://github.com/k1monfared/newsletter_base) installed via the dependency.

## Configuration

All tunable parameters live in `config.yaml`:

- `sources`: RSS feeds, bias notes, reliability notes.
- `models.default`: LLM model name (currently `claude-sonnet-4-5`).
- `buckets`: topic categories with keyword hints.
- `schedule`: timezone, run hour.
- `budget`: per-run cost cap in USD.
- `pipeline`: similarity thresholds, confidence thresholds, cache TTL.
- `publish`: site dir, branch, publish method.
- `translate_fa`: toggle + fixed category translations.
- `mailer`: toggle, from address, audience ids, site URL.

Prompts are in `prompts/*.yaml` with version numbers that the audit log records per call, so a regression in output can be traced back to a specific prompt version.

## Documentation

- `STATUS.log` — project status and progress tracking
- `CLAUDE.md` — project-specific instructions for Claude Code sessions
- `COSTS.md` — measured per-stage token usage and daily cost
- `config.yaml` — all configuration with inline comments

## Contributing

This is primarily a personal-use project published publicly so others can see how it works. Issues and pull requests are welcome but not the priority.

The generic newsletter plumbing (subscribe Worker, send library, Jekyll templates) is maintained separately at https://github.com/k1monfared/newsletter_base. If your fix is generic, consider contributing there; it will flow back here the next time this project syncs from upstream.
