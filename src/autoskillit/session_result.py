"""Domain model for Claude Code headless session results.

L2 module: imports only from L0 (types, _logging). No server side-effects.
Centralizes all session-parsing concerns so callers can work with typed
objects instead of raw JSON strings.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from autoskillit._logging import get_logger
from autoskillit.types import CONTEXT_EXHAUSTION_MARKER, RetryReason, TerminationReason

logger = get_logger(__name__)


def _truncate(text: str, max_len: int = 5000) -> str:
    if len(text) <= max_len:
        return text
    return f"...[truncated {len(text) - max_len} chars]...\n" + text[-max_len:]


_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)

_FAILURE_SUBTYPES = frozenset({"unknown", "empty_output", "unparseable", "timeout"})


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
            if isinstance(self.result, list):
                self.result = "\n".join(
                    b.get("text", "") if isinstance(b, dict) else str(b) for b in self.result
                )
            elif not isinstance(self.result, str):
                self.result = "" if self.result is None else str(self.result)
        if not isinstance(self.errors, list):
            self.errors = [] if self.errors is None else [str(self.errors)]
        if not isinstance(self.subtype, str):
            self.subtype = "unknown" if self.subtype is None else str(self.subtype)
        if not isinstance(self.session_id, str):
            self.session_id = "" if self.session_id is None else str(self.session_id)

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


@dataclass
class SkillResult:
    """Typed result returned by _build_skill_result and _run_headless_core."""

    success: bool
    result: str
    session_id: str
    subtype: str
    is_error: bool
    exit_code: int
    needs_retry: bool
    retry_reason: RetryReason
    stderr: str
    token_usage: dict[str, Any] | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "success": self.success,
                "result": self.result,
                "session_id": self.session_id,
                "subtype": self.subtype,
                "is_error": self.is_error,
                "exit_code": self.exit_code,
                "needs_retry": self.needs_retry,
                "retry_reason": self.retry_reason,
                "stderr": self.stderr,
                "token_usage": self.token_usage,
            },
            default=lambda o: o.value if isinstance(o, Enum) else str(o),
        )


def extract_token_usage(stdout: str) -> dict[str, Any] | None:
    """Extract token usage from Claude CLI NDJSON output.

    Takes raw NDJSON *stdout* (not a parsed ClaudeSessionResult) because this
    function is called inside parse_session_result() *during construction* of
    ClaudeSessionResult — before that object exists. A (result: ClaudeSessionResult)
    parameter would create a circular bootstrapping dependency and is therefore
    architecturally incorrect for this call site.

    Scans assistant records for per-model usage and the result record for
    authoritative aggregated totals. Returns None if no usage data is found.
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


_KNOWN_RESULT_KEYS: frozenset[str] = frozenset(
    {"type", "subtype", "is_error", "result", "session_id", "errors", "usage"}
)


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

    extra_keys = frozenset(result_obj.keys()) - _KNOWN_RESULT_KEYS
    if extra_keys:
        logger.debug("unknown_result_keys", unknown_fields=sorted(extra_keys))

    return ClaudeSessionResult(
        subtype=result_obj.get("subtype", "unknown"),
        is_error=result_obj.get("is_error", False),
        result=result_obj.get("result", ""),
        session_id=result_obj.get("session_id", ""),
        errors=result_obj.get("errors", []),
        token_usage=token_usage,
    )


def _compute_success(
    session: ClaudeSessionResult,
    returncode: int,
    termination: TerminationReason,
    completion_marker: str = "",
) -> bool:
    """Cross-validate all signals to determine unambiguous success/failure."""
    if termination in (TerminationReason.TIMED_OUT, TerminationReason.STALE):
        return False
    if returncode != 0:
        # COMPLETED path: the process was killed by our own async_kill_process_tree
        # (signal -15 or -9), so a non-zero returncode is expected and trustworthy
        # when the session envelope says "success". Trust the envelope.
        #
        # NATURAL_EXIT path: the process exited on its own with an error code.
        # We cannot distinguish PTY-masking quirks from genuine CLI errors here,
        # so we fail conservatively. The session result record (if any) may still
        # be present in stdout — but a non-zero natural exit is treated as authoritative
        # evidence of failure. No asymmetric bypass is applied.
        if (
            termination == TerminationReason.COMPLETED
            and session.subtype == "success"
            and session.result.strip()
        ):
            pass  # fall through to remaining checks
        else:
            return False
    if session.is_error:
        return False
    if not session.result.strip():
        return False
    if session.subtype in _FAILURE_SUBTYPES:
        return False

    if completion_marker:
        result_text = session.result.strip()
        marker_stripped = result_text.replace(completion_marker, "").strip()
        if not marker_stripped:
            return False
        if completion_marker not in result_text:
            return False

    return True


_KILL_ANOMALY_SUBTYPES: frozenset[str] = frozenset(
    {
        "unparseable",  # killed mid-write → partial NDJSON
        "empty_output",  # killed before any stdout was written
    }
)


def _is_completion_kill_anomaly(session: ClaudeSessionResult) -> bool:
    """True if the session result looks like a kill-induced incomplete flush.

    When termination == COMPLETED, the process was killed by our infrastructure
    (Channel B session monitor or Channel A heartbeat). Any result that is NOT
    a genuine success is therefore our infrastructure's fault, not the task's.

    Covers:
    - unparseable: process killed mid-write, stdout is partial NDJSON
    - empty_output: process killed before any stdout was written
    - success with empty result: kill occurred after result record was written
      but with empty content (Channel B / Channel A drain-race)
    """
    if session.subtype in _KILL_ANOMALY_SUBTYPES:
        return True
    if session.subtype == "success" and not session.result.strip():
        return True
    return False


def _compute_retry(
    session: ClaudeSessionResult,
    returncode: int,
    termination: TerminationReason,
) -> tuple[bool, RetryReason]:
    """Cross-validate all signals to determine retry eligibility."""
    # API-level retry: Claude API told us to retry (context exhaustion, max turns)
    if session.needs_retry:
        return True, RetryReason.RESUME

    # Infrastructure anomaly: CLI launched, wrote nothing, exited cleanly.
    # Covers: claude binary not found, immediate crash before any output.
    # Uses returncode == 0 to distinguish from kill-induced empty output (COMPLETED path below).
    if session.subtype == "empty_output" and returncode == 0:
        return True, RetryReason.RESUME

    # Infrastructure anomaly: process was killed by our infrastructure (Channel B/A race).
    # COMPLETED means we killed the process; any non-genuine-success result is our fault.
    if termination == TerminationReason.COMPLETED and _is_completion_kill_anomaly(session):
        return True, RetryReason.RESUME

    return False, RetryReason.NONE
