"""Infrastructure exit classification for headless sessions."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, assert_never

import regex as re

from autoskillit.core import (
    CODEX_CONTEXT_EXHAUSTION_MARKER,
    CONTEXT_EXHAUSTION_MARKER,
    ClaudeContentBlockType,
    InfraExitCategory,
    get_logger,
)

if TYPE_CHECKING:
    from autoskillit.core import BackendCapabilities, SubprocessResult
    from autoskillit.execution.session._session_model import ClaudeSessionResult

logger = get_logger(__name__)

_ABS_PATH_RE: re.Pattern[str] = re.compile(r'(?:^|[\s="\'])(/(?:[a-zA-Z0-9._/~@+-]+))')
_HANDLED_RECORD_TYPES: frozenset[str] = frozenset(
    {"assistant", "rate_limit_event", "result", "system", "user"}
)


def _provider_field(obj: Mapping[str, Any], *names: str) -> Any:
    """Return the first retained provider field across its observed spellings."""
    for name in names:
        if name in obj:
            return obj[name]
    return None


@dataclass
class _ProviderParseAccumulator:
    """Provider and transcript evidence retained during one stdout scan."""

    result_obj: dict[str, Any] | None = None
    tool_uses: list[dict[str, Any]] = field(default_factory=list)
    assistant_messages: list[str] = field(default_factory=list)
    jsonl_context_exhausted: bool = False
    stop_reasons: list[str] = field(default_factory=list)
    seen_block_types: set[str] = field(default_factory=set)
    has_thinking_only_turn: bool = False
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
    denied_tool_use_ids: set[str] = field(default_factory=set)


def _parse_provider_records(stdout: str) -> _ProviderParseAccumulator:
    """Scan Claude NDJSON records for transcript and structured provider evidence."""
    acc = _ProviderParseAccumulator()
    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                continue
            record_type = obj.get("type")
            if record_type is None:
                raw_error = _provider_field(obj, "error")
                if isinstance(raw_error, str):
                    acc.provider_error_code = raw_error
                continue
            if not isinstance(record_type, str) or record_type not in _HANDLED_RECORD_TYPES:
                continue
            if record_type == "system" and obj.get("subtype") == "api_retry":
                acc.api_retry_count += 1
                acc.api_retry_last_error = str(_provider_field(obj, "error") or "")
                raw_status = obj.get("error_status")
                acc.api_retry_last_status = raw_status if isinstance(raw_status, int) else None
                attempt = obj.get("attempt", 0)
                max_retries = obj.get("max_retries", 0)
                if (
                    isinstance(attempt, int)
                    and isinstance(max_retries, int)
                    and attempt >= max_retries
                    and max_retries > 0
                ):
                    acc.api_retry_exhausted = True
                continue
            if record_type == "rate_limit_event":
                info = _provider_field(obj, "rate_limit_info") or obj
                if isinstance(info, Mapping):
                    raw_limit_status = _provider_field(info, "status")
                    if isinstance(raw_limit_status, str):
                        acc.rate_limit_status = raw_limit_status
                        if raw_limit_status == "rejected":
                            acc.api_retry_exhausted = True
                    raw_limit_type = _provider_field(info, "rateLimitType", "rate_limit_type")
                    if isinstance(raw_limit_type, str):
                        acc.rate_limit_type = raw_limit_type
                    raw_resets_at = _provider_field(info, "resetsAt", "resets_at")
                    if isinstance(raw_resets_at, int):
                        # Preserve the most restrictive reset observed across
                        # multiple rate_limit_event records; a later event with
                        # an earlier reset would otherwise overwrite the
                        # longest-known block.
                        acc.rate_limit_resets_at_epoch = (
                            raw_resets_at
                            if acc.rate_limit_resets_at_epoch is None
                            else max(acc.rate_limit_resets_at_epoch, raw_resets_at)
                        )
                continue
            if record_type == "result":
                acc.result_obj = obj
                raw_status = _provider_field(obj, "api_error_status")
                if isinstance(raw_status, int):
                    acc.api_error_status = raw_status
                raw_terminal_reason = _provider_field(obj, "terminal_reason", "terminalReason")
                if isinstance(raw_terminal_reason, str):
                    acc.terminal_reason = raw_terminal_reason
            elif record_type == "assistant" and not obj.get("subagent_type"):
                raw_api_error_message_seen = _provider_field(
                    obj, "is_api_error_message", "isApiErrorMessage"
                )
                if isinstance(raw_api_error_message_seen, bool):
                    acc.api_error_message_seen = raw_api_error_message_seen
                _capture_assistant_record(obj, acc)
            elif record_type == "user" and not obj.get("subagent_type"):
                content = obj.get("message", {}).get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if (
                            isinstance(block, dict)
                            and block.get("type") == "tool_result"
                            and block.get("is_error") is True
                        ):
                            tool_use_id = block.get("tool_use_id", "")
                            if tool_use_id:
                                acc.denied_tool_use_ids.add(tool_use_id)
        except json.JSONDecodeError:
            continue

    if acc.result_obj is None:
        try:
            fallback = json.loads(stdout)
            if isinstance(fallback, dict) and fallback.get("type") == "result":
                acc.result_obj = fallback
                raw_status = _provider_field(fallback, "api_error_status")
                if isinstance(raw_status, int):
                    acc.api_error_status = raw_status
                raw_terminal_reason = _provider_field(
                    fallback, "terminal_reason", "terminalReason"
                )
                if isinstance(raw_terminal_reason, str):
                    acc.terminal_reason = raw_terminal_reason
        except json.JSONDecodeError:
            pass
    return acc


def _capture_assistant_record(obj: dict[str, Any], acc: _ProviderParseAccumulator) -> None:
    """Retain message text, tool uses, and context evidence from an assistant record."""
    message = obj.get("message")
    if not isinstance(message, dict):
        if "message" not in obj and obj.get("output_tokens", -1) == 0:
            flat_content = obj.get("content", [])
            if isinstance(flat_content, list) and any(
                isinstance(block, dict)
                and block.get("type") == "text"
                and CONTEXT_EXHAUSTION_MARKER in block.get("text", "").lower()
                for block in flat_content
            ):
                acc.jsonl_context_exhausted = True
        return

    content = message.get("content", "")
    if not isinstance(content, list):
        text = str(content).strip()
        if text:
            acc.assistant_messages.append(text)
        stop_reason = message.get("stop_reason", "")
        if stop_reason:
            acc.stop_reasons.append(str(stop_reason))
        return

    text_parts: list[str] = []
    turn_has_thinking = False
    turn_has_tool_use = False
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = ClaudeContentBlockType.from_api(block.get("type", ""))
        match block_type:
            case ClaudeContentBlockType.TEXT:
                text_parts.append(block.get("text", ""))
            case ClaudeContentBlockType.TOOL_USE:
                turn_has_tool_use = True
                _capture_tool_use(block, acc)
            case ClaudeContentBlockType.THINKING | ClaudeContentBlockType.REDACTED_THINKING:
                turn_has_thinking = True
            case ClaudeContentBlockType.TOOL_RESULT | ClaudeContentBlockType.IMAGE:
                pass
            case ClaudeContentBlockType.UNKNOWN:
                raw_type = block.get("type", "")
                logger.debug("unknown_content_block_type", block_type=raw_type)
                acc.seen_block_types.add(raw_type)
            case _ as unreachable:
                assert_never(unreachable)
    text = "\n".join(text_parts).strip()
    if text:
        acc.assistant_messages.append(text)
    if turn_has_thinking and not text_parts and not turn_has_tool_use:
        acc.has_thinking_only_turn = True
    stop_reason = message.get("stop_reason", "")
    if stop_reason:
        acc.stop_reasons.append(str(stop_reason))


def _capture_tool_use(block: dict[str, Any], acc: _ProviderParseAccumulator) -> None:
    """Record a tool-use evidence entry without retaining arbitrary tool input."""
    name = block.get("name", "")
    entry: dict[str, str | list[str]] = {"name": name, "id": block.get("id", "")}
    tool_input = block.get("input")
    if name in {"Write", "Edit"} and isinstance(tool_input, dict):
        file_path = tool_input.get("file_path", "")
        if file_path:
            entry["file_path"] = file_path
    elif name == "Bash" and isinstance(tool_input, dict):
        command = tool_input.get("command", "")
        if isinstance(command, str):
            paths = [
                match.group(1)
                for match in _ABS_PATH_RE.finditer(command)
                if len(match.group(1)) >= 5
            ]
            if paths:
                entry["bash_paths"] = paths
    acc.tool_uses.append(entry)


_API_ERROR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"overloaded", re.IGNORECASE),
    re.compile(r"\b529\b"),
    re.compile(r"\b503\b"),
    re.compile(r"ECONNRESET", re.IGNORECASE),
    re.compile(r"ECONNREFUSED", re.IGNORECASE),
    re.compile(r"socket hang up", re.IGNORECASE),
    re.compile(r"network error", re.IGNORECASE),
    re.compile(r"connection reset", re.IGNORECASE),
    re.compile(r"socket connection was closed", re.IGNORECASE),  # Bun HTTP client
    re.compile(r"rate[\s_\-]limited", re.IGNORECASE),
)

_CODEX_API_ERROR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"rate_limit_exceeded", re.IGNORECASE),
    re.compile(r"\bserver_error\b", re.IGNORECASE),
    re.compile(r"insufficient_quota", re.IGNORECASE),
    re.compile(r"model_not_found", re.IGNORECASE),
)
_CODEX_ERROR_CODE_API_STATUS: dict[str, int] = {
    "rate_limit_exceeded": 429,
    "server_error": 500,
    "insufficient_quota": 429,
    "model_not_found": 404,
}

_KNOWN_API_ERROR_PATTERNS: tuple[re.Pattern[str], ...] = (
    _API_ERROR_PATTERNS + _CODEX_API_ERROR_PATTERNS
)

# Additive subset of _KNOWN_API_ERROR_PATTERNS covering transient rate-limit signals.
# Must NOT remove these patterns from _API_ERROR_PATTERNS or _CODEX_API_ERROR_PATTERNS —
# _session_model._has_api_error() imports _KNOWN_API_ERROR_PATTERNS and relies on the
# full union. This tuple exists only for the RATE_LIMITED classification branch.
_RATE_LIMIT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"rate[\s_\-]limited", re.IGNORECASE),
    re.compile(r"rate_limit_exceeded", re.IGNORECASE),
)

_CODEX_CONTEXT_EXHAUSTION_PATTERN: re.Pattern[str] = re.compile(
    CODEX_CONTEXT_EXHAUSTION_MARKER, re.IGNORECASE
)
_MODEL_CAPACITY_ERROR: str = "Selected model is at capacity. Please try a different model."
_RETRIABLE_API_STATUSES: frozenset[int] = frozenset({408, 409})


def _has_model_capacity_error(
    session: ClaudeSessionResult,
    result: SubprocessResult,
    capabilities: BackendCapabilities,
) -> bool:
    """Match exact provider capacity evidence from backend-owned error channels."""
    if not capabilities.supports_model_capacity_error_detection:
        return False
    expected = _MODEL_CAPACITY_ERROR.casefold()
    return any(error.strip().casefold() == expected for error in session.errors) or any(
        line.strip().casefold() == expected for line in result.stderr.splitlines()
    )


def _all_text_sources(
    session: ClaudeSessionResult,
    result: SubprocessResult,
) -> list[str]:
    """Collect all searchable text from a session and subprocess result."""
    sources: list[str] = list(session.assistant_messages)
    if session.errors:
        sources.extend(session.errors)
    if session.result:
        sources.append(session.result)
    if result.stderr:
        sources.append(result.stderr)
    return sources


def has_rate_limit_signal(
    session: ClaudeSessionResult,
    result: SubprocessResult,
) -> bool:
    """Check whether a session exhibits rate-limit signals (HTTP 429 or text patterns)."""
    if session.api_error_status == 429:
        return True
    return any(
        p.search(msg) for p in _RATE_LIMIT_PATTERNS for msg in _all_text_sources(session, result)
    )


def classify_api_status(status: int) -> InfraExitCategory:
    """Classify a provider HTTP status with an explicit retriable allowlist."""
    if status == 429:
        return InfraExitCategory.RATE_LIMITED
    if status in _RETRIABLE_API_STATUSES or status >= 500:
        return InfraExitCategory.API_ERROR
    return InfraExitCategory.API_ERROR_TERMINAL


_SHELL_SIGNAL_MAX = 192  # 128 + SIGRTMAX (64 on Linux)


def is_signal_death_code(returncode: int) -> bool:
    """Return True if returncode indicates death by signal.

    Covers both Python convention (negative: -(signal_number)) and shell
    convention (positive: 128 + signal_number, range 129–192).
    """
    return returncode < 0 or (128 < returncode <= _SHELL_SIGNAL_MAX)


def classify_infra_exit(
    session: ClaudeSessionResult,
    result: SubprocessResult,
    *,
    capabilities: BackendCapabilities,
) -> InfraExitCategory:
    """Classify why a headless session exited at the infrastructure level.

    Context exhaustion takes precedence. Structured provider status and error-code
    evidence follow, then rate-limit and API text patterns, process death, and the
    failed-session tail. This keeps terminal numeric evidence from being shadowed by
    broad substring matches.

    Context-exhaustion detection is gated by ``capabilities.supports_context_exhaustion_detection``
    so backends that do not implement it fall through to downstream classification.
    """
    if capabilities.supports_context_exhaustion_detection:
        if session._is_context_exhausted():
            return InfraExitCategory.CONTEXT_EXHAUSTED
        if _CODEX_CONTEXT_EXHAUSTION_PATTERN.search(result.stderr):
            return InfraExitCategory.CONTEXT_EXHAUSTED
    if session.api_error_status is not None and session.api_error_status >= 400:
        return classify_api_status(session.api_error_status)
    if session.provider_error_code:
        return InfraExitCategory.API_ERROR_TERMINAL
    # Rate limit text remains useful when structured provider evidence is absent.
    if any(
        p.search(msg) for p in _RATE_LIMIT_PATTERNS for msg in _all_text_sources(session, result)
    ):
        return InfraExitCategory.RATE_LIMITED
    if _has_model_capacity_error(session, result, capabilities):
        return InfraExitCategory.API_ERROR
    if session._has_api_error() or any(p.search(result.stderr) for p in _KNOWN_API_ERROR_PATTERNS):
        return InfraExitCategory.API_ERROR
    # Separate guard: _has_api_error() scans message text for known patterns, but
    # api_retry_last_error can be "unknown" or another value not in _KNOWN_API_ERROR_PATTERNS.
    # In that case _has_api_error() returns False while api_retry_exhausted is still True.
    if session.api_retry_exhausted:
        return InfraExitCategory.API_ERROR
    if result.returncode is not None and is_signal_death_code(result.returncode):
        return InfraExitCategory.PROCESS_KILLED
    if session.is_error:
        return InfraExitCategory.UNCLASSIFIED
    return InfraExitCategory.COMPLETED
