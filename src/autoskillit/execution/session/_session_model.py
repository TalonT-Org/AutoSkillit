"""Session result model and parser — private sub-module of execution/session.py."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, assert_never

from autoskillit.core import (
    CONTEXT_EXHAUSTION_MARKER,
    CliSubtype,
    RetryReason,
    SessionOutcome,
    get_logger,
)

logger = get_logger(__name__)

_ABS_PATH_RE: re.Pattern[str] = re.compile(r'(?:^|[\s="\'])(/(?:[a-zA-Z0-9._/~@+-]+))')

_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)

FAILURE_SUBTYPES: frozenset[CliSubtype] = frozenset(
    {
        CliSubtype.UNKNOWN,
        CliSubtype.EMPTY_OUTPUT,
        CliSubtype.UNPARSEABLE,
        CliSubtype.TIMEOUT,
        CliSubtype.IDLE_STALL,
    }
)


class ContentState(StrEnum):
    """Content evaluation state for dead-end / drain-race guard dispatch."""

    COMPLETE = "complete"
    ABSENT = "absent"  # Empty result or missing marker — drain-race candidate
    CONTRACT_VIOLATION = "contract_violation"  # Result + marker present, but patterns fail
    SESSION_ERROR = "session_error"  # is_error=True or process-level failure


@dataclass
class ClaudeSessionResult:
    """Parsed result from a Claude Code headless session."""

    subtype: CliSubtype
    is_error: bool
    result: str
    session_id: str
    errors: list[str] = field(default_factory=list)
    token_usage: dict[str, Any] | None = None
    assistant_messages: list[str] = field(default_factory=list)
    tool_uses: list[dict[str, Any]] = field(default_factory=list)
    jsonl_context_exhausted: bool = False
    stop_reasons: list[str] = field(default_factory=list)

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
        if not isinstance(self.subtype, CliSubtype):
            self.subtype = (
                CliSubtype.UNKNOWN
                if self.subtype is None
                else CliSubtype.from_cli(str(self.subtype))
            )
        if not isinstance(self.session_id, str):
            self.session_id = "" if self.session_id is None else str(self.session_id)
        if not isinstance(self.jsonl_context_exhausted, bool):
            self.jsonl_context_exhausted = bool(self.jsonl_context_exhausted)

    def _is_context_exhausted(self) -> bool:
        """True when the session hit Claude's context window limit."""
        if self.jsonl_context_exhausted:
            return True
        if not self.is_error:
            return False
        marker = CONTEXT_EXHAUSTION_MARKER
        if any(marker in e.lower() for e in self.errors):
            return True
        if (
            self.subtype in (CliSubtype.SUCCESS, CliSubtype.ERROR_MAX_TURNS)
            and marker in self.result.lower()
        ):
            return True
        return False

    @property
    def agent_result(self) -> str:
        """Result text rewritten for LLM agent consumption."""
        if self._is_context_exhausted():
            return (
                "Context limit reached during session execution. "
                "The session made partial progress. "
                "Use needs_retry and retry_reason to continue from where it left off."
            )
        if self.subtype == CliSubtype.ERROR_MAX_TURNS:
            return (
                "Turn limit reached during session execution. "
                "The session made partial progress. "
                "Use needs_retry and retry_reason to continue from where it left off."
            )
        return self.result

    @property
    def needs_retry(self) -> bool:
        """Whether the session didn't finish and should be retried."""
        if self.subtype == CliSubtype.ERROR_MAX_TURNS:
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

    def normalize_subtype(self, outcome: SessionOutcome, completion_marker: str) -> str:
        """Map (outcome, completion_marker) → adjudicated subtype string.

        Class 2 (upward): SUCCEEDED + failure subtype → "success".
        Class 1 (downward): non-SUCCEEDED + success subtype → synthesized label.
        Return type is str because downward normalization synthesizes labels not in CliSubtype.
        """
        match self.subtype:
            case CliSubtype.SUCCESS:
                if outcome == SessionOutcome.SUCCEEDED:
                    return self.subtype
                if not self.result.strip():
                    return "empty_result"
                if self._is_context_exhausted():
                    return "context_exhausted"
                if completion_marker and completion_marker not in self.result:
                    return "missing_completion_marker"
                return "adjudicated_failure"
            case (
                CliSubtype.UNKNOWN
                | CliSubtype.EMPTY_OUTPUT
                | CliSubtype.UNPARSEABLE
                | CliSubtype.TIMEOUT
                | CliSubtype.IDLE_STALL
            ):
                if outcome == SessionOutcome.SUCCEEDED:
                    return "success"
                return self.subtype
            case (
                CliSubtype.ERROR_MAX_TURNS
                | CliSubtype.ERROR_DURING_EXECUTION
                | CliSubtype.CONTEXT_EXHAUSTION
                | CliSubtype.INTERRUPTED
            ):
                return self.subtype
            case _ as unreachable:
                assert_never(unreachable)

    @property
    def session_complete(self) -> bool:
        """True when not in error state and subtype is not in the failure set."""
        return not self.is_error and self.subtype not in FAILURE_SUBTYPES

    @property
    def last_stop_reason(self) -> str:
        """The stop_reason from the final assistant turn, or empty string."""
        return self.stop_reasons[-1] if self.stop_reasons else ""

    @property
    def lifespan_started(self) -> bool:
        """Heuristic: True when at least one MCP tool call was observed."""
        return bool(self.tool_uses)


