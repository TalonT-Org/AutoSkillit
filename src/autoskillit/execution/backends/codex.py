"""Codex/OpenAI backend result parser."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from autoskillit.core import (
    AGENT_BACKEND_CODEX,
    AgentSessionResult,
    BackendEventKind,
    CanonicalTokenUsage,
    CliSubtype,
    SessionEvent,
)


@dataclass
class _CodexParseAccumulator:
    session_id: str = ""
    agent_messages: list[str] = field(default_factory=list)
    command_executions: list[dict[str, Any]] = field(default_factory=list)
    mcp_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    file_changes: list[str] = field(default_factory=list)
    last_usage: dict[str, Any] | None = None
    success: bool = False
    error_message: str = ""


def _scan_codex_ndjson(stdout: str) -> _CodexParseAccumulator:
    if not stdout.strip():
        return _CodexParseAccumulator()
    acc = _CodexParseAccumulator()
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
        event_type = obj.get("type", "")
        if event_type == "thread.started":
            acc.session_id = obj.get("thread_id", "")
        elif event_type == "item.completed":
            item = obj.get("item", {})
            if not isinstance(item, dict):
                continue
            item_type = item.get("type", "")
            if item_type == "message":
                for block in item.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        if text:
                            acc.agent_messages.append(text)
            elif item_type == "function_call":
                acc.command_executions.append(item)
            elif item_type == "mcp_tool_call":
                acc.mcp_tool_calls.append(item)
            elif item_type == "file_change":
                path = item.get("path")
                if path:
                    acc.file_changes.append(path)
        elif event_type == "turn.completed":
            usage = obj.get("usage")
            if isinstance(usage, dict):
                acc.last_usage = usage
            acc.success = True
        elif event_type == "turn.failed":
            error = obj.get("error", {})
            if isinstance(error, dict):
                acc.error_message = error.get("message", "")
            else:
                acc.error_message = str(error) if error else ""
            acc.success = False
    return acc


@dataclass(frozen=True, slots=True)
class CodexResultParser:
    """Codex/OpenAI backend result parser conforming to ResultParser protocol."""

    def parse_result(self, events: Sequence[SessionEvent]) -> AgentSessionResult:
        if not events:
            return AgentSessionResult(
                success=False,
                exit_code=1,
                backend_name=AGENT_BACKEND_CODEX,
                elapsed_seconds=0.0,
                error="empty events sequence",
            )
        session_id: str | None = None
        has_completion = False
        for event in events:
            if event.kind == BackendEventKind.SESSION_META and event.session_id:
                session_id = event.session_id
            if event.kind == BackendEventKind.COMPLETION:
                has_completion = True
        return AgentSessionResult(
            success=has_completion,
            exit_code=0 if has_completion else 1,
            backend_name=AGENT_BACKEND_CODEX,
            elapsed_seconds=0.0,
            session_id=session_id,
        )

    def parse_stdout(self, stdout: str, *, exit_code: int = 0) -> AgentSessionResult:
        acc = _scan_codex_ndjson(stdout)
        if acc.success:
            subtype = CliSubtype.SUCCESS.value
        elif acc.error_message:
            subtype = CliSubtype.ERROR_DURING_EXECUTION.value
        elif not stdout.strip():
            subtype = CliSubtype.EMPTY_OUTPUT.value
        else:
            subtype = CliSubtype.UNPARSEABLE.value
        is_error = subtype != CliSubtype.SUCCESS.value
        canonical_dict = None
        if acc.last_usage is not None:
            canonical = CanonicalTokenUsage.from_codex_dict(acc.last_usage)
            canonical_dict = canonical.to_dict()
        return AgentSessionResult(
            success=not is_error,
            exit_code=exit_code if not is_error else (exit_code or 1),
            backend_name=AGENT_BACKEND_CODEX,
            elapsed_seconds=0.0,
            session_id=acc.session_id or None,
            output="\n".join(acc.agent_messages),
            error=acc.error_message,
            raw={
                "subtype": subtype,
                "is_error": is_error,
                "token_usage": acc.last_usage,
                "canonical_token_usage": canonical_dict,
                "agent_messages": acc.agent_messages,
                "command_executions": acc.command_executions,
                "mcp_tool_calls": acc.mcp_tool_calls,
                "file_changes": acc.file_changes,
            },
        )
