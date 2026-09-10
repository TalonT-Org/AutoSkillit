"""Session result model and parser — private sub-module of execution/session.py."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, assert_never

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    CODEX_CONTEXT_EXHAUSTION_MARKER,
    CONTEXT_EXHAUSTION_MARKER,
    ClaudeContentBlockType,
    CliSubtype,
    RetryReason,
    SessionOutcome,
    TurnTokenEntry,
    get_logger,
)
from autoskillit.execution.session._provider_parse import _parse_provider_records
from autoskillit.execution.session._turn_usage import (
    build_turn_token_entry,
    merge_turn_usage,
    valid_context_window,
    valid_token_count,
)

logger = get_logger(__name__)

_API_TOKEN_FIELDS, _CANONICAL_TOKEN_FIELDS = (
    ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"),
    ("input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens"),
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
    ABSENT = "absent"  # Empty result or marker absent (no pattern match) — drain-race
    MARKER_ABSENT_CONTRACT_MET = "marker_absent_contract_met"
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
    turn_usage: list[TurnTokenEntry] = field(default_factory=list)
    assistant_messages: list[str] = field(default_factory=list)
    tool_uses: list[dict[str, Any]] = field(default_factory=list)
    jsonl_context_exhausted: bool = False
    stop_reasons: list[str] = field(default_factory=list)
    has_thinking_only_turn: bool = False
    seen_block_types: frozenset[str] = field(default_factory=frozenset)
    api_retry_count: int = 0
    api_retry_last_error: str = ""
    api_retry_last_status: int | None = None
    api_retry_exhausted: bool = False
    api_error_status: int | None = None
    rate_limit_status: str = ""
    rate_limit_type: str = ""
    rate_limit_resets_at_epoch: int | None = None
    terminal_reason: str = ""
    provider_error_code: str = ""
    api_error_message_seen: bool = False
    seen_ndjson_unknown_event_count: int = 0
    seen_ndjson_unknown_item_count: int = 0
    denied_tool_use_ids: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.result, str):
            if isinstance(self.result, list):
                parts: list[str] = []
                for b in self.result:
                    if not isinstance(b, dict):
                        parts.append(str(b))
                        continue
                    block_type = ClaudeContentBlockType.from_api(b.get("type", ""))
                    if block_type == ClaudeContentBlockType.TEXT:
                        parts.append(b.get("text", ""))
                self.result = "\n".join(parts)
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
        codex = CODEX_CONTEXT_EXHAUSTION_MARKER
        return (
            any(marker in e.lower() or codex in e.lower() for e in self.errors)
            or codex in self.result.lower()
            or (
                self.subtype in (CliSubtype.SUCCESS, CliSubtype.ERROR_MAX_TURNS)
                and marker in self.result.lower()
            )
            or any(codex in m.lower() for m in self.assistant_messages)
        )

    def _has_api_error(self) -> bool:
        """True when the session encountered an API infrastructure error."""
        from autoskillit.execution.session._exit_classification import _KNOWN_API_ERROR_PATTERNS

        searchable = "\n".join(self.assistant_messages)
        if self.errors:
            searchable += "\n" + "\n".join(self.errors)
        if self.result:
            searchable += "\n" + self.result
        return any(p.search(searchable) for p in _KNOWN_API_ERROR_PATTERNS)

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
        return self.subtype == CliSubtype.ERROR_MAX_TURNS or self._is_context_exhausted()

    @property
    def retry_reason(self) -> RetryReason:
        """Why retry is needed. NONE if needs_retry is False."""
        if self.needs_retry:
            return RetryReason.RESUME
        return RetryReason.NONE

    def normalize_subtype(
        self,
        outcome: SessionOutcome,
        completion_marker: str,
        prior_completion_markers: Sequence[str] | None = None,
    ) -> str:
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
                    if not (
                        prior_completion_markers
                        and any(pm and pm in self.result for pm in prior_completion_markers)
                    ):
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


def _is_parent_assistant_record(obj: dict[str, Any]) -> bool:
    """Return whether a record is a real parent assistant observation."""
    if obj.get("type") != "assistant" or obj.get("subagent_type"):
        return False
    message = obj.get("message")
    return not (isinstance(message, dict) and message.get("model") == "<synthetic>")


def _nonempty_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _usage_counter(usage: dict[str, Any], api_field: str, canonical_field: str) -> int | None:
    api_value = valid_token_count(usage.get(api_field))
    if api_value is not None:
        return api_value
    return valid_token_count(usage.get(canonical_field))


def extract_token_usage(stdout: str) -> tuple[dict[str, Any] | None, list[TurnTokenEntry]]:
    """Extract token usage from Claude CLI NDJSON output.

    Takes raw stdout (not ClaudeSessionResult) — called during parse_session_result
    construction before the object exists. Returns aggregates and deduplicated rows.
    """
    if not stdout.strip():
        return None, []

    result_usage: dict[str, int] | None = None
    candidate_rows: list[TurnTokenEntry] = []
    model_windows: dict[str, set[int]] = {}

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
        if _is_parent_assistant_record(obj):
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            counters = {
                canon_f: _usage_counter(usage, api_f, canon_f)
                for api_f, canon_f in zip(_API_TOKEN_FIELDS, _CANONICAL_TOKEN_FIELDS)
            }
            if all(value is None for value in counters.values()):
                continue
            candidate_rows.append(
                build_turn_token_entry(
                    backend=AGENT_BACKEND_CLAUDE_CODE,
                    message_id=_nonempty_string(msg.get("id")),
                    request_id=_nonempty_string(obj.get("requestId")),
                    timestamp=_nonempty_string(obj.get("timestamp")),
                    model=_nonempty_string(msg.get("model")),
                    input_tokens=counters["input_tokens"],
                    output_tokens=counters["output_tokens"],
                    cache_read_tokens=counters["cache_read_tokens"],
                    cache_creation_tokens=counters["cache_write_tokens"],
                )
            )
        elif record_type == "result":
            usage = obj.get("usage")
            if isinstance(usage, dict):
                result_usage = {
                    canon_f: _usage_counter(usage, api_f, canon_f) or 0
                    for api_f, canon_f in zip(_API_TOKEN_FIELDS, _CANONICAL_TOKEN_FIELDS)
                }
            model_usage = obj.get("modelUsage")
            if isinstance(model_usage, dict):
                for model, metadata in model_usage.items():
                    if not isinstance(model, str) or not model or not isinstance(metadata, dict):
                        continue
                    window = valid_context_window(metadata.get("contextWindow"))
                    if window is not None:
                        model_windows.setdefault(model, set()).add(window)

    raw_rows = merge_turn_usage(candidate_rows)
    if not raw_rows and result_usage is None:
        return None, []

    model_buckets: dict[str, dict[str, int]] = {}
    peak_context = 0
    turn_usage: list[TurnTokenEntry] = []
    for row in raw_rows:
        model = row["model"]
        bucket = model_buckets.setdefault(
            model or "unknown", {f: 0 for f in _CANONICAL_TOKEN_FIELDS}
        )
        bucket["input_tokens"] += row["input_tokens"] or 0
        bucket["output_tokens"] += row["output_tokens"] or 0
        bucket["cache_read_tokens"] += row["cache_read_tokens"] or 0
        bucket["cache_write_tokens"] += row["cache_creation_tokens"] or 0
        peak_context = max(peak_context, row["cache_read_tokens"] or 0)

        raw_input = row["input_tokens"]
        cache_read = row["cache_read_tokens"]
        cache_creation = row["cache_creation_tokens"]
        inclusive_input = (
            raw_input + cache_read + cache_creation
            if raw_input is not None and cache_read is not None and cache_creation is not None
            else None
        )
        windows = model_windows.get(model, set()) if model is not None else set()
        context_window = next(iter(windows)) if len(windows) == 1 else None
        turn_usage.append(
            build_turn_token_entry(
                backend=row["backend"],
                message_id=row["message_id"],
                request_id=row["request_id"],
                timestamp=row["timestamp"],
                model=model,
                input_tokens=inclusive_input,
                output_tokens=row["output_tokens"],
                cache_read_tokens=cache_read,
                cache_creation_tokens=cache_creation,
                context_window_tokens=context_window,
            )
        )

    if result_usage is not None:
        totals = dict(result_usage)
    else:
        totals = {f: 0 for f in _CANONICAL_TOKEN_FIELDS}
        for bucket in model_buckets.values():
            for f in _CANONICAL_TOKEN_FIELDS:
                totals[f] += bucket[f]

    return {
        **totals,
        "model_breakdown": dict(model_buckets) if model_buckets else {},
        "peak_context": peak_context,
        "turn_count": len(raw_rows),
    }, turn_usage


_KNOWN_RESULT_KEYS: frozenset[str] = frozenset(
    {
        "type",
        "subtype",
        "is_error",
        "result",
        "session_id",
        "errors",
        "usage",
        "modelUsage",
        "api_error_status",
        "terminal_reason",
        "terminalReason",
    }
)


def parse_session_result(stdout: str) -> ClaudeSessionResult:
    """Parse Claude Code NDJSON stdout into a typed result."""
    if not stdout.strip():
        return ClaudeSessionResult(
            subtype=CliSubtype.EMPTY_OUTPUT,
            is_error=True,
            result="",
            session_id="",
            errors=[],
        )
    acc = _parse_provider_records(stdout)
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
        subtype = (
            CliSubtype.CONTEXT_EXHAUSTION
            if acc.jsonl_context_exhausted
            else CliSubtype.UNPARSEABLE
        )
        is_error, result_text, session_id, errors = True, stdout, "", []
    token_usage, turn_usage = extract_token_usage(stdout)
    return ClaudeSessionResult(
        subtype=subtype,
        is_error=is_error,
        result=result_text,
        session_id=session_id,
        errors=errors,
        token_usage=token_usage,
        turn_usage=turn_usage,
        assistant_messages=acc.assistant_messages,
        tool_uses=acc.tool_uses,
        jsonl_context_exhausted=acc.jsonl_context_exhausted,
        stop_reasons=acc.stop_reasons,
        has_thinking_only_turn=acc.has_thinking_only_turn,
        seen_block_types=frozenset(acc.seen_block_types),
        api_retry_count=acc.api_retry_count,
        api_retry_last_error=acc.api_retry_last_error,
        api_retry_last_status=acc.api_retry_last_status,
        api_retry_exhausted=acc.api_retry_exhausted,
        api_error_status=acc.api_error_status,
        rate_limit_status=acc.rate_limit_status,
        rate_limit_type=acc.rate_limit_type,
        rate_limit_resets_at_epoch=acc.rate_limit_resets_at_epoch,
        terminal_reason=acc.terminal_reason,
        provider_error_code=acc.provider_error_code,
        api_error_message_seen=acc.api_error_message_seen,
        denied_tool_use_ids=frozenset(acc.denied_tool_use_ids),
    )