def extract_token_usage(stdout: str) -> dict[str, Any] | None:
    """Extract token usage from Claude CLI NDJSON output.

    Takes raw stdout (not ClaudeSessionResult) — called during parse_session_result
    construction before the object exists. Returns None if no usage data found.
    """
    if not stdout.strip():
        return None

    model_buckets: dict[str, dict[str, int]] = {}
    result_usage: dict[str, int] | None = None
    peak_context = 0
    turn_count = 0

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
            cr = usage.get("cache_read_input_tokens", 0)
            if cr > peak_context:
                peak_context = cr
            turn_count += 1
        elif record_type == "result":
            usage = obj.get("usage")
            if isinstance(usage, dict):
                result_usage = {f: usage.get(f, 0) for f in _TOKEN_FIELDS}

    if not model_buckets and result_usage is None:
        return None

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
        "peak_context": peak_context,
        "turn_count": turn_count,
    }


_KNOWN_RESULT_KEYS: frozenset[str] = frozenset(
    {"type", "subtype", "is_error", "result", "session_id", "errors", "usage"}
)


@dataclass
class _ParseAccumulator:
    """Mutable accumulator for parse_session_result's NDJSON scan."""

    result_obj: dict[str, Any] | None = None
    tool_uses: list[dict[str, Any]] = field(default_factory=list)
    assistant_messages: list[str] = field(default_factory=list)
    jsonl_context_exhausted: bool = False
    stop_reasons: list[str] = field(default_factory=list)


def parse_session_result(stdout: str) -> ClaudeSessionResult:
    """Parse Claude Code NDJSON stdout into a typed result.

    Scans all NDJSON records into a _ParseAccumulator then constructs a single
    ClaudeSessionResult — no early returns, ensuring tool_uses and token_usage
    are preserved on all paths including fallback/unparseable.
    """
    if not stdout.strip():
        return ClaudeSessionResult(
            subtype=CliSubtype.EMPTY_OUTPUT,
            is_error=True,
            result="",
            session_id="",
            errors=[],
        )

    acc = _ParseAccumulator()
    marker = CONTEXT_EXHAUSTION_MARKER

    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                continue
            record_type = obj.get("type")
            if record_type == "result":
                acc.result_obj = obj
            elif record_type == "assistant":
                msg = obj.get("message")
                if isinstance(msg, dict):
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_use":
                                name = block.get("name", "")
                                entry: dict[str, str | list[str]] = {
                                    "name": name,
                                    "id": block.get("id", ""),
                                }
                                if name in {"Write", "Edit"} and isinstance(
                                    block.get("input"), dict
                                ):
                                    fp = block["input"].get("file_path", "")
                                    if fp:
                                        entry["file_path"] = fp
                                elif name == "Bash" and isinstance(block.get("input"), dict):
                                    command = block["input"].get("command", "")
                                    if isinstance(command, str):
                                        paths = [
                                            m.group(1)
                                            for m in _ABS_PATH_RE.finditer(command)
                                            if len(m.group(1)) >= 5
                                        ]
                                        if paths:
                                            entry["bash_paths"] = paths
                                acc.tool_uses.append(entry)
                        text = "\n".join(
                            block.get("text", "") for block in content if isinstance(block, dict)
                        ).strip()
                    else:
                        text = str(content).strip()
                    if text:
                        acc.assistant_messages.append(text)
                    _stop = msg.get("stop_reason", "")
                    if _stop:
                        acc.stop_reasons.append(str(_stop))
                elif "message" not in obj:
                    # Flat assistant record — detect context exhaustion inline.
                    if obj.get("output_tokens", -1) == 0:
                        flat_content = obj.get("content", [])
                        if isinstance(flat_content, list) and any(
                            isinstance(block, dict)
                            and block.get("type") == "text"
                            and marker in block.get("text", "").lower()
                            for block in flat_content
                        ):
                            acc.jsonl_context_exhausted = True
        except json.JSONDecodeError:
            continue

    if acc.result_obj is None:
        try:
            fallback = json.loads(stdout)
            if isinstance(fallback, dict) and fallback.get("type") == "result":
                acc.result_obj = fallback
        except json.JSONDecodeError:
            pass

    token_usage = extract_token_usage(stdout)

    if acc.result_obj is not None:
        extra_keys = frozenset(acc.result_obj.keys()) - _KNOWN_RESULT_KEYS
        if extra_keys:
            logger.debug("unknown_result_keys", unknown_fields=sorted(extra_keys))
        subtype = CliSubtype.from_cli(acc.result_obj.get("subtype") or "unknown")
        is_error: bool = acc.result_obj.get("is_error", False)
        result_text: str = acc.result_obj.get("result") or ""
        session_id: str = acc.result_obj.get("session_id") or ""
        errors: list[str] = acc.result_obj.get("errors") or []
    else:
        if acc.jsonl_context_exhausted:
            subtype = CliSubtype.CONTEXT_EXHAUSTION
        else:
            subtype = CliSubtype.UNPARSEABLE
        is_error = True
        result_text = stdout
        session_id = ""
        errors = []

    return ClaudeSessionResult(
        subtype=subtype,
        is_error=is_error,
        result=result_text,
        session_id=session_id,
        errors=errors,
        token_usage=token_usage,
        assistant_messages=acc.assistant_messages,
        tool_uses=acc.tool_uses,
        jsonl_context_exhausted=acc.jsonl_context_exhausted,
        stop_reasons=acc.stop_reasons,
    )
