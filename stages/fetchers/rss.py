"""RSS fetcher for Al Jazeera and Reuters."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

import feedparser

from models import RawItem, SourceConfig
from audit_logger import AuditedHTTPClient
from stages.fetchers.base import BaseFetcher

logger = logging.getLogger(__name__)


class RSSFetcher(BaseFetcher):
    """Fetches news items from RSS/Atom feeds."""

    def __init__(self, source_config: SourceConfig, http_client: AuditedHTTPClient,
                 etag_cache: dict | None = None):
        super().__init__(source_config, http_client)
        self.etag_cache = etag_cache or {}

    def fetch(self) -> list[RawItem]:
        source_name = self.config.name
        url = self.config.url
        max_items = self.config.max_items

        logger.info(f"Fetching RSS from {source_name}: {url}")

        headers = {}
        cached = self.etag_cache.get(source_name, {})
        if cached.get("etag"):
            headers["If-None-Match"] = cached["etag"]
        if cached.get("last_modified"):
            headers["If-Modified-Since"] = cached["last_modified"]

        try:
            response = self.http.request_with_retry(
                "GET", url, source=source_name, headers=headers,
            )
        except Exception as e:
            logger.error(f"Failed to fetch RSS from {source_name}: {e}")
            return []

        if response.status_code == 304:
            logger.info(f"{source_name}: 304 Not Modified, using cache")
            return []

        if not self.validate_response(response):
            logger.warning(f"{source_name}: Invalid response (status={response.status_code})")
            return []

        # Update etag cache
        self.etag_cache[source_name] = {
            "etag": response.headers.get("etag", ""),
            "last_modified": response.headers.get("last-modified", ""),
        }

        feed = feedparser.parse(response.text)
        if feed.bozo and not feed.entries:
            logger.warning(f"{source_name}: Malformed feed, no entries parsed")
            return []

        items = []
        for entry in feed.entries[:max_items]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()

            # Get the best available text content
            text = ""
            if entry.get("content"):
                text = entry.content[0].get("value", "")
            elif entry.get("summary"):
                text = entry.summary
            elif entry.get("description"):
                text = entry.description
            text = text.strip()

            if not title and not text:
                continue

            # Parse timestamp
            timestamp = ""
            if entry.get("published_parsed"):
                try:
                    dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    timestamp = dt.isoformat()
                except (ValueError, TypeError):
                    timestamp = datetime.now(timezone.utc).isoformat()
            elif entry.get("updated_parsed"):
                try:
                    dt = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
                    timestamp = dt.isoformat()
                except (ValueError, TypeError):
                    timestamp = datetime.now(timezone.utc).isoformat()
            else:
                timestamp = datetime.now(timezone.utc).isoformat()

            fetch_id = hashlib.sha256(
                f"{source_name}:{link}".encode()
            ).hexdigest()[:16]

            items.append(RawItem(
                source=source_name,
                source_url=link,
                timestamp=timestamp,
                title=title,
                text=text,
                language=self.config.language,
                fetch_id=fetch_id,
            ))

        logger.info(f"{source_name}: Fetched {len(items)} items")
        return items

    def validate_response(self, response) -> bool:
        if response.status_code != 200:
            return False
        content = response.text.strip()
        return content.startswith("<?xml") or content.startswith("<rss") or content.startswith("<feed")
