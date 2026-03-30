"""GDELT DOC API fetcher for historical article retrieval."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from urllib.parse import urlencode

from models import RawItem, SourceConfig
from audit_logger import AuditedHTTPClient
from stages.fetchers.base import BaseFetcher

logger = logging.getLogger(__name__)

# Map source names to their primary domains in GDELT
SOURCE_DOMAINS = {
    "aljazeera": "aljazeera.com",
    "iranintl": "iranintl.com",
    "reuters": "reuters.com",
    "france24": "france24.com",
    "euronews": "euronews.com",
}

GDELT_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


class GDELTFetcher(BaseFetcher):
    """Fetches historical articles from the GDELT DOC 2.0 API for a specific source and date."""

    def __init__(
        self,
        source_config: SourceConfig,
        http_client: AuditedHTTPClient,
        target_date: str,
    ):
        super().__init__(source_config, http_client)
        self.target_date = target_date  # YYYY-MM-DD

    def fetch(self) -> list[RawItem]:
        source_name = self.config.name
        domain = SOURCE_DOMAINS.get(source_name)
        if not domain:
            logger.warning(f"No GDELT domain mapping for source: {source_name}")
            return []

        # Build date range for the target date (full day)
        date_clean = self.target_date.replace("-", "")
        start_dt = f"{date_clean}000000"
        end_dt = f"{date_clean}235959"

        # Build query: domain filter + Iran keyword
        query = f"domain:{domain} Iran"
        params = {
            "query": query,
            "mode": "ArtList",
            "format": "json",
            "startdatetime": start_dt,
            "enddatetime": end_dt,
            "maxrecords": str(self.config.max_items),
        }

        url = f"{GDELT_API_URL}?{urlencode(params)}"
        logger.info(f"GDELT query for {source_name} on {self.target_date}: {url}")

        try:
            response = self.http.request_with_retry(
                "GET", url, source=f"gdelt_{source_name}",
            )
        except Exception as e:
            logger.error(f"GDELT request failed for {source_name}: {e}")
            return []

        if response.status_code != 200:
            logger.warning(f"GDELT returned status {response.status_code} for {source_name}")
            return []

        try:
            data = response.json()
        except Exception:
            logger.warning(f"GDELT returned non-JSON response for {source_name}")
            return []

        articles = data.get("articles", [])
        if not articles:
            logger.info(f"GDELT: No articles found for {source_name} on {self.target_date}")
            return []

        items = []
        for article in articles:
            article_url = article.get("url", "").strip()
            title = article.get("title", "").strip()
            seendate = article.get("seendate", "")

            if not title or not article_url:
                continue

            timestamp = self._parse_seendate(seendate)

            fetch_id = hashlib.sha256(
                f"{source_name}:{article_url}".encode()
            ).hexdigest()[:16]

            items.append(RawItem(
                source=source_name,
                source_url=article_url,
                timestamp=timestamp,
                title=title,
                text=title,  # GDELT only provides titles
                language=self.config.language,
                fetch_id=fetch_id,
            ))

        logger.info(f"GDELT: {len(items)} articles for {source_name} on {self.target_date}")
        return items

    @staticmethod
    def _parse_seendate(seendate: str) -> str:
        """Parse GDELT seendate format to ISO string."""
        try:
            dt = datetime.strptime(seendate, "%Y%m%dT%H%M%SZ")
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except (ValueError, TypeError):
            return datetime.now(timezone.utc).isoformat()
