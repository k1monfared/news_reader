# USrael War on Iran: Daily Brief

**Status**: 🟡 MVP | **Mode**: 🤖 Claude Code | **Updated**: 2026-04-20

Automated daily news digest pipeline covering the US-Israel war on Iran. Fetches from 4 RSS sources, deduplicates events, filters by importance, categorizes, runs an editorial pass, verifies links, publishes a blog post, and optionally translates to Farsi and emails subscribers. Published to GitHub Pages.

## Pipeline

```
cron (daily 8am Pacific) via GitHub Actions
  -> fetch               (Al Jazeera, Reuters, France 24, Euronews RSS)
  -> translate           (any non-English items -> English via LLM)
  -> dedup               (TF-IDF cosine similarity clustering)
  -> filter              (LLM importance classification with source bias awareness)
  -> categorize          (LLM topic bucketing)
  -> track_developments  (LLM comparison against the last 7 days of posts)
  -> summarize           (LLM report generation with expandable format)
  -> editorial           (LLM review for contradictions, bias detection)
  -> verify              (HTTP HEAD checks on all URLs)
  -> publish             (write Jekyll post to docs/_posts/)
  -> translate_fa        (LLM translation to Farsi, write docs/_fa_posts/, git commit + push)
  -> mailer              (send broadcast to Resend audiences via newsletter_base)
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env  # fill in API credentials
```

Environment variables (Poe API):
- `ANTHROPIC_AUTH_TOKEN` -- your Poe API key
- `ANTHROPIC_BASE_URL` -- https://api.poe.com

## Usage

```bash
# Run the pipeline manually
python run_pipeline.py

# Install cron job (8am Pacific daily)
bash schedule/setup.sh

# Run tests
pytest tests/
```

## Project Structure

```
config.yaml              # all tunable parameters
models.py                # Pydantic data models
llm_client.py            # audited LLM wrapper
audit_logger.py          # audited HTTP wrapper
run_pipeline.py          # orchestrator with backfill
prompt_loader.py         # YAML prompt template loader

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
  mailer.py              # send broadcast via newsletter_base / Resend

prompts/*.yaml           # versioned prompt templates
docs/                    # Jekyll site (GitHub Pages builds from here)
  _posts/                # published English daily briefs
  _fa_posts/             # Farsi parallel posts
  _data/                 # i18n strings, source bias records
  subscribe.md, ...      # subscribe / confirmation / blocked pages

subscribe-proxy/         # Cloudflare Worker (vendored from newsletter_base)
scripts/                 # one-off maintenance helpers
data/runs/               # per-run output and audit trails (gitignored)
```

## What each run saves

Every pipeline run writes a complete paper trail to `data/runs/{run_id}/`. The directory is gitignored, so the trail lives only on the machine that executed the run (local machine for manual runs, ephemeral GitHub Actions runner for scheduled runs).

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

- `report.md` -- first draft from summarize
- `report_edited.md` -- after editorial review
- `report_verified.md` -- after URL check
- `headlines.txt` -- short terminal summary

**LLM and HTTP audit trail** (in `audit/`):

- `llm_calls.jsonl` -- one line per LLM call: stage, tokens in/out, duration, call_id, input/output hash
- `llm_inputs/{call_id}.json` -- the exact system prompt + user message sent to the model
- `llm_outputs/{call_id}.json` -- the model's full response
- `api_calls.jsonl` -- every HTTP call to RSS sources, with status code and response size
- `dedup_similarity_matrix.json` -- pairwise TF-IDF similarity scores

Each run is roughly 1-5 MB, dominated by `llm_inputs/` (full prompts of 10-60 KB each, 15-40 calls per day). See `COSTS.md` for token and spend breakdowns.

## Documentation

- `STATUS.log` -- project status and progress tracking
- `CLAUDE.md` -- Claude Code instructions and conventions
- `config.yaml` -- all configuration (sources, buckets, budget, schedule)
- `COSTS.md` -- measured per-stage token usage and daily cost
