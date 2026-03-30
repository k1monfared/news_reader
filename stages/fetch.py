"""Fetch stage: pulls raw content from all configured sources."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from models import PipelineConfig, RawItem
from llm_client import AuditedLLMClient
from audit_logger import AuditedHTTPClient
from stages.fetchers.rss import RSSFetcher
from stages.fetchers.scrape_iranintl import IranIntlScraper
from stages.fetchers.gdelt import GDELTFetcher
from stages.fetchers.archive_france24 import France24ArchiveFetcher

logger = logging.getLogger(__name__)

FETCHER_MAP = {
    ("rss", None): RSSFetcher,
    ("scrape", "iranintl"): IranIntlScraper,
    ("gdelt", None): GDELTFetcher,
    ("archive", "france24"): France24ArchiveFetcher,
}


def _get_fetcher(source_config, http_client, etag_cache):
    """Pick the right fetcher class for a source."""
    if source_config.type == "rss":
        return RSSFetcher(source_config, http_client, etag_cache)
    if source_config.type == "scrape" and source_config.name == "iranintl":
        return IranIntlScraper(source_config, http_client)
    raise ValueError(f"No fetcher for source type={source_config.type} name={source_config.name}")


def run_fetch(
    run_dir: str,
    config: PipelineConfig,
    llm_client: AuditedLLMClient,
    http_client: AuditedHTTPClient,
) -> dict:
    """Run the fetch stage.

    Returns:
        Stage result dict with item counts per source.
    """
    run_path = Path(run_dir)
    cache_dir = Path(config.paths.get("data_dir", "data")) / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Load etag cache
    etag_path = cache_dir / "etag_cache.json"
    etag_cache = {}
    if etag_path.exists():
        try:
            etag_cache = json.loads(etag_path.read_text())
        except json.JSONDecodeError:
            etag_cache = {}

    all_items: list[RawItem] = []
    items_by_source: dict[str, int] = {}
    sources_down: list[str] = []

    for source in config.sources:
        try:
            fetcher = _get_fetcher(source, http_client, etag_cache)
            items = fetcher.fetch()
            all_items.extend(items)
            items_by_source[source.name] = len(items)
        except Exception as e:
            logger.error(f"Source {source.name} failed: {e}", exc_info=True)
            sources_down.append(source.name)
            items_by_source[source.name] = 0

    # Save etag cache
    etag_path.write_text(json.dumps(etag_cache, indent=2))

    # Write output
    output_path = run_path / "raw_items.json"
    output_path.write_text(
        json.dumps([item.model_dump() for item in all_items], indent=2)
    )

    logger.info(f"Fetch complete: {len(all_items)} items from {len(config.sources)} sources")

    return {
        "items_by_source": items_by_source,
        "total_items": len(all_items),
        "sources_down": sources_down,
    }
