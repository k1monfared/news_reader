"""Audited LLM client wrapper for the news pipeline.

Every call to the Anthropic API is logged with full input/output,
token counts, timing, and cost tracking. A per-run budget cap
prevents runaway spending.
"""

import anthropic
import json
import hashlib
import uuid
import time
from datetime import datetime, timezone
from pathlib import Path


class BudgetExceededError(Exception):
    """Raised when cumulative cost exceeds the configured budget cap."""
    pass


class AuditedLLMClient:
    """Wrapper around anthropic.Anthropic() that audits every call.

    Audit artifacts per run:
        audit/llm_calls.jsonl   -- one JSON line per call (summary)
        audit/llm_inputs/       -- full input payloads, one file per call
        audit/llm_outputs/      -- full output payloads, one file per call
    """

    # Default cost estimates per million tokens (Sonnet)
    _INPUT_COST_PER_M = 3.0
    _OUTPUT_COST_PER_M = 15.0

    def __init__(self, run_dir: str, config: dict) -> None:
        """Initialize the audited client.

        Args:
            run_dir: Path to the run directory, e.g.
                     ``data/runs/2026-03-29-080000/``.
            config:  The budget section from the pipeline config. Expected
                     keys: ``max_cost_per_run_usd`` (float).
        """
        self._client = anthropic.Anthropic()
        self._run_dir = Path(run_dir)
        self._max_cost = float(config.get("max_cost_per_run_usd", 1.0))

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
        model: str = "claude-sonnet-4-5",
        max_tokens: int = 4096,
    ) -> str:
        """Send a prompt to the Anthropic API and return the response text.

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

        # Call the API
        start = time.monotonic()
        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        duration_s = round(time.monotonic() - start, 3)

        # Extract results
        response_text = response.content[0].text
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
        output_hash = hashlib.sha256(
            response_text.encode("utf-8")
        ).hexdigest()

        # Save full output payload
        output_payload = {
            "call_id": call_id,
            "response_text": response_text,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "stop_reason": response.stop_reason,
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
