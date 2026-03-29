"""Tests for the RSS fetcher."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import SourceConfig, RawItem
from stages.fetchers.rss import RSSFetcher


# ---------------------------------------------------------------------------
# Sample RSS XML
# ---------------------------------------------------------------------------

SAMPLE_RSS_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <link>https://example.com</link>
    <description>Test RSS feed</description>
    <item>
      <title>Iran-Israel tensions escalate after drone strike</title>
      <link>https://example.com/article/1</link>
      <description>Tensions surged after reports of a drone strike near Isfahan.</description>
      <pubDate>Sat, 29 Mar 2026 06:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Oil prices jump on Gulf conflict fears</title>
      <link>https://example.com/article/2</link>
      <description>Brent crude jumped 4 percent as traders reacted to military activity.</description>
      <pubDate>Sat, 29 Mar 2026 07:15:00 GMT</pubDate>
    </item>
    <item>
      <title>UN calls for de-escalation in Middle East</title>
      <link>https://example.com/article/3</link>
      <description>The UN Secretary-General urged all parties to exercise restraint.</description>
      <pubDate>Sat, 29 Mar 2026 08:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source_config(name: str = "testrss", language: str = "en") -> SourceConfig:
    return SourceConfig(
        name=name,
        type="rss",
        url="https://example.com/rss/feed.xml",
        language=language,
        max_items=200,
        known_biases="None",
        reliability_notes="Test",
        filter_instructions="",
        debias_instructions="",
    )


def _make_mock_response(status_code: int, text: str, content: bytes | None = None) -> MagicMock:
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.content = content if content is not None else text.encode("utf-8")
    resp.headers = {}
    return resp


def _make_mock_http_client(response: MagicMock) -> MagicMock:
    """Create a mock AuditedHTTPClient that returns the given response."""
    client = MagicMock()
    client.request_with_retry.return_value = response
    client.get.return_value = response
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRSSFetcher:
    def test_rss_fetcher_parses_items(self):
        """A valid RSS response should be parsed into a list of RawItems."""
        source_config = _make_source_config()
        response = _make_mock_response(200, SAMPLE_RSS_XML)
        http_client = _make_mock_http_client(response)

        fetcher = RSSFetcher(source_config, http_client)
        items = fetcher.fetch()

        assert len(items) == 3
        for item in items:
            assert isinstance(item, RawItem)
            assert item.source == "testrss"
            assert item.language == "en"
            assert item.source_url.startswith("https://example.com/article/")
            assert item.title
            assert item.text
            assert item.timestamp
            assert item.fetch_id

        # Check specific content
        assert "drone strike" in items[0].title.lower()
        assert "oil" in items[1].title.lower()
        assert "un" in items[2].title.lower()

    def test_rss_fetcher_handles_error(self):
        """A 500 response should produce an empty list, not an exception."""
        source_config = _make_source_config()
        response = _make_mock_response(500, "Internal Server Error")
        http_client = _make_mock_http_client(response)

        fetcher = RSSFetcher(source_config, http_client)
        items = fetcher.fetch()

        assert items == []

    def test_rss_fetcher_handles_304_not_modified(self):
        """A 304 Not Modified should return an empty list (cache hit)."""
        source_config = _make_source_config()
        response = _make_mock_response(304, "")
        http_client = _make_mock_http_client(response)

        fetcher = RSSFetcher(source_config, http_client, etag_cache={
            "testrss": {"etag": '"abc123"', "last_modified": "Sat, 28 Mar 2026 00:00:00 GMT"},
        })
        items = fetcher.fetch()

        assert items == []

    def test_rss_fetcher_respects_max_items(self):
        """Only max_items entries should be returned even if the feed has more."""
        source_config = _make_source_config()
        # Override max_items to 2
        source_config.max_items = 2
        response = _make_mock_response(200, SAMPLE_RSS_XML)
        http_client = _make_mock_http_client(response)

        fetcher = RSSFetcher(source_config, http_client)
        items = fetcher.fetch()

        assert len(items) == 2

    def test_rss_fetcher_handles_network_exception(self):
        """A network error should produce an empty list, not crash."""
        import httpx

        source_config = _make_source_config()
        http_client = MagicMock()
        http_client.request_with_retry.side_effect = httpx.ConnectError("Connection refused")

        fetcher = RSSFetcher(source_config, http_client)
        items = fetcher.fetch()

        assert items == []

    def test_rss_fetcher_skips_empty_entries(self):
        """Entries with no title and no text should be skipped."""
        rss_with_empty = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test</title>
    <item>
      <title></title>
      <link>https://example.com/empty</link>
      <description></description>
    </item>
    <item>
      <title>Valid headline about Iran conflict</title>
      <link>https://example.com/valid</link>
      <description>This is a real article with content.</description>
    </item>
  </channel>
</rss>
"""
        source_config = _make_source_config()
        response = _make_mock_response(200, rss_with_empty)
        http_client = _make_mock_http_client(response)

        fetcher = RSSFetcher(source_config, http_client)
        items = fetcher.fetch()

        assert len(items) == 1
        assert "Valid headline" in items[0].title
