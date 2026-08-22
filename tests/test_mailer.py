"""Tests for the mailer today-only broadcast rule.

Emails must never be sent for backfills or re-runs of old dates: the
mailer broadcasts only when the brief's date equals today's date.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_pipeline import get_timezone_offset


def _today_str(config) -> str:
    tz = get_timezone_offset(config.schedule.get("timezone", "America/Vancouver"))
    return datetime.now(tz).strftime("%Y-%m-%d")


def _make_posts(site_dir: Path, date_str: str) -> None:
    posts = site_dir / "_posts"
    fa_posts = site_dir / "_fa_posts"
    posts.mkdir(parents=True, exist_ok=True)
    fa_posts.mkdir(parents=True, exist_ok=True)
    (posts / f"{date_str}-daily-brief.md").write_text(
        f"---\nlayout: post\ntitle: \"Daily Brief\"\ndate: {date_str}\n---\n\n# Brief\n"
    )
    (fa_posts / f"{date_str}-daily-brief.md").write_text(
        f"---\nlayout: post\nlang: fa\ntitle: \"گزارش\"\ndate: {date_str}\n---\n\n# گزارش\n"
    )


class TestTodayOnlyRule:
    def test_past_brief_is_never_emailed(self, tmp_path, monkeypatch, sample_config):
        """A backfill run for a past date must skip broadcasting entirely."""
        import stages.mailer as mailer_module

        yesterday = (
            datetime.now(timezone(timedelta(hours=-7))) - timedelta(days=1)
        ).strftime("%Y-%m-%d")
        run_dir = tmp_path / f"{yesterday}-030000"
        run_dir.mkdir(parents=True)

        site_dir = tmp_path / "site"
        _make_posts(site_dir, yesterday)

        config = sample_config.model_copy(deep=True)
        config.mailer["enabled"] = True
        config.publish["site_dir"] = str(site_dir)

        def boom(*args, **kwargs):
            raise AssertionError("_send_for_language must not be called for backfills")

        monkeypatch.setattr(mailer_module, "_send_for_language", boom)

        result = mailer_module.run_mailer(str(run_dir), config, None, None)

        assert result == {
            "status": "skipped",
            "reason": "not_today",
            "brief_date": yesterday,
        }

    def test_future_brief_is_never_emailed(self, tmp_path, monkeypatch, sample_config):
        """Defensive: anything that is not today is skipped, even future."""
        import stages.mailer as mailer_module

        tomorrow = (
            datetime.now(timezone(timedelta(hours=-7))) + timedelta(days=1)
        ).strftime("%Y-%m-%d")
        run_dir = tmp_path / f"{tomorrow}-030000"
        run_dir.mkdir(parents=True)

        config = sample_config.model_copy(deep=True)
        config.mailer["enabled"] = True
        config.publish["site_dir"] = str(tmp_path / "site")

        def boom(*args, **kwargs):
            raise AssertionError("_send_for_language must not be called for future dates")

        monkeypatch.setattr(mailer_module, "_send_for_language", boom)

        result = mailer_module.run_mailer(str(run_dir), config, None, None)
        assert result["status"] == "skipped"
        assert result["reason"] == "not_today"

    def test_todays_brief_sends_both_languages(self, tmp_path, monkeypatch, sample_config):
        """When the brief date equals today, both broadcasts are dispatched."""
        import stages.mailer as mailer_module

        today = _today_str(sample_config)
        run_dir = tmp_path / f"{today}-080000"
        run_dir.mkdir(parents=True)

        site_dir = tmp_path / "site"
        _make_posts(site_dir, today)

        sent: list[str] = []

        def fake_send(**kwargs):
            sent.append(kwargs["lang"])
            return {"lang": kwargs["lang"], "broadcast_id": "b-123"}

        monkeypatch.setattr(mailer_module, "_send_for_language", fake_send)

        config = sample_config.model_copy(deep=True)
        config.mailer["enabled"] = True
        config.publish["site_dir"] = str(site_dir)

        result = mailer_module.run_mailer(str(run_dir), config, None, None)

        assert sorted(sent) == ["en", "fa"]
        assert result["status"] == "sent"

    def test_guard_precedes_missing_key_error(self, tmp_path, monkeypatch, sample_config):
        """The date check fires before any send attempt or key lookup."""
        import stages.mailer as mailer_module

        old_date = "2026-04-20"
        run_dir = tmp_path / f"{old_date}-080000"
        run_dir.mkdir(parents=True)

        config = sample_config.model_copy(deep=True)
        config.mailer["enabled"] = True
        config.publish["site_dir"] = str(tmp_path / "site")
        monkeypatch.delenv("RESEND_API_KEY", raising=False)

        result = mailer_module.run_mailer(str(run_dir), config, None, None)

        assert result["status"] == "skipped"

    def test_disabled_mailer_still_skips_first(self, tmp_path, sample_config):
        """Disabled mailer keeps its original short-circuit behavior."""
        import stages.mailer as mailer_module

        today = _today_str(sample_config)
        run_dir = tmp_path / f"{today}-080000"
        run_dir.mkdir(parents=True)

        config = sample_config.model_copy(deep=True)
        config.mailer["enabled"] = False

        result = mailer_module.run_mailer(str(run_dir), config, None, None)
        assert result == {"status": "skipped"}
