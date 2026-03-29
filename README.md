# Iran Conflict Daily Brief

**Status**: 🟡 MVP | **Mode**: 🤖 Claude Code | **Updated**: 2026-03-29

Automated daily news digest pipeline for the Iran conflict. Fetches from 3 sources, translates Farsi, deduplicates, filters by importance, categorizes, and publishes an expandable-format blog post to GitHub Pages.

## Pipeline

```
cron (daily 8am Pacific)
  -> fetch       (Al Jazeera RSS, Iran Intl scrape, Reuters RSS)
  -> translate   (Farsi -> English via LLM)
  -> dedup       (TF-IDF cosine similarity clustering)
  -> filter      (LLM importance classification with source bias awareness)
  -> categorize  (LLM topic bucketing)
  -> summarize   (LLM report generation with expandable format)
  -> editorial   (LLM review for contradictions and coherence)
  -> verify      (HTTP HEAD checks on all URLs)
  -> publish     (git push to gh-pages)
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
  fetch.py               # source fetching
  translate.py           # Farsi translation
  dedup.py               # TF-IDF deduplication
  filter.py              # importance filtering
  categorize.py          # topic bucketing
  summarize.py           # report generation
  editorial.py           # editorial review
  verify.py              # URL verification
  publish.py             # GitHub Pages deployment

prompts/*.yaml           # versioned prompt templates
site/                    # Jekyll blog structure
data/runs/               # per-run output and audit trails
```

## Documentation

- `STATUS.log` -- project status and progress tracking
- `CLAUDE.md` -- Claude Code instructions and conventions
- `config.yaml` -- all configuration (sources, buckets, budget, schedule)
