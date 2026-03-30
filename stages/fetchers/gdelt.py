"""GDELT DOC API fetcher for historical article retrieval."""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse

from models import PipelineConfig, RawItem, SourceConfig
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

# Reverse: domain -> source name
DOMAIN_TO_SOURCE: dict[str, str] = {v: k for k, v in SOURCE_DOMAINS.items()}

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
            response = self.http.get(url, source=f"gdelt_{source_name}")
        except Exception as e:
            logger.error(f"GDELT request failed for {source_name}: {e}")
            return []

        if response.status_code == 429:
            logger.warning(f"GDELT rate limited for {source_name}, waiting 5s and retrying")
            time.sleep(5)
            try:
                response = self.http.get(url, source=f"gdelt_{source_name}")
            except Exception as e:
                logger.error(f"GDELT retry failed for {source_name}: {e}")
                return []

        if response.status_code != 200:
            logger.warning(f"GDELT returned status {response.status_code} for {source_name}")
            return []

        return self._parse_response(response, source_name)

    def _parse_response(self, response, source_name: str) -> list[RawItem]:
        """Parse a GDELT API response into RawItems."""
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


def gdelt_fetch_all_sources(
    config: PipelineConfig,
    http_client: AuditedHTTPClient,
    target_date: str,
) -> list[RawItem]:
    """Fetch articles for ALL sources in a single GDELT query, avoiding rate limits.

    Combines all source domains into one OR query, then splits results by domain.
    """
    domains = []
    source_map: dict[str, SourceConfig] = {}
    for source in config.sources:
        domain = SOURCE_DOMAINS.get(source.name)
        if domain:
            domains.append(domain)
            source_map[domain] = source

    if not domains:
        return []

    date_clean = target_date.replace("-", "")
    start_dt = f"{date_clean}000000"
    end_dt = f"{date_clean}235959"

    # Single query: (domain:a.com OR domain:b.com OR ...) Iran
    domain_clauses = " OR ".join(f"domain:{d}" for d in domains)
    query = f"({domain_clauses}) Iran"
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "startdatetime": start_dt,
        "enddatetime": end_dt,
        "maxrecords": "250",
    }

    url = f"{GDELT_API_URL}?{urlencode(params)}"
    logger.info(f"GDELT batch query for {target_date}: {url}")

    for attempt in range(3):
        try:
            response = http_client.get(url, source="gdelt_batch")
        except Exception as e:
            logger.error(f"GDELT batch request failed: {e}")
            return []

        if response.status_code == 429:
            wait = 5 * (attempt + 1)
            logger.warning(f"GDELT rate limited, waiting {wait}s (attempt {attempt + 1}/3)")
            time.sleep(wait)
            continue

        if response.status_code != 200:
            logger.warning(f"GDELT batch returned status {response.status_code}")
            return []

        break
    else:
        logger.error("GDELT batch: exhausted retries due to rate limiting")
        return []

    try:
        data = response.json()
    except Exception:
        logger.warning("GDELT batch returned non-JSON response")
        return []

    articles = data.get("articles", [])
    if not articles:
        logger.info(f"GDELT batch: No articles found for {target_date}")
        return []

    # Split articles by domain, map to correct source
    items: list[RawItem] = []
    for article in articles:
        article_url = article.get("url", "").strip()
        title = article.get("title", "").strip()
        seendate = article.get("seendate", "")
        article_domain = article.get("domain", "").strip()

        if not title or not article_url:
            continue

        # Match domain to source config
        source_cfg = source_map.get(article_domain)
        if not source_cfg:
            # Try extracting domain from URL
            parsed = urlparse(article_url)
            host = parsed.hostname or ""
            for known_domain, cfg in source_map.items():
                if known_domain in host:
                    source_cfg = cfg
                    break
        if not source_cfg:
            continue

        try:
            dt = datetime.strptime(seendate, "%Y%m%dT%H%M%SZ")
            timestamp = dt.replace(tzinfo=timezone.utc).isoformat()
        except (ValueError, TypeError):
            timestamp = datetime.now(timezone.utc).isoformat()

        fetch_id = hashlib.sha256(
            f"{source_cfg.name}:{article_url}".encode()
        ).hexdigest()[:16]

        items.append(RawItem(
            source=source_cfg.name,
            source_url=article_url,
            timestamp=timestamp,
            title=title,
            text=title,
            language=source_cfg.language,
            fetch_id=fetch_id,
        ))

    by_source: dict[str, int] = {}
    for item in items:
        by_source[item.source] = by_source.get(item.source, 0) + 1
    logger.info(f"GDELT batch: {len(items)} articles for {target_date}: {by_source}")

    return items
