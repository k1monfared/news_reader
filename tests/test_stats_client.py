"""Tests for the best-effort dashboard stats providers."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import stats_client
from stats_client import (
    audience_count,
    configured_subscribe_stats_url,
    subscribe_joins,
)


class TestAudienceCount:
    def test_missing_inputs_return_none(self):
        assert audience_count("", "aud") is None
        assert audience_count("key", "") is None


class TestSubscribeJoins:
    def test_no_url_returns_none(self):
        assert subscribe_joins("") is None


class TestConfig:
    def test_reads_url_and_env_token(self, monkeypatch):
        monkeypatch.setenv("SUBSCRIBE_STATS_TOKEN", "tok")

        class Cfg:
            metrics = {"subscribe_stats_url": "https://x/stats"}

        url, token = configured_subscribe_stats_url(Cfg())
        assert url == "https://x/stats"
        assert token == "tok"

    def test_missing_config(self, monkeypatch):
        monkeypatch.delenv("SUBSCRIBE_STATS_TOKEN", raising=False)

        class Cfg:
            metrics = {}

        assert configured_subscribe_stats_url(Cfg()) == ("", "")

    def test_audience_count_handles_http_error(self, monkeypatch):
        import httpx

        class BoomClient:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get(self, *a, **k):
                raise httpx.ConnectError("down")

        monkeypatch.setattr(stats_client.httpx, "Client", BoomClient)
        assert audience_count("key", "aud") is None
