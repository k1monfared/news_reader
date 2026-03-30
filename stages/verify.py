"""Verify stage: checks all URLs in the report are accessible."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from models import PipelineConfig
from llm_client import AuditedLLMClient
from audit_logger import AuditedHTTPClient

logger = logging.getLogger(__name__)

URL_PATTERN = re.compile(r'\[([^\]]+)\]\((https?://[^\)]+)\)')


def run_verify(
    run_dir: str,
    config: PipelineConfig,
    llm_client: AuditedLLMClient,
    http_client: AuditedHTTPClient,
) -> dict:
    run_path = Path(run_dir)

    # Read the edited report
    report_path = run_path / "report_edited.md"
    if not report_path.exists():
        report_path = run_path / "report.md"
    if not report_path.exists():
        logger.warning("No report found to verify")
        return {"status": "skipped"}

    report = report_path.read_text()

    # Find all markdown links
    matches = URL_PATTERN.findall(report)
    if not matches:
        logger.info("No URLs found in report")
        (run_path / "report_verified.md").write_text(report)
        return {"urls_checked": 0, "urls_valid": 0, "urls_invalid": 0}

    valid = 0
    invalid = 0
    replacements = {}

    for link_text, url in matches:
        try:
            response = http_client.head(url, source="verify", follow_redirects=True)
            if response.status_code in (200, 301, 302, 303, 307, 308):
                valid += 1
            else:
                logger.warning(f"URL check failed ({response.status_code}): {url}")
                invalid += 1
                replacements[url] = True
        except Exception as e:
            logger.warning(f"URL check error for {url}: {e}")
            invalid += 1
            replacements[url] = True

    # Keep invalid URLs but mark them so readers know the link may be dead
    verified_report = report
    for url in replacements:
        verified_report = verified_report.replace(
            f"]({url})",
            f"]({url} \"link may be dead\")",
        )

    (run_path / "report_verified.md").write_text(verified_report)

    logger.info(f"Verified {len(matches)} URLs: {valid} valid, {invalid} invalid")
    return {"urls_checked": len(matches), "urls_valid": valid, "urls_invalid": invalid}
