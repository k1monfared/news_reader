"""Tests for the render-check stage and backfill title marking.

Kramdown mis-renders two patterns that have shipped broken pages: bare
``<details>``/``<summary>`` tags (the brief collapses into the toggle label
and literal ``</summary>`` text appears) and bare `` | `` separators (the
Sources line becomes a bordered table). The render check repairs those,
fails the run on structural damage, and the publish/translate stages mark
backfilled posts in their titles and frontmatter.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stages.render_check import check_and_repair, run_render_check
from stages.publish import _build_frontmatter, _is_backfill_run
from stages.translate_fa import (
    _build_fa_frontmatter,
    _fa_title_for_date,
    _reasoning_leak_issues,
)


CLEAN_ITEM = """**Key development:** Something happened.

## Military Operations

<details markdown="block">
<summary markdown="span">**One-liner.** Why it matters. *(BBC)*</summary>

Context: background.

Sources: [BBC](https://b.co) \\| [The Hindu](https://t.co)

</details>
"""


# ---------------------------------------------------------------------------
# check_and_repair
# ---------------------------------------------------------------------------


class TestCheckAndRepair:
    def test_clean_report_untouched(self):
        repaired, repairs, issues = check_and_repair(CLEAN_ITEM)
        assert repaired == CLEAN_ITEM
        assert repairs == []
        assert issues == []

    def test_adds_missing_markdown_attrs(self):
        broken = CLEAN_ITEM.replace(
            '<details markdown="block">', "<details>"
        ).replace('<summary markdown="span">', "<summary>")
        repaired, repairs, issues = check_and_repair(broken)

        assert '<details markdown="block">' in repaired
        assert '<summary markdown="span">' in repaired
        assert issues == []
        assert len(repairs) == 2

    def test_escapes_unescaped_source_pipes(self):
        broken = CLEAN_ITEM.replace(" \\| ", " | ")
        repaired, repairs, issues = check_and_repair(broken)

        assert "Sources: [BBC](https://b.co) \\| [The Hindu](https://t.co)" in repaired
        assert issues == []
        assert repairs == ["escaped pipe separator on sources line"]

    def test_collapses_overescaped_pipes(self):
        broken = CLEAN_ITEM.replace(" \\| ", " \\\\\\| ")
        repaired, _, _ = check_and_repair(broken)
        assert " \\| " in repaired
        assert "\\\\" not in repaired

    def test_farsi_sources_line(self):
        text = "منابع: [Al Jazeera](https://a.co) | [France 24](https://f.co)\n"
        repaired, repairs, _ = check_and_repair(text)
        assert "منابع: [Al Jazeera](https://a.co) \\| [France 24](https://f.co)" in repaired
        assert repairs == ["escaped pipe separator on sources line"]

    def test_prose_line_with_pipe_is_left_alone(self):
        text = "Iran | US talks stalled.\n"
        repaired, repairs, issues = check_and_repair(text)
        assert repaired == text
        assert repairs == []
        assert issues == []

    def test_flags_unbalanced_tags(self):
        broken = "<details markdown=\"block\">\n<summary>title</summary>\n"
        _, _, issues = check_and_repair(broken)
        assert any("unclosed tag" in i for i in issues)

    def test_flags_escaped_entities(self):
        broken = CLEAN_ITEM + "\n&lt;/summary&gt;\n"
        _, _, issues = check_and_repair(broken)
        assert any("escaped HTML entity" in i for i in issues)

    def test_flags_details_without_summary(self):
        broken = "<details markdown=\"block\">\n\nContext only.\n\n</details>\n"
        _, _, issues = check_and_repair(broken)
        assert any("without a <summary>" in i for i in issues)

    def test_empty_brief_passes(self):
        repaired, repairs, issues = check_and_repair(
            "No significant developments reported today.\n"
        )
        assert repairs == []
        assert issues == []


# ---------------------------------------------------------------------------
# run_render_check
# ---------------------------------------------------------------------------


class TestRunRenderCheck:
    def test_repairs_report_in_place(
        self, tmp_run_dir, sample_config, mock_llm_client
    ):
        report = Path(tmp_run_dir) / "report_verified.md"
        report.write_text(CLEAN_ITEM.replace(" \\| ", " | "))

        result = run_render_check(
            tmp_run_dir, sample_config, mock_llm_client, None
        )

        assert result["status"] == "passed"
        assert "\\|" in report.read_text()

    def test_raises_on_unresolved_issues(
        self, tmp_run_dir, sample_config, mock_llm_client
    ):
        report = Path(tmp_run_dir) / "report_verified.md"
        report.write_text("<details markdown=\"block\">\nno summary here\n")

        with pytest.raises(RuntimeError, match="Render check failed"):
            run_render_check(tmp_run_dir, sample_config, mock_llm_client, None)

        diagnostics = json.loads(
            (Path(tmp_run_dir) / "render_check.json").read_text()
        )
        assert diagnostics["issues"]

    def test_skips_when_no_report(
        self, tmp_run_dir, sample_config, mock_llm_client
    ):
        result = run_render_check(
            tmp_run_dir, sample_config, mock_llm_client, None
        )
        assert result["status"] == "skipped"

    def test_prefers_verified_report(
        self, tmp_run_dir, sample_config, mock_llm_client
    ):
        Path(tmp_run_dir, "report.md").write_text("<details>\n")
        verified = Path(tmp_run_dir, "report_verified.md")
        verified.write_text(CLEAN_ITEM)

        result = run_render_check(
            tmp_run_dir, sample_config, mock_llm_client, None
        )

        assert result["status"] == "passed"
        assert result["report"] == "report_verified.md"


# ---------------------------------------------------------------------------
# translate_fa reasoning-leak guard
# ---------------------------------------------------------------------------


class TestReasoningLeakGuard:
    def test_flags_reasoning_marker(self):
        fa = "متن\n\nThe user wants me to translate this brief.\n"
        issues = _reasoning_leak_issues(fa, "Some English body.")
        assert any("reasoning marker" in i for i in issues)

    def test_flags_high_latin_ratio(self):
        fa = "The model returned " + "reasoning text " * 40
        issues = _reasoning_leak_issues(fa, "short")
        assert any("latin-letter ratio" in i for i in issues)

    def test_flags_overlong_body(self):
        fa = "سلام " * 300
        issues = _reasoning_leak_issues(fa, "x" * 400)
        assert any("English body" in i for i in issues)

    def test_short_english_body_ignores_length_ratio(self):
        # A quiet-day one-liner must not trip the length heuristic.
        issues = _reasoning_leak_issues("امروز خبر مهمی نبود.", "No news today.")
        assert issues == []

    def test_good_translation_passes(self):
        fa = (
            "**مهم‌ترین تحول:** اتفاقی افتاد. *(BBC)*\n\n"
            "زمینه: توضیح. منابع: [BBC](https://b.co)\n"
        )
        assert _reasoning_leak_issues(fa, "**Key development:** something. *(BBC)*") == []


# ---------------------------------------------------------------------------
# Backfill marking
# ---------------------------------------------------------------------------


def _write_meta(run_dir: Path, backfill: bool) -> None:
    (run_dir / "run_meta.json").write_text(json.dumps({"backfill": backfill}))


class TestEnglishBackfillMarking:
    def test_title_and_flag_when_backfilled(self, tmp_path, sample_config):
        run_dir = tmp_path / "2026-09-14-120000"
        run_dir.mkdir()
        _write_meta(run_dir, backfill=True)

        fm = _build_frontmatter(sample_config, str(run_dir), ["m"], backfilled=True)

        assert 'title: "Daily Brief: September 14, 2026 (backfilled)"' in fm
        assert "backfilled: true" in fm

    def test_no_marker_for_normal_run(self, tmp_path, sample_config):
        run_dir = tmp_path / "2026-09-16-120000"
        run_dir.mkdir()
        _write_meta(run_dir, backfill=False)

        fm = _build_frontmatter(sample_config, str(run_dir), ["m"], backfilled=False)

        assert 'title: "Daily Brief: September 16, 2026"' in fm
        assert "backfilled" not in fm
        assert _is_backfill_run(str(run_dir)) is False

    def test_is_backfill_run_reads_meta(self, tmp_path):
        run_dir = tmp_path / "2026-09-14-120000"
        run_dir.mkdir()
        _write_meta(run_dir, backfill=True)
        assert _is_backfill_run(str(run_dir)) is True

    def test_is_backfill_run_missing_meta(self, tmp_path):
        run_dir = tmp_path / "2026-09-14-120000"
        run_dir.mkdir()
        assert _is_backfill_run(str(run_dir)) is False


class TestFarsiBackfillMarking:
    def test_title_suffix(self):
        assert _fa_title_for_date("۲۳ شهریور ۱۴۰۵") == "گزارش روزانه: ۲۳ شهریور ۱۴۰۵"
        assert (
            _fa_title_for_date("۲۳ شهریور ۱۴۰۵", backfilled=True)
            == "گزارش روزانه: ۲۳ شهریور ۱۴۰۵ (با تأخیر)"
        )

    def test_frontmatter_flag(self):
        fm = _build_fa_frontmatter(
            date_str="2026-09-14",
            fa_date_str="۲۳ شهریور ۱۴۰۵",
            fa_title="گزارش روزانه: ۲۳ شهریور ۱۴۰۵ (با تأخیر)",
            generated_at=None,
            sources_down=[],
            models_used=[],
            backfilled=True,
        )
        assert "backfilled: true" in fm
        assert "(با تأخیر)" in fm
        assert 'date_fa: "۲۳ شهریور ۱۴۰۵"' in fm
