"""Audited LLM client wrapper for the news pipeline.

Every call to the LLM API is logged with full input/output, token counts,
timing, and cost tracking. A per-run budget cap prevents runaway usage.

The pipeline routes through OpenCode Zen (https://opencode.ai/zen) using the
OpenAI-compatible chat completions endpoint. Free-tier Zen models are used
(cost $0), so nothing is billed to the OpenCode Go subscription.

Runtime failover: each call resolves a chain of models from config
(``models.default`` / ``models.translate_fa`` primary plus ``models.fallbacks``).
Transient errors (429 rate limits, 5xx, network failures, empty responses)
are retried per model; permanent provider errors (e.g. 400 "Model is
unavailable") skip to the next model immediately. The winning model and any
failed-over attempts are recorded in the audit trail.

Env vars:
    OPENCODE_API_KEY       required; OpenCode Zen / OpenCode Go API key
    OPENCODE_API_BASE_URL  optional; defaults to https://opencode.ai/zen/v1
"""

import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


def extract_json(text: str):
    """Parse an LLM response that should be JSON, tolerating markdown code
    fences and surrounding prose.

    Free-tier Zen models occasionally wrap answers in ```json fences or add
    prose around the JSON. This helper tries a plain parse first, then falls
    back to the first balanced ``{...}`` or ``[...]`` span in the text.
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


class ModelUnavailableError(RuntimeError):
    """Raised when a model rejects a request permanently (e.g. 400/401 with
    "Model is unavailable"). Triggers immediate failover to the next model
    instead of retrying the same one."""
    pass


class AuditedLLMClient:
    """Wrapper around the OpenCode Zen chat completions API that audits every call.

    Audit artifacts per run:
        audit/llm_calls.jsonl   -- one JSON line per call (summary)
        audit/llm_inputs/       -- full input payloads, one file per call
        audit/llm_outputs/      -- full output payloads, one file per call
    """

    # Cost estimates per million tokens (all configured models are free tier)
    _INPUT_COST_PER_M = 0.0
    _OUTPUT_COST_PER_M = 0.0

    _DEFAULT_BASE_URL = "https://opencode.ai/zen/v1"

    def __init__(
        self,
        run_dir: str,
        config: dict,
        models_cfg: dict | None = None,
    ) -> None:
        """Initialize the audited client.

        Args:
            run_dir:    Path to the run directory, e.g.
                        ``data/runs/2026-03-29-080000/``.
            config:     The budget section from the pipeline config. Expected
                        keys: ``max_cost_per_run_usd`` (float).
            models_cfg: Optional ``models`` section from the pipeline config.
                        Expected keys: ``default`` (primary model used when a
                        caller does not pass one explicitly) and ``fallbacks``
                        (list of model ids tried in order when the primary
                        fails).
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
        self._fallbacks: list[str] = list((models_cfg or {}).get("fallbacks", []))
        self._primary_default: str = (models_cfg or {}).get("default", "")

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
        model: str | None = None,
        max_tokens: int = 4096,
    ) -> str:
        """Send a prompt to the OpenCode Zen API and return the response text.

        Every call is fully audited: input saved, output saved, and a
        summary line appended to ``llm_calls.jsonl``. The request walks the
        failover chain (requested model first, then configured fallbacks)
        until one answers.

        Args:
            stage:          Pipeline stage name (e.g. ``"fetch"``, ``"rank"``).
            prompt_name:    Logical prompt identifier.
            prompt_version: Integer version of the prompt template.
            system:         System message content.
            user_message:   User message content.
            model:          Primary model identifier. When None, falls back
                            to ``models.default`` from config.
            max_tokens:     Maximum tokens in the response.

        Returns:
            The assistant's response text.

        Raises:
            BudgetExceededError: If cumulative cost would exceed the cap.
            RuntimeError: If every model in the failover chain fails.
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

        # Save full input payload (chain primary recorded as requested model)
        primary = model or self._primary_default
        chain = [primary] + [m for m in self._fallbacks if m != primary]
        input_payload = {
            "call_id": call_id,
            "stage": stage,
            "prompt_name": prompt_name,
            "prompt_version": prompt_version,
            "model": chain[0],
            "failover_chain": chain,
            "max_tokens": max_tokens,
            "system": system,
            "user_message": user_message,
        }
        self._inputs_dir.joinpath(f"{call_id}.json").write_text(
            json.dumps(input_payload, indent=2), encoding="utf-8"
        )

        # Call the API (OpenAI-compatible chat completions on OpenCode Zen),
        # walking the failover chain until a model answers.
        start = time.monotonic()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        attempts: list[dict] = []
        response_text = ""
        tokens_in = tokens_out = 0
        stop_reason: str | None = None
        served_model: str | None = None
        for chain_model in chain:
            try:
                (
                    response_text,
                    tokens_in,
                    tokens_out,
                    stop_reason,
                ) = self._complete(chain_model, system, user_message, max_tokens, headers)
                served_model = chain_model
                break
            except ModelUnavailableError as e:
                attempts.append({"model": chain_model, "error": str(e)})
                logger.warning(
                    f"Stage {stage}: model {chain_model} is unavailable, "
                    f"failing over to next model"
                )
            except RuntimeError as e:
                attempts.append({"model": chain_model, "error": str(e)})
                logger.warning(
                    f"Stage {stage}: model {chain_model} exhausted retries, "
                    f"failing over to next model: {e}"
                )
        if served_model is None:
            raise RuntimeError(
                f"Stage {stage}: all {len(chain)} models in failover chain "
                f"failed: {json.dumps(attempts)}"
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
            "failed_over_from": attempts,
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
            "model": served_model,
            "failed_over_from": [a["model"] for a in attempts],
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
        self,
        model: str,
        system: str,
        user_message: str,
        max_tokens: int,
        headers: dict,
    ) -> tuple[str, int, int, str | None]:
        """POST to the OpenCode Zen chat completions endpoint with retries
        for a single model.

        Free-tier Zen models can fail transiently (429 rate limits, 5xx,
        network errors) or return an empty ``content`` field (the model
        emitted only ``reasoning_content``), so retry a few times with
        backoff before giving up on this model. Permanent rejections (400/
        401/403/404, e.g. "Model is unavailable") raise
        ``ModelUnavailableError`` immediately so the caller can fail over.

        Returns ``(response_text, tokens_in, tokens_out, stop_reason)``.
        """
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
        }
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
                        f"OpenCode Zen API error {response.status_code} "
                        f"from {model}: {response.text[:200]}"
                    )
                elif response.status_code != 200:
                    raise ModelUnavailableError(
                        f"Model {model} rejected the request "
                        f"({response.status_code}): {response.text[:300]}"
                    )
                else:
                    try:
                        data = response.json()
                    except Exception as e:
                        last_error = RuntimeError(
                            f"Model {model} returned invalid JSON: {e}; "
                            f"body={response.text[:300]}"
                        )
                    else:
                        try:
                            choices = data.get("choices")
                            if not choices or not isinstance(choices, list):
                                raise KeyError("choices")
                            first = choices[0]
                            if not isinstance(first, dict):
                                raise KeyError("choices[0] is not a dict")
                            message = first.get("message") or {}
                            if not isinstance(message, dict):
                                raise KeyError("message is not a dict")
                            content = (message.get("content") or "").strip()
                        except (KeyError, IndexError, TypeError, AttributeError) as e:
                            last_error = RuntimeError(
                                f"Model {model} returned malformed response "
                                f"missing 'choices': {e}; body={str(data)[:400]}"
                            )
                        else:
                            if not content:
                                last_error = RuntimeError(
                                    f"Model {model} returned empty content; retrying "
                                    "(free models may emit only reasoning_content)"
                                )
                            else:
                                usage = data.get("usage", {}) if isinstance(data.get("usage"), dict) else {}
                                return (
                                    content,
                                    usage.get("prompt_tokens", 0),
                                    usage.get("completion_tokens", 0),
                                    first.get("finish_reason"),
                                )
            if attempt < self._max_attempts - 1:
                time.sleep(2 ** attempt)
        raise RuntimeError(
            f"Model {model} failed after {self._max_attempts} attempts: "
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
