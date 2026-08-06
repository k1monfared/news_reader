"""Audited LLM client wrapper for the news pipeline.

Every call to the LLM API is logged with full input/output, token counts,
timing, and cost tracking. A per-run budget cap prevents runaway usage.

The pipeline routes through OpenCode Zen (https://opencode.ai/zen) using the
OpenAI-compatible chat completions endpoint. The default model is the free
``deepseek-v4-flash-free`` (cost $0), which is not billed to the OpenCode Go
subscription.

Env vars:
    OPENCODE_API_KEY       required; OpenCode Zen / OpenCode Go API key
    OPENCODE_API_BASE_URL  optional; defaults to https://opencode.ai/zen/v1
"""

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx


def extract_json(text: str):
    """Parse an LLM response that should be JSON, tolerating markdown code
    fences and surrounding prose.

    The free model (deepseek-v4-flash-free) occasionally wraps its answer in
    ```json fences or adds prose around the JSON. This helper tries a plain
    parse first, then falls back to the first balanced ``{...}`` or ``[...]``
    span in the text.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        if "\n" in cleaned:
            cleaned = cleaned[cleaned.index("\n") + 1:]
        else:
            cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].rstrip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        spans = []
        for opener, closer in (("{", "}"), ("[", "]")):
            start = cleaned.find(opener)
            if start != -1:
                end = cleaned.rfind(closer)
                if end > start:
                    spans.append((start, end + 1))
        if not spans:
            raise
        spans.sort(key=lambda s: s[0])
        start, end = spans[0]
        return json.loads(cleaned[start:end])


class BudgetExceededError(Exception):
    """Raised when cumulative cost exceeds the configured budget cap."""
    pass


class AuditedLLMClient:
    """Wrapper around the OpenCode Zen chat completions API that audits every call.

    Audit artifacts per run:
        audit/llm_calls.jsonl   -- one JSON line per call (summary)
        audit/llm_inputs/       -- full input payloads, one file per call
        audit/llm_outputs/      -- full output payloads, one file per call
    """

    # Default cost estimates per million tokens (deepseek-v4-flash-free is $0)
    _INPUT_COST_PER_M = 0.0
    _OUTPUT_COST_PER_M = 0.0

    _DEFAULT_BASE_URL = "https://opencode.ai/zen/v1"

    def __init__(self, run_dir: str, config: dict) -> None:
        """Initialize the audited client.

        Args:
            run_dir: Path to the run directory, e.g.
                     ``data/runs/2026-03-29-080000/``.
            config:  The budget section from the pipeline config. Expected
                     keys: ``max_cost_per_run_usd`` (float).
        """
        self._api_key = os.environ.get("OPENCODE_API_KEY", "")
        if not self._api_key:
            raise RuntimeError(
                "OPENCODE_API_KEY is not set (required for OpenCode Zen)"
            )
        self._base_url = os.environ.get(
            "OPENCODE_API_BASE_URL", self._DEFAULT_BASE_URL
        ).rstrip("/")
        self._run_dir = Path(run_dir)
        self._max_cost = float(config.get("max_cost_per_run_usd", 1.0))
        self._max_attempts = int(config.get("llm_retry_attempts", 3))

        self._cumulative_cost = 0.0
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._prompt_versions_used: dict[str, int] = {}

        # Build audit directory structure
        self._audit_dir = self._run_dir / "audit"
        self._inputs_dir = self._audit_dir / "llm_inputs"
        self._outputs_dir = self._audit_dir / "llm_outputs"
        self._calls_file = self._audit_dir / "llm_calls.jsonl"

        self._inputs_dir.mkdir(parents=True, exist_ok=True)
        self._outputs_dir.mkdir(parents=True, exist_ok=True)
        # Touch the JSONL file so it exists from the start
        self._calls_file.touch(exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def call(
        self,
        stage: str,
        prompt_name: str,
        prompt_version: int,
        system: str,
        user_message: str,
        model: str = "deepseek-v4-flash-free",
        max_tokens: int = 4096,
    ) -> str:
        """Send a prompt to the OpenCode Zen API and return the response text.

        Every call is fully audited: input saved, output saved, and a
        summary line appended to ``llm_calls.jsonl``.

        Args:
            stage:          Pipeline stage name (e.g. ``"fetch"``, ``"rank"``).
            prompt_name:    Logical prompt identifier.
            prompt_version: Integer version of the prompt template.
            system:         System message content.
            user_message:   User message content.
            model:          Model identifier.
            max_tokens:     Maximum tokens in the response.

        Returns:
            The assistant's response text.

        Raises:
            BudgetExceededError: If cumulative cost would exceed the cap.
        """
        call_id = str(uuid.uuid4())
        input_hash = hashlib.sha256(
            (system + user_message).encode("utf-8")
        ).hexdigest()

        # Pre-flight budget check is intentionally skipped here so that
        # the check happens after we know the real token counts. A caller
        # that is already over budget will be caught at the end of the
        # previous call.
        if self._cumulative_cost >= self._max_cost:
            raise BudgetExceededError(
                f"Budget exhausted before call. "
                f"Cumulative cost ${self._cumulative_cost:.4f} "
                f">= cap ${self._max_cost:.2f}"
            )

        # Save full input payload
        input_payload = {
            "call_id": call_id,
            "stage": stage,
            "prompt_name": prompt_name,
            "prompt_version": prompt_version,
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "user_message": user_message,
        }
        self._inputs_dir.joinpath(f"{call_id}.json").write_text(
            json.dumps(input_payload, indent=2), encoding="utf-8"
        )

        # Call the API (OpenAI-compatible chat completions on OpenCode Zen)
        start = time.monotonic()
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        response_text, tokens_in, tokens_out, stop_reason = self._complete(
            payload, headers
        )
        duration_s = round(time.monotonic() - start, 3)
        output_hash = hashlib.sha256(
            response_text.encode("utf-8")
        ).hexdigest()

        # Save full output payload
        output_payload = {
            "call_id": call_id,
            "response_text": response_text,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "stop_reason": stop_reason,
        }
        self._outputs_dir.joinpath(f"{call_id}.json").write_text(
            json.dumps(output_payload, indent=2), encoding="utf-8"
        )

        # Track prompt versions used
        self._prompt_versions_used[prompt_name] = prompt_version

        # Cost accounting
        call_cost = (
            (tokens_in / 1_000_000) * self._INPUT_COST_PER_M
            + (tokens_out / 1_000_000) * self._OUTPUT_COST_PER_M
        )
        self._cumulative_cost += call_cost
        self._total_input_tokens += tokens_in
        self._total_output_tokens += tokens_out

        # Append summary line to JSONL
        timestamp = datetime.now(timezone.utc).isoformat()
        summary = {
            "stage": stage,
            "prompt_name": prompt_name,
            "prompt_version": prompt_version,
            "call_id": call_id,
            "input_hash": input_hash,
            "output_hash": output_hash,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "duration_s": duration_s,
            "timestamp": timestamp,
            "model": model,
        }
        with self._calls_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(summary) + "\n")

        # Post-call budget check
        if self._cumulative_cost >= self._max_cost:
            raise BudgetExceededError(
                f"Budget exceeded after call {call_id}. "
                f"Cumulative cost ${self._cumulative_cost:.4f} "
                f">= cap ${self._max_cost:.2f}"
            )

        return response_text

    def _complete(
        self, payload: dict, headers: dict
    ) -> tuple[str, int, int, str | None]:
        """POST to the OpenCode Zen chat completions endpoint with retries.

        The free model (deepseek-v4-flash-free) is a beta model that can
        return an empty ``content`` field (it emits its answer in
        ``reasoning_content``) or fail transiently with a 429/5xx, so retry
        a few times with backoff before giving up.

        Returns ``(response_text, tokens_in, tokens_out, stop_reason)``.
        """
        last_error: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(600.0, connect=10.0)
                ) as client:
                    response = client.post(
                        f"{self._base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                    )
            except httpx.HTTPError as e:
                last_error = RuntimeError(f"OpenCode Zen request failed: {e}")
            else:
                if response.status_code in (429, 500, 502, 503, 504):
                    last_error = RuntimeError(
                        f"OpenCode Zen API error {response.status_code}: "
                        f"{response.text[:200]}"
                    )
                elif response.status_code != 200:
                    raise RuntimeError(
                        f"OpenCode Zen API error {response.status_code}: "
                        f"{response.text[:500]}"
                    )
                else:
                    data = response.json()
                    content = (
                        data["choices"][0]["message"].get("content") or ""
                    ).strip()
                    if not content:
                        last_error = RuntimeError(
                            "OpenCode Zen returned empty content; retrying "
                            "(the free model may emit only reasoning_content)"
                        )
                    else:
                        usage = data.get("usage", {})
                        return (
                            content,
                            usage.get("prompt_tokens", 0),
                            usage.get("completion_tokens", 0),
                            data["choices"][0].get("finish_reason"),
                        )
            if attempt < self._max_attempts - 1:
                time.sleep(2 ** attempt)
        raise RuntimeError(
            f"OpenCode Zen call failed after {self._max_attempts} attempts: "
            f"{last_error}"
        )

    @property
    def total_cost(self) -> float:
        """Cumulative estimated cost in USD across all calls this run."""
        return self._cumulative_cost

    @property
    def total_tokens(self) -> dict:
        """Cumulative token counts across all calls this run."""
        return {
            "input": self._total_input_tokens,
            "output": self._total_output_tokens,
        }

    @property
    def prompt_versions_used(self) -> dict[str, int]:
        """Map of prompt_name to version for all prompts used this run."""
        return dict(self._prompt_versions_used)
