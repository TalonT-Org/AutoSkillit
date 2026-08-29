"""Claude NDJSON provider-parse primitives.

Extracted from ``_exit_classification`` so the parse authority is no longer
absorbed by the classifier file. Co-locating parser surface with the
classification cascade coupled the read vocabulary to the classification
branching without an interface boundary; this module gives that boundary its
own home and inverts the import direction so ``_session_model`` no longer
imports parser primitives from a consumer module.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, assert_never

import regex as re

from autoskillit.core import CONTEXT_EXHAUSTION_MARKER, ClaudeContentBlockType, get_logger

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
    """Scan Claude NDJSON records for transcript and structured provider evidence.

    Field-accumulation rules:

    - ``rate_limit_resets_at_epoch`` keeps the most restrictive value (max) across
      records so a later shorter reset does not erase a longer known block.
    - ``api_error_message_seen`` latches true once observed (OR-accumulate) so an
      earlier API-error-message flag is not lost when a later assistant turn
      records ``false``.
    - ``rate_limit_status`` and ``rate_limit_type`` may still be overwritten by a
      later ``rate_limit_event`` record; downstream classification only trusts
      these fields when accompanied by a retained reset epoch.
    """
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
                        # "rejected" is the most restrictive observation; keep it
                        # sticky so a later "allowed" record does not erase the
                        # rejection (rate_limit_resets_at_epoch is already sticky
                        # via max() — keep status / type consistent with it).
                        if acc.rate_limit_status != "rejected" or raw_limit_status == "rejected":
                            acc.rate_limit_status = raw_limit_status
                        if raw_limit_status == "rejected":
                            acc.api_retry_exhausted = True
                    raw_limit_type = _provider_field(info, "rateLimitType", "rate_limit_type")
                    if isinstance(raw_limit_type, str) and not acc.rate_limit_type:
                        # First non-empty type wins; clearing on a later record
                        # would erase provider-side evidence.
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
                if isinstance(raw_api_error_message_seen, bool) and raw_api_error_message_seen:
                    # Latch: once we see an API-error-message flag, retain it
                    # even if a later assistant record reports false.
                    acc.api_error_message_seen = True
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
