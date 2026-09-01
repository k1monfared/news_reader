from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


# --- Pipeline item models (progressive enrichment chain) ---


class RawItem(BaseModel):
    source: str
    source_url: str
    timestamp: str
    title: str
    text: str
    language: str
    fetch_id: str


class TranslatedItem(RawItem):
    text_en: str
    title_en: str
    translation_call_id: str | None = None


class DedupedItem(TranslatedItem):
    event_id: str
    is_primary: bool
    related_sources: list[str] = []
    similarity_scores: dict[str, float] = {}
    cluster_method: str = "tfidf_cosine"
    cluster_threshold: float = 0.85


class FilteredItem(DedupedItem):
    included: bool
    confidence: float
    filter_reason: str
    sole_source_flag: bool = False


class CategorizedItem(FilteredItem):
    primary_category: str
    secondary_category: str | None = None


class TrackedItem(CategorizedItem):
    story_status: str = "new"  # "new" | "continuation" | "development"
    story_timeline: list[dict] = []  # [{date, summary, fetch_id}]
    development_note: str | None = None


# --- Audit models ---


class AuditLLMCall(BaseModel):
    stage: str
    prompt_name: str
    prompt_version: int
    call_id: str
    input_hash: str
    output_hash: str
    tokens_in: int
    tokens_out: int
    duration_s: float
    timestamp: str
    model: str


class AuditAPICall(BaseModel):
    source: str
    url: str
    method: str
    status_code: int
    response_size_bytes: int
    duration_s: float
    timestamp: str


# --- Run metadata ---


class RunMeta(BaseModel):
    run_id: str
    started_at: str
    finished_at: str | None = None
    stages: dict = {}
    total_cost_usd: float = 0.0
    total_duration_s: float = 0.0
    prompt_versions: dict[str, int] = {}
    errors: list[str] = []
    items_fetched: int = 0
    items_filtered: int = 0
    items_included: int = 0
    sources_down: list[str] = []


# --- Configuration models ---


class SourceConfig(BaseModel):
    name: str
    type: str
    url: str
    language: str
    max_items: int = 200
    known_biases: str = ""
    reliability_notes: str = ""
    filter_instructions: str = ""
    debias_instructions: str = ""


class BucketConfig(BaseModel):
    name: str
    description: str
    keywords: list[str] = []


class PipelineConfig(BaseModel):
    sources: list[SourceConfig]
    models: dict
    buckets: list[BucketConfig]
    schedule: dict
    budget: dict
    pipeline: dict
    publish: dict
    paths: dict
    audit: dict = {}
    development_tracking: dict = {}
    translate_fa: dict = {}
    mailer: dict = {}
    empty_brief: dict = {}


# --- Helper ---


def load_config(path: str = "config.yaml") -> PipelineConfig:
    raw = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    return PipelineConfig(**data)
