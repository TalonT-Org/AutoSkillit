"""Claude Code stdout parser and provider-evidence retention."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, assert_never

from autoskillit.core import (
    CONTEXT_EXHAUSTION_MARKER,
    ClaudeContentBlockType,
    CliSubtype,
    get_logger,
)

from ._session_model import (
    _ABS_PATH_RE,
    _HANDLED_RECORD_TYPES,
    _KNOWN_RESULT_KEYS,
    ClaudeSessionResult,
    extract_token_usage,
)

logger = get_logger(__name__)


def _provider_field(obj: Mapping[str, Any], *names: str) -> Any:
    """Return the first retained provider field across its observed spellings."""
    for name in names:
        if name in obj:
            return obj[name]
    return None


@dataclass
class _ParseAccumulator:
    """Mutable accumulator for a complete NDJSON scan."""

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
                        acc.rate_limit_resets_at_epoch = raw_resets_at
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
                msg = obj.get("message")
                if isinstance(msg, dict):
                    content = msg.get("content", "")
                    if isinstance(content, list):
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
                                                match.group(1)
                                                for match in _ABS_PATH_RE.finditer(command)
                                                if len(match.group(1)) >= 5
                                            ]
                                            if paths:
                                                entry["bash_paths"] = paths
                                    acc.tool_uses.append(entry)
                                case (
                                    ClaudeContentBlockType.THINKING
                                    | ClaudeContentBlockType.REDACTED_THINKING
                                ):
                                    turn_has_thinking = True
                                case (
                                    ClaudeContentBlockType.TOOL_RESULT
                                    | ClaudeContentBlockType.IMAGE
                                ):
                                    pass
                                case ClaudeContentBlockType.UNKNOWN:
                                    raw_type = block.get("type", "")
                                    logger.debug("unknown_content_block_type", block_type=raw_type)
                                    acc.seen_block_types.add(raw_type)
                                case _ as unreachable:
                                    assert_never(unreachable)
                        text = "\n".join(text_parts).strip()
                        if turn_has_thinking and not text_parts and not turn_has_tool_use:
                            acc.has_thinking_only_turn = True
                    else:
                        text = str(content).strip()
                    if text:
                        acc.assistant_messages.append(text)
                    stop_reason = msg.get("stop_reason", "")
                    if stop_reason:
                        acc.stop_reasons.append(str(stop_reason))
                elif "message" not in obj and obj.get("output_tokens", -1) == 0:
                    flat_content = obj.get("content", [])
                    if isinstance(flat_content, list) and any(
                        isinstance(block, dict)
                        and block.get("type") == "text"
                        and marker in block.get("text", "").lower()
                        for block in flat_content
                    ):
                        acc.jsonl_context_exhausted = True
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
        token_usage=extract_token_usage(stdout),
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
