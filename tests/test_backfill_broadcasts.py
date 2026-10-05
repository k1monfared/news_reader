"""Tests for the Resend broadcast-ledger backfill (pure logic)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_broadcasts import (
    build_ledger,
    merge_ledgers,
    parse_broadcast,
)

AUD = {"aud-en": "en", "aud-fa": "fa"}


class TestParseBroadcast:
    def test_from_name(self):
        assert parse_broadcast("daily-brief-en-2026-10-05", "x", "", AUD) == ("2026-10-05", "en")
        assert parse_broadcast("daily-brief-fa-2026-09-01", "x", "", AUD) == ("2026-09-01", "fa")

    def test_falls_back_to_segment_and_sent_at(self):
        assert parse_broadcast("", "aud-fa", "2026-09-01 09:00:00.000+00", AUD) == ("2026-09-01", "fa")

    def test_unknown_broadcast_skipped(self):
        assert parse_broadcast("Welcome", "other", "2026-09-01 09:00:00+00", AUD) is None


class TestBuildLedger:
    def test_builds_with_delivered(self):
        broadcasts = [{
            "id": "b1", "name": "daily-brief-en-2026-10-05", "status": "sent",
            "segment_id": "aud-en", "sent_at": "2026-10-05 19:31:59+00",
        }]
        metrics = {"b1": {"sent": 50, "delivered": 48}}
        ledger = build_ledger(broadcasts, metrics, AUD)
        assert ledger["2026-10-05"]["en"]["recipients"] == 48
        assert ledger["2026-10-05"]["en"]["sent"] == 50

    def test_draft_ignored(self):
        broadcasts = [{
            "id": "b1", "name": "daily-brief-en-2026-10-05", "status": "draft",
            "segment_id": "aud-en", "sent_at": None,
        }]
        assert build_ledger(broadcasts, {}, AUD) == {}

    def test_keeps_latest_resend(self):
        broadcasts = [
            {"id": "b1", "name": "daily-brief-en-2026-10-05", "status": "sent",
             "segment_id": "aud-en", "sent_at": "2026-10-05 09:00:00+00"},
            {"id": "b2", "name": "daily-brief-en-2026-10-05", "status": "sent",
             "segment_id": "aud-en", "sent_at": "2026-10-05 20:00:00+00"},
        ]
        ledger = build_ledger(broadcasts, {}, AUD)
        assert ledger["2026-10-05"]["en"]["broadcast_id"] == "b2"


class TestMerge:
    def test_existing_only_preserved(self):
        existing = {"2026-01-01": {"en": {"broadcast_id": "old", "recipients": 5}}}
        merged = merge_ledgers(existing, {"2026-10-05": {"en": {"broadcast_id": "new"}}})
        assert merged["2026-01-01"]["en"]["recipients"] == 5
        assert merged["2026-10-05"]["en"]["broadcast_id"] == "new"

    def test_missing_recipients_kept_from_existing(self):
        existing = {"2026-10-05": {"en": {"broadcast_id": "x", "recipients": 42}}}
        merged = merge_ledgers(existing, {"2026-10-05": {"en": {"broadcast_id": "x", "recipients": None}}})
        assert merged["2026-10-05"]["en"]["recipients"] == 42
