# Claude Instructions

## Project Overview
Daily automated news digest pipeline covering the US-Israel war on Iran. Fetches from 4 sources (Al Jazeera, Reuters, France 24, Euronews), deduplicates, filters by importance, categorizes into topic buckets, produces an expandable-format blog post, reviews editorially, detects source biases, verifies links, and publishes to GitHub Pages.

## Project Stage Definitions

### 1. Initial Setup
- Repository created, basic structure in place
- Template files copied and configured
- Ready to start development

### 2. Proof of Concept (POC)
- Validating core concept/technology
- Experimenting with approaches
- Not production-ready
- Goal: Answer "Can this work?"

### 3. Minimum Viable Product (MVP)
- First usable version with core features
- May have bugs blocking demo/production
- Focus on core functionality only
- Goal: Get something working end-to-end

### 4. Beta Release
- Feature-complete for v1.0 scope
- Functional but missing polish features
- Known list of features X, Y, Z to add
- May have active testers/users
- Goal: Refinement and feature completion

### 5. Production Ready
- Stable, tested, documented
- All planned features implemented
- Ready for real-world use
- Active maintenance

### 6. Maintenance Mode
- Core features complete and stable
- Nice-to-have features queued (A, B, C)
- Only accepting specific contributions
- Minimal active development

## Development Commands
All project commands are defined in `.claude/commands.env`

## Git Workflow
- Run tests before every commit/push
- Only push if all tests pass
- Use descriptive commit messages

## Implementation Rules
**No creative liberties** - Only implement what is explicitly discussed and decided
- Never invent UI elements, charts, or features without user approval
- Never create placeholder/demo content or hardcoded values
- Always ask "what should this show?" before creating any display elements

**Data-driven approach** - All content must come from files
- Create data files first, then build components that read from them
- For mockups: create realistic data files, never hardcode values
- Example: inventory dashboard needs `data/inventory.json`, not `const items = [...]`

**Decision-first workflow**
1. Discuss what to build (purpose, data, layout)
2. Create data structure/files
3. Build components that consume the data
4. Document everything as you go at each step

## Documentation Format
**Use loglog format** for all documentation and notes
- Create documentation as `*.log` files using loglog syntax
- Convert to markdown when needed: `loglog file.log > file.md`
- Follow loglog syntax from https://github.com/k1monfared/loglog
- Installation: `pip install loglog` or `cargo install loglog`

## Code Conventions
- Python 3.11+, Pydantic v2 for data models
- `from __future__ import annotations` in all files
- Each pipeline stage: `run_{stage}(run_dir, config, llm_client, http_client) -> dict`
- Config in config.yaml, prompts in prompts/*.yaml
- All LLM calls go through `AuditedLLMClient`, all HTTP through `AuditedHTTPClient`

## Testing Instructions
- Run tests: `pytest tests/`
- Tests use mocked LLM and HTTP clients (no network needed)
- Golden dataset in `tests/golden/items.json`

## Architecture Notes
- Pipeline stages in `stages/*.py`, each reads previous stage's JSON output
- Run isolation: `data/runs/{YYYY-MM-DD-HHMMSS}/` per run
- Audit trail: `audit/llm_calls.jsonl`, `audit/llm_inputs/`, `audit/llm_outputs/`, `audit/api_calls.jsonl`
- Prompt templates in `prompts/*.yaml` with version tracking
- Source fetchers in `stages/fetchers/` behind `BaseFetcher` interface
- Bias tracking: `docs/_data/source_biases.json` (detected in editorial stage, displayed on About page)

## Naming Convention
- The conflict is called the "USrael war on Iran", not "Iran conflict"
- Site title: "USrael War on Iran: Daily Brief"
- Post titles: "Daily Brief: {Month Day, Year}" (no redundant conflict name)

## Deploying to GitHub Pages
GitHub Pages is configured to build from the `docs/` directory on `master`. Everything is one branch, one push. The pipeline's publish stage commits new posts and pushes master automatically. Site changes (layouts, CSS, pages) deploy with any normal `git push origin master`.

## Environment Setup
- Install deps: `pip install -r requirements.txt`
- Env vars: `ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_BASE_URL` (Poe API)
- See `.env.example` for template
- Schedule: `bash schedule/setup.sh` to install cron job

## Project Status Tracking

### STATUS.log File
- Maintain project status in `STATUS.log` file (loglog format)
- Update "Last Updated" date when making changes
- Track current stage, development mode (Claude/Manual/Hybrid), and next milestone
- Run `/home/k1/public/update_project_status.sh` after updates to sync master status file

### Status Badges
Add to top of README files for quick visual reference:

**Status**: [🔴 POC | 🟡 MVP | 🔵 Beta | 🟢 Production | ⚫ Maintenance] | **Mode**: [🤖 Claude Code | 👤 Manual | 🔀 Hybrid] | **Updated**: YYYY-MM-DD

### Converting to Markdown
Convert loglog files to markdown when needed:
```bash
loglog STATUS.log > STATUS.md
loglog readme.log > README.md
```