"""France24 archive scraper for historical article retrieval."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from models import RawItem, SourceConfig
from audit_logger import AuditedHTTPClient
from stages.fetchers.base import BaseFetcher

logger = logging.getLogger(__name__)

MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}


class France24ArchiveFetcher(BaseFetcher):
    """Scrapes articles from France24 archive pages for a specific date."""

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
        dt = datetime.strptime(self.target_date, "%Y-%m-%d")
        month_name = MONTH_NAMES[dt.month]

        archive_url = (
            f"https://www.france24.com/en/archives/middle-east/"
            f"{dt.year}/{dt.month:02d}/{dt.day:02d}-{month_name}-{dt.year}"
        )
        logger.info(f"France24 archive for {self.target_date}: {archive_url}")

        try:
            response = self.http.request_with_retry(
                "GET", archive_url, source="france24_archive",
                headers={"User-Agent": "Mozilla/5.0 (compatible; NewsReader/1.0)"},
            )
        except Exception as e:
            logger.error(f"France24 archive request failed: {e}")
            return []

        if response.status_code != 200:
            logger.info(f"France24 archive returned {response.status_code} for {self.target_date}")
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        items = []

        articles = (
            soup.select("article") or
            soup.select(".o-layout-list__item") or
            soup.select(".t-content__title") or
            soup.select("a[href*='/middle-east/']")
        )

        if not articles:
            logger.info(f"France24 archive: No articles found for {self.target_date}")
            return []

        for article in articles[:self.config.max_items]:
            title = ""
            link = ""
            text = ""

            heading = article.find(["h1", "h2", "h3", "h4"])
            if heading:
                title = heading.get_text(strip=True)

            a_tag = article.find("a", href=True) if article.name != "a" else article
            if a_tag and a_tag.get("href"):
                href = a_tag["href"]
                if href.startswith("/"):
                    href = f"https://www.france24.com{href}"
                link = href

            summary_el = article.find(
                ["p", "div"],
                class_=lambda c: c and any(
                    kw in str(c).lower()
                    for kw in ("summary", "excerpt", "desc", "intro", "chapo")
                ),
            )
            if summary_el:
                text = summary_el.get_text(strip=True)
            else:
                p_tag = article.find("p")
                if p_tag:
                    text = p_tag.get_text(strip=True)

            if not title and not text:
                continue

            fetch_id = hashlib.sha256(
                f"{source_name}:{link}".encode()
            ).hexdigest()[:16]

            items.append(RawItem(
                source=source_name,
                source_url=link,
                timestamp=datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc).isoformat(),
                title=title or text[:80],
                text=text or title,
                language="en",
                fetch_id=fetch_id,
            ))

        logger.info(f"France24 archive: {len(items)} articles for {self.target_date}")
        return items
