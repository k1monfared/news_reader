"""Base fetcher interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from models import RawItem, SourceConfig
from audit_logger import AuditedHTTPClient


class BaseFetcher(ABC):
    """Common interface for all source fetchers."""

    def __init__(self, source_config: SourceConfig, http_client: AuditedHTTPClient):
        self.config = source_config
        self.http = http_client

    @abstractmethod
    def fetch(self) -> list[RawItem]:
        """Fetch raw items from this source.

        Returns:
            List of RawItem instances, up to max_items.
        """

    def validate_response(self, response) -> bool:
        """Check that the response looks like expected content."""
        return response.status_code == 200 and len(response.content) > 0
