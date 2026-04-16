"""Development tracking stage: compares today's items against the past N days
to classify each as new, continuation, or development."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from models import PipelineConfig, CategorizedItem, TrackedItem
from llm_client import AuditedLLMClient
from audit_logger import AuditedHTTPClient
from prompt_loader import load_prompt

logger = logging.getLogger(__name__)


_SUMMARY_LINE_RE = re.compile(
    r'<summary[^>]*>\s*\*\*(.*?)\*\*',
    re.DOTALL,
)


def _load_recent_brief_headlines(
    posts_dir: str, run_date: str, lookback_days: int
) -> list[tuple[str, str]]:
    """Extract headline lines from recent published daily brief posts.

    These capture what the reader actually saw on prior days, regardless of
    whether the run data is still on disk. Returns a list of
    (date, headline) pairs, most recent last.
    """
    posts_path = Path(posts_dir)
    if not posts_path.exists():
        return []

    end_date = datetime.strptime(run_date, "%Y-%m-%d")
    start_date = end_date - timedelta(days=lookback_days)

    results: list[tuple[str, str]] = []
    for post in sorted(posts_path.glob("*-daily-brief.md")):
        try:
            date_str = post.name[:10]
            post_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if post_date < start_date or post_date >= end_date:
            continue

        try:
            content = post.read_text(encoding="utf-8")
        except OSError:
            continue

        for match in _SUMMARY_LINE_RE.finditer(content):
            headline = match.group(1).strip()
            headline = re.sub(r'^\(Update\)\s*', '', headline)
            if headline:
                results.append((date_str, headline))

    return results


def _load_historical_items(
    data_dir: str, run_date: str, lookback_days: int
) -> list[CategorizedItem]:
    """Load included CategorizedItems from the past N days of runs."""
    runs_dir = Path(data_dir) / "runs"
    if not runs_dir.exists():
        return []

    end_date = datetime.strptime(run_date, "%Y-%m-%d")
    start_date = end_date - timedelta(days=lookback_days)
    historical: list[CategorizedItem] = []

    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir() or run_dir.is_symlink():
            continue

        dir_date_str = run_dir.name[:10]
        try:
            dir_date = datetime.strptime(dir_date_str, "%Y-%m-%d")
        except ValueError:
            continue

        if dir_date < start_date or dir_date >= end_date:
            continue

        cat_path = run_dir / "categorized_items.json"
        if not cat_path.exists():
            continue

        try:
            raw = json.loads(cat_path.read_text(encoding="utf-8"))
            for item_data in raw:
                item = CategorizedItem(**item_data)
                if item.included:
                    historical.append(item)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Failed to load historical items from {run_dir.name}: {e}")
            continue

    logger.info(f"Loaded {len(historical)} historical items from past {lookback_days} days")
    return historical


def _make_doc(item: CategorizedItem) -> str:
    """Build a weighted document for TF-IDF: title repeated for emphasis, plus text."""
    title = item.title_en or ""
    text = item.text_en or ""
    # Title is repeated to boost its weight in TF-IDF, since RSS text is often
    # just the title again or very short.
    return f"{title} {title} {title} {text}"


def _is_live_blog(title: str) -> bool:
    """Check if a title looks like a live blog/rolling coverage post."""
    lower = title.lower()
    return "live :" in lower or "live:" in lower or "live |" in lower


def _compute_matches(
    today_items: list[CategorizedItem],
    historical_items: list[CategorizedItem],
    threshold: float,
) -> dict[str, list[tuple[CategorizedItem, float]]]:
    """Use TF-IDF cosine similarity to find historical matches for today's items.

    Uses two matching strategies:
    1. Full-text TF-IDF at the configured threshold
    2. Title-only TF-IDF at a lower threshold (titles are more distinctive for
       short RSS items)
    3. Live blog detection: items sharing a "live:" pattern are always matched

    Returns a dict mapping today's fetch_id to a list of (historical_item, score) tuples.
    """
    if not today_items or not historical_items:
        return {}

    # Strategy 1: full-text TF-IDF (with title boost)
    today_docs = [_make_doc(item) for item in today_items]
    hist_docs = [_make_doc(item) for item in historical_items]

    all_docs = today_docs + hist_docs
    vectorizer = TfidfVectorizer(max_features=10000)
    tfidf_matrix = vectorizer.fit_transform(all_docs)

    today_matrix = tfidf_matrix[: len(today_docs)]
    hist_matrix = tfidf_matrix[len(today_docs) :]

    sim_matrix = cosine_similarity(today_matrix, hist_matrix)

    # Strategy 2: title-only TF-IDF
    today_titles = [item.title_en or "" for item in today_items]
    hist_titles = [item.title_en or "" for item in historical_items]
    title_vectorizer = TfidfVectorizer(max_features=5000)
    try:
        title_matrix = title_vectorizer.fit_transform(today_titles + hist_titles)
        title_sim = cosine_similarity(
            title_matrix[:len(today_titles)],
            title_matrix[len(today_titles):]
        )
    except ValueError:
        title_sim = np.zeros((len(today_items), len(historical_items)))

    # Lower thresholds to catch more potential matches for LLM classification
    text_threshold = threshold
    title_threshold = max(threshold - 0.15, 0.2)

    matches: dict[str, list[tuple[CategorizedItem, float]]] = {}
    for i, today_item in enumerate(today_items):
        item_matches = []
        seen_ids = set()
        for j, hist_item in enumerate(historical_items):
            text_score = float(sim_matrix[i, j])
            title_score = float(title_sim[i, j])
            # Use the higher of text or title similarity
            best_score = max(text_score, title_score)

            matched = False
            if text_score >= text_threshold:
                matched = True
            elif title_score >= title_threshold:
                matched = True
            # Live blog heuristic: if both are live blogs from the same source,
            # they're almost certainly about the same running story
            elif (_is_live_blog(today_item.title_en or "") and
                  _is_live_blog(hist_item.title_en or "") and
                  today_item.source == hist_item.source):
                matched = True
                best_score = max(best_score, 0.5)  # Ensure they get classified

            if matched and hist_item.fetch_id not in seen_ids:
                item_matches.append((hist_item, best_score))
                seen_ids.add(hist_item.fetch_id)

        if item_matches:
            item_matches.sort(key=lambda x: x[1], reverse=True)
            # Limit to top 5 matches to keep LLM calls manageable
            matches[today_item.fetch_id] = item_matches[:5]

    return matches


def _classify_with_llm(
    today_item: CategorizedItem,
    matched_historical: list[tuple[CategorizedItem, float]],
    recent_headlines: list[tuple[str, str]],
    llm_client: AuditedLLMClient,
    config: PipelineConfig,
) -> dict | None:
    """Ask the LLM to classify today's item against historical matches and recent headlines.

    Returns a dict with keys: overall_status, matched_reference,
    development_note, per_historical. Returns None on failure.
    """
    template = load_prompt("track_developments", config.paths.get("prompts_dir", "prompts"))

    if matched_historical:
        hist_text = ""
        for hist_item, score in matched_historical:
            hist_text += (
                f'<historical fetch_id="{hist_item.fetch_id}" source="{hist_item.source}" '
                f'date="{hist_item.timestamp[:10]}" similarity="{score:.2f}">\n'
                f"Title: {hist_item.title_en}\n"
                f"Text: {hist_item.text_en[:400]}\n"
                f"</historical>\n\n"
            )
    else:
        hist_text = "(no historical matches)"

    if recent_headlines:
        headlines_text = "\n".join(
            f"- [{date}] {headline}" for date, headline in recent_headlines
        )
    else:
        headlines_text = "(no recent briefs available)"

    system, user_msg, version = template.render(
        today_fetch_id=today_item.fetch_id,
        today_title=today_item.title_en,
        today_text=today_item.text_en[:500],
        historical_items=hist_text,
        recent_brief_headlines=headlines_text,
    )

    model = config.models.get("default", "claude-sonnet-4-5")

    try:
        response = llm_client.call(
            stage="track_developments",
            prompt_name="track_developments",
            prompt_version=version,
            system=system,
            user_message=user_msg,
            model=model,
            max_tokens=2048,
        )
        # Strip markdown fencing if present
        cleaned = response.strip()
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
        cleaned = re.sub(r'\s*```$', '', cleaned)

        result = json.loads(cleaned)
        return result
    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"LLM classification failed for {today_item.fetch_id}: {e}")
        return None


def _build_timeline(
    fetch_id: str,
    matched_historical: list[tuple[CategorizedItem, float]],
    per_historical: list[dict],
) -> list[dict]:
    """Build a story timeline from historical matches classified as development."""
    dev_ids = set()
    for c in per_historical:
        if c.get("status") == "development":
            dev_ids.add(c.get("historical_fetch_id"))

    timeline = []
    for hist_item, _score in matched_historical:
        if hist_item.fetch_id in dev_ids:
            timeline.append({
                "date": hist_item.timestamp[:10],
                "summary": hist_item.title_en,
                "fetch_id": hist_item.fetch_id,
            })

    timeline.sort(key=lambda x: x["date"])
    return timeline


def run_track_developments(
    run_dir: str,
    config: PipelineConfig,
    llm_client: AuditedLLMClient,
    http_client: AuditedHTTPClient,
) -> dict:
    """Run the development tracking stage.

    Compares today's included items against the past N days to classify
    each as new, continuation, or development.
    """
    run_path = Path(run_dir)
    data_dir = config.paths.get("data_dir", "data")
    tracking_config = config.development_tracking
    lookback_days = tracking_config.get("lookback_days", 14)
    similarity_threshold = tracking_config.get("similarity_threshold", 0.5)

    # Load today's categorized items
    cat_path = run_path / "categorized_items.json"
    raw = json.loads(cat_path.read_text(encoding="utf-8"))
    all_items = [CategorizedItem(**item) for item in raw]
    included = [item for item in all_items if item.included]

    if not included:
        tracked = [
            TrackedItem(**item.model_dump())
            for item in all_items
        ]
        output_path = run_path / "tracked_items.json"
        output_path.write_text(json.dumps([t.model_dump() for t in tracked], indent=2))
        return {"items_tracked": 0, "new": 0, "continuation": 0, "development": 0}

    # Extract run date from the run directory name
    run_date = run_path.name[:10]

    # Load historical items
    try:
        historical = _load_historical_items(data_dir, run_date, lookback_days)
    except Exception as e:
        logger.error(f"Failed to load historical items: {e}. All items will be marked as new.")
        historical = []

    # Load recent brief headlines (survives machine changes since posts are in git)
    posts_dir = Path(config.publish.get("site_dir", "docs")) / "_posts"
    try:
        recent_headlines = _load_recent_brief_headlines(
            str(posts_dir), run_date, lookback_days
        )
        logger.info(
            f"Loaded {len(recent_headlines)} recent brief headlines from past "
            f"{lookback_days} days"
        )
    except Exception as e:
        logger.warning(f"Failed to load recent brief headlines: {e}")
        recent_headlines = []

    # Compute TF-IDF matches against historical run items
    try:
        matches = _compute_matches(included, historical, similarity_threshold)
    except Exception as e:
        logger.error(f"TF-IDF matching failed: {e}. All items will be marked as new.")
        matches = {}

    # Classify each item
    status_counts = {"new": 0, "continuation": 0, "development": 0}
    tracked_map: dict[str, TrackedItem] = {}

    # Cap how many recent headlines we show the LLM per item so the prompt
    # stays small. Keep the most recent ones, which are most likely to clash
    # with today's items.
    max_headlines = 25
    trimmed_headlines = (
        recent_headlines[-max_headlines:]
        if len(recent_headlines) > max_headlines
        else recent_headlines
    )

    for item in included:
        fetch_id = item.fetch_id
        matched = matches.get(fetch_id, [])
        item_recent = trimmed_headlines

        if not matched and not item_recent:
            tracked_map[fetch_id] = TrackedItem(
                **item.model_dump(), story_status="new"
            )
            status_counts["new"] += 1
            continue

        result = _classify_with_llm(
            item, matched, item_recent, llm_client, config
        )

        if not result:
            tracked_map[fetch_id] = TrackedItem(
                **item.model_dump(), story_status="new"
            )
            status_counts["new"] += 1
            continue

        overall = result.get("overall_status", "new")
        per_historical = result.get("per_historical", []) or []

        if overall == "development":
            timeline = _build_timeline(fetch_id, matched, per_historical)
            tracked_map[fetch_id] = TrackedItem(
                **item.model_dump(),
                story_status="development",
                story_timeline=timeline,
                development_note=result.get("development_note"),
            )
            status_counts["development"] += 1
        elif overall == "continuation":
            tracked_item = TrackedItem(
                **item.model_dump(),
                story_status="continuation",
            )
            tracked_item.included = False
            tracked_map[fetch_id] = tracked_item
            status_counts["continuation"] += 1
        else:
            tracked_map[fetch_id] = TrackedItem(
                **item.model_dump(), story_status="new"
            )
            status_counts["new"] += 1

    # Build full output: tracked versions of included items + non-included items as-is
    tracked_items: list[TrackedItem] = []
    for item in all_items:
        if item.fetch_id in tracked_map:
            tracked_items.append(tracked_map[item.fetch_id])
        else:
            tracked_items.append(TrackedItem(**item.model_dump()))

    output_path = run_path / "tracked_items.json"
    output_path.write_text(
        json.dumps([t.model_dump() for t in tracked_items], indent=2)
    )

    logger.info(
        f"Development tracking complete: {status_counts['new']} new, "
        f"{status_counts['development']} developments, "
        f"{status_counts['continuation']} continuations"
    )

    return {
        "items_tracked": len(included),
        **status_counts,
    }
