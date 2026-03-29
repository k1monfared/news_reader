import httpx
import json
import time
from datetime import datetime, timezone
from pathlib import Path


class AuditedHTTPClient:
    """HTTP client wrapper that logs all API calls to an audit trail."""

    def __init__(self, run_dir: str, timeout: float = 30.0):
        self.run_dir = Path(run_dir)
        self.audit_dir = self.run_dir / "audit"
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.audit_file = self.audit_dir / "api_calls.jsonl"
        self.client = httpx.Client(timeout=timeout)

    def _log_call(self, source: str, url: str, method: str, response: httpx.Response, duration_s: float):
        entry = {
            "source": source,
            "url": url,
            "method": method,
            "status_code": response.status_code,
            "response_size_bytes": len(response.content),
            "duration_s": round(duration_s, 4),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with open(self.audit_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def get(self, url: str, **kwargs) -> httpx.Response:
        source = kwargs.pop("source", "unknown")
        start = time.monotonic()
        response = self.client.get(url, **kwargs)
        duration = time.monotonic() - start
        self._log_call(source, url, "GET", response, duration)
        return response

    def head(self, url: str, **kwargs) -> httpx.Response:
        source = kwargs.pop("source", "unknown")
        start = time.monotonic()
        response = self.client.head(url, **kwargs)
        duration = time.monotonic() - start
        self._log_call(source, url, "HEAD", response, duration)
        return response

    def request_with_retry(self, method: str, url: str, max_retries: int = 3, backoff_base: int = 2, **kwargs) -> httpx.Response:
        source = kwargs.pop("source", "unknown")
        last_exception = None

        for attempt in range(max_retries + 1):
            try:
                start = time.monotonic()
                response = self.client.request(method, url, **kwargs)
                duration = time.monotonic() - start
                self._log_call(source, url, method.upper(), response, duration)

                if response.status_code >= 500 and attempt < max_retries:
                    time.sleep(backoff_base ** attempt)
                    continue

                return response

            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exception = exc
                if attempt < max_retries:
                    time.sleep(backoff_base ** attempt)
                    continue
                raise

        raise last_exception

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
