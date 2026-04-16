"""One-time bootstrap: translate the full source_biases.json into Farsi.

Run this once after installing the Farsi site. After that the daily
`translate_fa` pipeline stage handles incremental updates (only new bias
entries are translated per day).

The static UI strings (nav labels, "Previous", "Next", site title) live
in `docs/_data/i18n.yml` and are checked in by hand, not by this script.
The Farsi index and about pages (`docs/fa/index.html`,
`docs/fa/about.md`) are also checked in by hand.

Usage:
    python scripts/translate_static_fa.py
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# Allow running from either the project root or the scripts/ directory.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from models import load_config  # noqa: E402
from llm_client import AuditedLLMClient  # noqa: E402
from stages.translate_fa import _translate_bias_batch, BIAS_BATCH_SIZE, _bias_key  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("bootstrap_fa")


BIASES_PATH = ROOT / "docs" / "_data" / "source_biases.json"
BIASES_FA_PATH = ROOT / "docs" / "_data" / "source_biases_fa.json"


# Translated display names / notes that are set once. We keep them in the
# script rather than calling the LLM for single-line values.
SOURCE_DISPLAY_FA = {
    "aljazeera": {"display_name": "الجزیره", "notes": ""},
    "reuters": {"display_name": "رویترز", "notes": ""},
    "france24": {"display_name": "فرانس ۲۴", "notes": ""},
    "euronews": {"display_name": "یورونیوز", "notes": ""},
    "iranintl": {"display_name": "ایران اینترنشنال", "notes": ""},
}


def main() -> None:
    config = load_config(str(ROOT / "config.yaml"))
    if not BIASES_PATH.exists():
        logger.error(f"Source bias file not found at {BIASES_PATH}")
        return

    en_data = json.loads(BIASES_PATH.read_text(encoding="utf-8"))

    # Use a standalone run directory for audit logs so we don't pollute a real run.
    bootstrap_dir = ROOT / "data" / "bootstrap_fa"
    bootstrap_dir.mkdir(parents=True, exist_ok=True)
    (bootstrap_dir / "audit").mkdir(exist_ok=True)
    (bootstrap_dir / "audit" / "llm_inputs").mkdir(exist_ok=True)
    (bootstrap_dir / "audit" / "llm_outputs").mkdir(exist_ok=True)
    llm_client = AuditedLLMClient(str(bootstrap_dir), config.budget)

    fa_data: dict = {}
    if BIASES_FA_PATH.exists():
        try:
            fa_data = json.loads(BIASES_FA_PATH.read_text(encoding="utf-8"))
            logger.info(f"Found existing Farsi file with {len(fa_data)} sources; will skip already-translated entries.")
        except json.JSONDecodeError:
            logger.warning("Existing Farsi file was invalid JSON; starting fresh.")
            fa_data = {}

    for source_key, en_source in en_data.items():
        en_biases = en_source.get("biases", [])
        fa_source = fa_data.get(source_key, {})
        fa_biases_existing = fa_source.get("biases", [])

        existing_keys = {_bias_key(b) for b in fa_biases_existing}
        to_translate = [
            b for b in en_biases
            if _bias_key(b) not in existing_keys
        ]

        logger.info(
            f"[{source_key}] {len(en_biases)} total, {len(fa_biases_existing)} already translated, "
            f"{len(to_translate)} to translate."
        )

        translated_new: list[dict] = []
        for i in range(0, len(to_translate), BIAS_BATCH_SIZE):
            batch = to_translate[i:i + BIAS_BATCH_SIZE]
            logger.info(
                f"  batch {i // BIAS_BATCH_SIZE + 1}/"
                f"{(len(to_translate) + BIAS_BATCH_SIZE - 1) // BIAS_BATCH_SIZE} "
                f"(entries {i + 1}-{i + len(batch)})"
            )
            translated_new.extend(_translate_bias_batch(batch, llm_client, config))

        display = SOURCE_DISPLAY_FA.get(source_key, {})
        fa_data[source_key] = {
            "display_name": display.get("display_name") or fa_source.get("display_name") or en_source.get("display_name", source_key),
            "notes": display.get("notes", "") or fa_source.get("notes", en_source.get("notes", "")),
            "biases": fa_biases_existing + translated_new,
        }

        # Write incrementally so a long run survives interruptions.
        BIASES_FA_PATH.write_text(
            json.dumps(fa_data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        logger.info(f"  wrote snapshot to {BIASES_FA_PATH.name}")

    logger.info(f"Done. Total sources: {len(fa_data)}. Total cost: ${llm_client.total_cost:.4f}")


if __name__ == "__main__":
    main()
