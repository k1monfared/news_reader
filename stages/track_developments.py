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


def _compute_matches(
    today_items: list[CategorizedItem],
    historical_items: list[CategorizedItem],
    threshold: float,
) -> dict[str, list[tuple[CategorizedItem, float]]]:
    """Use TF-IDF cosine similarity to find historical matches for today's items.

    Returns a dict mapping today's fetch_id to a list of (historical_item, score) tuples.
    """
    if not today_items or not historical_items:
        return {}

    today_docs = [item.title_en + " " + item.text_en for item in today_items]
    hist_docs = [item.title_en + " " + item.text_en for item in historical_items]

    all_docs = today_docs + hist_docs
    vectorizer = TfidfVectorizer()
    tfidf_matrix = vectorizer.fit_transform(all_docs)

    today_matrix = tfidf_matrix[: len(today_docs)]
    hist_matrix = tfidf_matrix[len(today_docs) :]

    sim_matrix = cosine_similarity(today_matrix, hist_matrix)

    matches: dict[str, list[tuple[CategorizedItem, float]]] = {}
    for i, today_item in enumerate(today_items):
        item_matches = []
        for j, hist_item in enumerate(historical_items):
            score = float(sim_matrix[i, j])
            if score >= threshold:
                item_matches.append((hist_item, score))
        if item_matches:
            item_matches.sort(key=lambda x: x[1], reverse=True)
            matches[today_item.fetch_id] = item_matches

    return matches


def _classify_with_llm(
    today_item: CategorizedItem,
    matched_historical: list[tuple[CategorizedItem, float]],
    llm_client: AuditedLLMClient,
    config: PipelineConfig,
) -> list[dict]:
    """Ask the LLM to classify the relationship between today's item and historical matches."""
    template = load_prompt("track_developments", config.paths.get("prompts_dir", "prompts"))

    hist_text = ""
    for hist_item, score in matched_historical:
        hist_text += (
            f'<historical fetch_id="{hist_item.fetch_id}" source="{hist_item.source}" '
            f'date="{hist_item.timestamp[:10]}" similarity="{score:.2f}">\n'
            f"Title: {hist_item.title_en}\n"
            f"Text: {hist_item.text_en[:400]}\n"
            f"</historical>\n\n"
        )

    system, user_msg, version = template.render(
        today_fetch_id=today_item.fetch_id,
        today_title=today_item.title_en,
        today_text=today_item.text_en[:500],
        historical_items=hist_text,
    )

    model = config.models.get("categorize", "haiku")

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
        return result.get("classifications", [])
    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"LLM classification failed for {today_item.fetch_id}: {e}")
        return []


def _build_timeline(
    fetch_id: str,
    matched_historical: list[tuple[CategorizedItem, float]],
    classifications: list[dict],
) -> list[dict]:
    """Build a story timeline from historical matches classified as development."""
    dev_ids = set()
    for c in classifications:
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

    # Compute TF-IDF matches
    try:
        matches = _compute_matches(included, historical, similarity_threshold)
    except Exception as e:
        logger.error(f"TF-IDF matching failed: {e}. All items will be marked as new.")
        matches = {}

    # Classify each item
    status_counts = {"new": 0, "continuation": 0, "development": 0}
    tracked_map: dict[str, TrackedItem] = {}

    for item in included:
        fetch_id = item.fetch_id
        matched = matches.get(fetch_id, [])

        if not matched:
            tracked_map[fetch_id] = TrackedItem(
                **item.model_dump(), story_status="new"
            )
            status_counts["new"] += 1
            continue

        classifications = _classify_with_llm(item, matched, llm_client, config)

        if not classifications:
            tracked_map[fetch_id] = TrackedItem(
                **item.model_dump(), story_status="new"
            )
            status_counts["new"] += 1
            continue

        # Determine overall status: development > continuation > new
        statuses = [c.get("status", "new") for c in classifications]

        if "development" in statuses:
            dev_notes = [
                c.get("development_note")
                for c in classifications
                if c.get("status") == "development" and c.get("development_note")
            ]
            timeline = _build_timeline(fetch_id, matched, classifications)
            tracked_map[fetch_id] = TrackedItem(
                **item.model_dump(),
                story_status="development",
                story_timeline=timeline,
                development_note=dev_notes[0] if dev_notes else None,
            )
            status_counts["development"] += 1
        elif "continuation" in statuses and all(s in ("continuation", "new") for s in statuses):
            # If all matches are continuation (no new info), mark as continuation
            # and set included=False
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
