"""Shared pytest fixtures for the news_reader test suite.

All fixtures produce realistic data that mirrors the Iran conflict news
pipeline without requiring network access or API keys.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure the project root is importable (run_pipeline, models, etc.)
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from models import load_config, PipelineConfig, SourceConfig


# ---------------------------------------------------------------------------
# Run directory
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_run_dir(tmp_path: Path) -> str:
    """Create a temporary run directory with the expected audit sub-dirs."""
    run_dir = tmp_path / "data" / "runs" / "2026-03-29-080000"
    run_dir.mkdir(parents=True)
    (run_dir / "audit").mkdir()
    (run_dir / "audit" / "llm_inputs").mkdir()
    (run_dir / "audit" / "llm_outputs").mkdir()
    return str(run_dir)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_config() -> PipelineConfig:
    """Load the real config.yaml from the project root."""
    config_path = Path(PROJECT_ROOT) / "config.yaml"
    return load_config(str(config_path))


# ---------------------------------------------------------------------------
# Mock LLM client
# ---------------------------------------------------------------------------


class MockLLMClient:
    """Stand-in for AuditedLLMClient that never touches a real LLM API.

    Set ``mock_response`` to control what ``call()`` returns.
    """

    def __init__(self, run_dir: str, config: dict) -> None:
        self._run_dir = run_dir
        self._max_cost = float(config.get("max_cost_per_run_usd", 1.0))
        self._cumulative_cost = 0.0
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._prompt_versions_used: dict[str, int] = {}
        self.calls: list[dict] = []

        # Default response returned by call().  Tests can override this.
        self.mock_response: str = '{"result": "mock"}'

    def call(
        self,
        stage: str,
        prompt_name: str,
        prompt_version: int,
        system: str,
        user_message: str,
        model: str = "",
        max_tokens: int = 4096,
    ) -> str:
        self.calls.append({
            "stage": stage,
            "prompt_name": prompt_name,
            "prompt_version": prompt_version,
            "system": system,
            "user_message": user_message,
            "model": model,
            "max_tokens": max_tokens,
        })
        self._prompt_versions_used[prompt_name] = prompt_version
        return self.mock_response

    @property
    def total_cost(self) -> float:
        return self._cumulative_cost

    @property
    def total_tokens(self) -> dict:
        return {
            "input": self._total_input_tokens,
            "output": self._total_output_tokens,
        }

    @property
    def prompt_versions_used(self) -> dict[str, int]:
        return dict(self._prompt_versions_used)


@pytest.fixture
def mock_llm_client(tmp_run_dir: str, sample_config: PipelineConfig) -> MockLLMClient:
    """Return a MockLLMClient wired to the temporary run directory."""
    return MockLLMClient(tmp_run_dir, sample_config.budget)


# ---------------------------------------------------------------------------
# Mock HTTP client
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_http_client(tmp_run_dir: str):
    """Return an AuditedHTTPClient pointed at the temp run directory."""
    from audit_logger import AuditedHTTPClient
    return AuditedHTTPClient(tmp_run_dir)


# ---------------------------------------------------------------------------
# Sample pipeline items
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_raw_items() -> list[dict]:
    """Three realistic RawItem dicts covering English sources."""
    return [
        {
            "source": "aljazeera",
            "source_url": "https://aljazeera.com/news/2026/03/29/iran-israel-tensions-escalate",
            "timestamp": "2026-03-29T06:00:00+00:00",
            "title": "Iran-Israel tensions escalate after drone strike near Isfahan",
            "text": (
                "Tensions between Iran and Israel surged on Saturday after reports "
                "of a drone strike near the city of Isfahan. Iranian state media "
                "confirmed explosions were heard but denied significant damage. "
                "Israel has not officially commented on the incident."
            ),
            "language": "en",
            "fetch_id": "aj001",
        },
        {
            "source": "reuters",
            "source_url": "https://reuters.com/world/middle-east/oil-prices-surge-iran-fears",
            "timestamp": "2026-03-29T07:15:00+00:00",
            "title": "Oil prices surge amid Iran conflict fears",
            "text": (
                "Brent crude jumped 4% to $96 per barrel on Friday as traders "
                "reacted to heightened military activity in the Persian Gulf region. "
                "Analysts warn that any disruption to the Strait of Hormuz could "
                "push prices above $100."
            ),
            "language": "en",
            "fetch_id": "rt001",
        },
        {
            "source": "aljazeera",
            "source_url": "https://aljazeera.com/news/2026/03/29/drone-attack-isfahan-iran",
            "timestamp": "2026-03-29T06:30:00+00:00",
            "title": "Drone attack reported near Isfahan, Iran denies major damage",
            "text": (
                "A drone attack was reported near Isfahan early Saturday morning. "
                "Iranian officials said air defenses intercepted most of the drones "
                "and that no critical infrastructure was hit. Regional analysts "
                "believe the attack originated from Israeli-controlled assets."
            ),
            "language": "en",
            "fetch_id": "aj002",
        },
    ]


@pytest.fixture
def sample_translated_items(sample_raw_items: list[dict]) -> list[dict]:
    """TranslatedItem dicts built from the raw items.

    English items pass through as-is.
    """
    items = []
    for raw in sample_raw_items:
        item = dict(raw)
        if raw["language"] == "en":
            item["text_en"] = raw["text"]
            item["title_en"] = raw["title"]
        item["translation_call_id"] = None
        items.append(item)
    return items


@pytest.fixture
def sample_deduped_items(sample_translated_items: list[dict]) -> list[dict]:
    """DedupedItem dicts with two clusters.

    Cluster 1: items 0 and 2 (both about the Isfahan drone strike).
    Cluster 2: item 1 (oil prices) is its own cluster.
    """
    items = []
    for i, trans in enumerate(sample_translated_items):
        item = dict(trans)
        if i in (0, 2):
            # Items 0 and 2 are about the same event
            item["event_id"] = "aj001"
            # Item 2 has longer text, so it is the primary
            item["is_primary"] = (i == 2)
            other_idx = 2 if i == 0 else 0
            item["related_sources"] = [sample_translated_items[other_idx]["source"]]
            item["similarity_scores"] = {
                sample_translated_items[other_idx]["fetch_id"]: 0.92,
            }
        else:
            item["event_id"] = trans["fetch_id"]
            item["is_primary"] = True
            item["related_sources"] = []
            item["similarity_scores"] = {}
        item["cluster_method"] = "tfidf_cosine"
        item["cluster_threshold"] = 0.85
        items.append(item)
    return items
