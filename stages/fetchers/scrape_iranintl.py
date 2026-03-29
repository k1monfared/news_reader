"""Iran International scraper."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from models import RawItem, SourceConfig
from audit_logger import AuditedHTTPClient
from stages.fetchers.base import BaseFetcher

logger = logging.getLogger(__name__)


class IranIntlScraper(BaseFetcher):
    """Scrapes news items from Iran International (Farsi site)."""

    def fetch(self) -> list[RawItem]:
        source_name = self.config.name
        url = self.config.url
        max_items = self.config.max_items

        logger.info(f"Scraping {source_name}: {url}")

        try:
            response = self.http.request_with_retry(
                "GET", url, source=source_name,
                headers={"User-Agent": "Mozilla/5.0 (compatible; NewsReader/1.0)"},
            )
        except Exception as e:
            logger.error(f"Failed to scrape {source_name}: {e}")
            return []

        if not self.validate_response(response):
            logger.warning(f"{source_name}: Invalid response (status={response.status_code})")
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        items = []

        # Try multiple selectors for article elements
        articles = (
            soup.select("article") or
            soup.select(".news-item") or
            soup.select("[class*='card']") or
            soup.select("a[href*='/news/']")
        )

        if not articles:
            logger.warning(f"{source_name}: No articles found with known selectors")
            return []

        for article in articles[:max_items]:
            title = ""
            link = ""
            text = ""

            # Extract title
            heading = article.find(["h1", "h2", "h3", "h4"])
            if heading:
                title = heading.get_text(strip=True)

            # Extract link
            a_tag = article.find("a", href=True) if article.name != "a" else article
            if a_tag and a_tag.get("href"):
                href = a_tag["href"]
                if href.startswith("/"):
                    href = url.rstrip("/") + href
                link = href

            # Extract text (summary/excerpt if available)
            text_el = article.find(["p", "div"], class_=lambda c: c and ("summary" in str(c).lower() or "excerpt" in str(c).lower() or "desc" in str(c).lower()))
            if text_el:
                text = text_el.get_text(strip=True)
            elif not title:
                # Use article text as fallback
                text = article.get_text(strip=True)[:500]

            if not title and not text:
                continue

            fetch_id = hashlib.sha256(
                f"{source_name}:{link}".encode()
            ).hexdigest()[:16]

            items.append(RawItem(
                source=source_name,
                source_url=link,
                timestamp=datetime.now(timezone.utc).isoformat(),
                title=title or text[:80],
                text=text or title,
                language="fa",
                fetch_id=fetch_id,
            ))

        logger.info(f"{source_name}: Scraped {len(items)} items")
        return items
