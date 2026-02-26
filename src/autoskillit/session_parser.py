"""NDJSON result-parsing for Claude Code headless sessions.

No MCP dependencies. Pure data extraction and typed result construction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Self

from autoskillit._logging import get_logger
from autoskillit.process_lifecycle import _extract_text_content
from autoskillit.types import CONTEXT_EXHAUSTION_MARKER, RetryReason

logger = get_logger(__name__)

_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


@dataclass
class ClaudeSessionResult:
    """Parsed result from a Claude Code headless session."""

    subtype: str  # "success", "error_max_turns", "error_during_execution", etc.
    is_error: bool
    result: str
    session_id: str
    errors: list[str] = field(default_factory=list)
    token_usage: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.result, str):
            self.result = _extract_text_content(self.result)
        if not isinstance(self.errors, list):
            self.errors = [] if self.errors is None else [str(self.errors)]
        if not isinstance(self.subtype, str):
            self.subtype = "unknown" if self.subtype is None else str(self.subtype)
        if not isinstance(self.session_id, str):
            self.session_id = "" if self.session_id is None else str(self.session_id)

    @classmethod
    def from_result_dict(
        cls,
        result_obj: dict[str, Any],
        token_usage: dict[str, Any] | None = None,
    ) -> Self:
        """Construct a ClaudeSessionResult from a parsed result JSON object.

        Makes the field-mapping contract explicit. token_usage is extracted
        separately by extract_token_usage() and passed by the caller.
        """
        return cls(
            subtype=result_obj.get("subtype", "unknown"),
            is_error=result_obj.get("is_error", False),
            result=result_obj.get("result", ""),
            session_id=result_obj.get("session_id", ""),
            errors=list(result_obj.get("errors", [])),
            token_usage=token_usage,
        )

    def _is_context_exhausted(self) -> bool:
        """Detect context window exhaustion from Claude's error output.

        Requires both ``is_error=True`` AND the marker to appear in the
        ``errors`` list (structured CLI signal).  Falls back to checking
        ``result`` only when the subtype is a known error subtype, to
        narrow false-positives from model prose that happens to contain
        the marker phrase.
        """
        if not self.is_error:
            return False
        # Primary: check the structured errors list from Claude CLI
        marker = CONTEXT_EXHAUSTION_MARKER
        if any(marker in e.lower() for e in self.errors):
            return True
        # Fallback: only trust result text for error subtypes, not execution errors
        # where the model's own output could contain the marker phrase
        if self.subtype in ("success", "error_max_turns") and marker in self.result.lower():
            return True
        return False

    @property
    def agent_result(self) -> str:
        """Result text rewritten for LLM agent consumption.

        When the session ended due to a retriable condition (context exhaustion,
        max turns), the raw result text from Claude CLI can be misleading to
        LLM callers. This property returns semantically correct, actionable text.
        The raw result is preserved in self.result for debugging.
        """
        if self._is_context_exhausted():
            return (
                "Context limit reached during session execution. "
                "The session made partial progress. "
                "Use needs_retry and retry_reason to continue from where it left off."
            )
        if self.subtype == "error_max_turns":
            return (
                "Turn limit reached during session execution. "
                "The session made partial progress. "
                "Use needs_retry and retry_reason to continue from where it left off."
            )
        return self.result

    @property
    def needs_retry(self) -> bool:
        """Whether the session didn't finish and should be retried."""
        if self.subtype == "error_max_turns":
            return True
        if self._is_context_exhausted():
            return True
        return False

    @property
    def retry_reason(self) -> RetryReason:
        """Why retry is needed. NONE if needs_retry is False."""
        if self.needs_retry:
            return RetryReason.RESUME
        return RetryReason.NONE


def extract_token_usage(stdout: str) -> dict[str, Any] | None:
    """Extract token usage from Claude CLI NDJSON output.

    Scans assistant records for per-model usage and the result record
    for authoritative aggregated totals.  Returns None if no usage
    data is found.
    """
    if not stdout.strip():
        return None

    model_buckets: dict[str, dict[str, int]] = {}
    result_usage: dict[str, int] | None = None

    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue

        record_type = obj.get("type")

        if record_type == "assistant":
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            model = msg.get("model", "unknown")
            bucket = model_buckets.setdefault(model, {f: 0 for f in _TOKEN_FIELDS})
            for f in _TOKEN_FIELDS:
                bucket[f] += usage.get(f, 0)

        elif record_type == "result":
            usage = obj.get("usage")
            if isinstance(usage, dict):
                result_usage = {f: usage.get(f, 0) for f in _TOKEN_FIELDS}

    if not model_buckets and result_usage is None:
        return None

    # Aggregated totals: prefer result record, fall back to assistant sum
    if result_usage is not None:
        totals = dict(result_usage)
    else:
        totals = {f: 0 for f in _TOKEN_FIELDS}
        for bucket in model_buckets.values():
            for f in _TOKEN_FIELDS:
                totals[f] += bucket[f]

    return {
        **totals,
        "model_breakdown": dict(model_buckets) if model_buckets else {},
    }


def parse_session_result(stdout: str) -> ClaudeSessionResult:
    """Parse Claude Code's --output-format json stdout into a typed result.

    Handles multi-line NDJSON (Claude Code may emit multiple JSON objects;
    the last 'result' type object is authoritative).
    Falls back gracefully for non-JSON or missing fields.
    """
    if not stdout.strip():
        return ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result="",
            session_id="",
            errors=[],
        )

    result_obj = None
    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and obj.get("type") == "result":
                result_obj = obj
        except json.JSONDecodeError:
            continue

    if result_obj is None:
        try:
            fallback = json.loads(stdout)
            if isinstance(fallback, dict) and fallback.get("type") == "result":
                result_obj = fallback
            else:
                return ClaudeSessionResult(
                    subtype="unparseable",
                    is_error=True,
                    result=stdout,
                    session_id="",
                    errors=[],
                )
        except json.JSONDecodeError:
            return ClaudeSessionResult(
                subtype="unparseable",
                is_error=True,
                result=stdout,
                session_id="",
                errors=[],
            )

    token_usage = extract_token_usage(stdout)
    return ClaudeSessionResult.from_result_dict(result_obj, token_usage=token_usage)
