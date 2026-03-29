"""Bias tracker: logs detected bias patterns for source analysis."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def track_biases(filtered_items: list, run_dir: str) -> None:
    """Extract and log bias patterns from filter decisions.

    Looks for filter_reason text that mentions bias-related keywords
    and logs them to data/source_analysis/biases.jsonl.
    """
    bias_keywords = ["bias", "editorializ", "unverified", "unconfirmed", "propaganda", "inflat", "exaggerat"]
    output_dir = Path("data/source_analysis")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "biases.jsonl"

    count = 0
    with open(output_path, "a") as f:
        for item in filtered_items:
            reason = getattr(item, "filter_reason", "").lower()
            for keyword in bias_keywords:
                if keyword in reason:
                    entry = {
                        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                        "source": item.source,
                        "pattern": item.filter_reason,
                        "example_fetch_id": item.fetch_id,
                    }
                    f.write(json.dumps(entry) + "\n")
                    count += 1
                    break

    if count:
        logger.info(f"Tracked {count} bias pattern(s)")
