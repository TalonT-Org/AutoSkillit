"""NDJSON stream/result parsing for the Codex backend."""

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
    CodexEventData,
    CodexEventType,
    CodexItemType,
    SessionEvent,
    fast_loads,
    get_logger,
)
from autoskillit.execution.process import _marker_is_standalone

logger = get_logger(__name__)


@dataclass
class _CodexParseAccumulator:
    session_id: str = ""
    agent_messages: list[str] = field(default_factory=list)
    command_executions: list[dict[str, Any]] = field(default_factory=list)
    mcp_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    file_changes: list[str] = field(default_factory=list)
    last_usage: dict[str, Any] | None = None
    saw_failure: bool = False
    success: bool = False
    error_message: str = ""
    error_code: str = ""


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
        event_type = CodexEventType.from_ndjson(obj.get("type", ""))
        if event_type == CodexEventType.UNKNOWN:
            logger.warning("codex_ndjson_unknown_event_type", type=obj.get("type", ""))
            continue
        if event_type == CodexEventType.THREAD_STARTED:
            acc.session_id = obj.get("thread_id", "")
        elif event_type in (CodexEventType.TURN_STARTED, CodexEventType.ITEM_STARTED):
            continue
        elif event_type == CodexEventType.ITEM_COMPLETED:
            item = obj.get("item", {})
            if not isinstance(item, dict):
                continue
            item_type = CodexItemType.from_ndjson(item.get("type", ""))
            if item_type == CodexItemType.AGENT_MESSAGE:
                text = item.get("text", "")
                if text:
                    acc.agent_messages.append(text)
            elif item_type == CodexItemType.MESSAGE:
                for block in item.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        if text:
                            acc.agent_messages.append(text)
            elif item_type in (CodexItemType.COMMAND_EXECUTION, CodexItemType.FUNCTION_CALL):
                acc.command_executions.append(item)
            elif item_type == CodexItemType.MCP_TOOL_CALL:
                acc.mcp_tool_calls.append(item)
            elif item_type == CodexItemType.FILE_CHANGE:
                changes = item.get("changes", [])
                if changes and isinstance(changes, list):
                    for change in changes:
                        if isinstance(change, dict):
                            if path := change.get("path"):
                                acc.file_changes.append(path)
                else:
                    if path := item.get("path"):
                        acc.file_changes.append(path)
            elif item_type in (CodexItemType.COLLAB_TOOL_CALL, CodexItemType.WEB_SEARCH):
                acc.command_executions.append(item)
            elif item_type in (CodexItemType.REASONING, CodexItemType.TODO_LIST):
                logger.debug("codex_ndjson_informational_item", item_type=item_type.value)
                continue
            elif item_type == CodexItemType.UNKNOWN:
                logger.warning("codex_ndjson_unknown_item_type", item_type=item.get("type", ""))
                continue
        elif event_type == CodexEventType.TURN_COMPLETED:
            usage = obj.get("usage")
            if isinstance(usage, dict):
                acc.last_usage = usage
            if not acc.saw_failure:
                acc.success = True
        elif event_type == CodexEventType.TURN_FAILED:
            error = obj.get("error", {})
            if isinstance(error, dict):
                error_msg = error.get("message", "")
                error_code = error.get("code", "")
                acc.error_code = error_code
                if error_code and error_code not in error_msg:
                    acc.error_message = f"{error_msg} [{error_code}]" if error_msg else error_code
                else:
                    acc.error_message = error_msg
            else:
                acc.error_message = str(error) if error else ""
            acc.saw_failure = True
            acc.success = False
    return acc


@dataclass(frozen=True, slots=True)
class CodexResultParser:
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
            exit_code=0 if not is_error else (exit_code or 1),
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
                "error_code": acc.error_code,
            },
        )


@dataclass(slots=True)
class CodexStreamParser:
    """Stateful NDJSON stream parser for Codex CLI output.

    One instance per session — accumulates marker detection state across
    parse_line() calls. Not reusable across sessions.
    """

    completion_marker: str = ""
    _saw_marker: bool = field(default=False, init=False, repr=False)

    def _check_marker_text(self, text: str) -> None:
        if self.completion_marker and _marker_is_standalone(text, self.completion_marker):
            self._saw_marker = True

    def parse_line(self, line: str) -> SessionEvent | None:
        line = line.strip()
        if not line:
            return None
        try:
            obj = fast_loads(line)
        except (ValueError, TypeError):
            return None
        if not isinstance(obj, dict):
            return None

        event_type = CodexEventType.from_ndjson(obj.get("type", ""))

        if event_type == CodexEventType.THREAD_STARTED:
            return SessionEvent(
                kind=BackendEventKind.SESSION_META,
                is_terminal=False,
                has_marker=False,
                session_id=obj.get("thread_id", "") or None,
            )

        if event_type in (CodexEventType.TURN_STARTED, CodexEventType.ITEM_STARTED):
            return SessionEvent(
                kind=BackendEventKind.IGNORED,
                is_terminal=False,
                has_marker=False,
            )

        if event_type == CodexEventType.ITEM_COMPLETED:
            item = obj.get("item", {})
            if not isinstance(item, dict):
                return SessionEvent(
                    kind=BackendEventKind.IGNORED,
                    is_terminal=False,
                    has_marker=False,
                )
            item_type = CodexItemType.from_ndjson(item.get("type", ""))

            if item_type == CodexItemType.AGENT_MESSAGE:
                self._check_marker_text(item.get("text", ""))
                return SessionEvent(
                    kind=BackendEventKind.TOOL_OUTPUT,
                    is_terminal=False,
                    has_marker=False,
                    backend_data=CodexEventData(
                        record_type="item.completed",
                        thread_id="",
                        item_type="agent_message",
                        raw=obj,
                    ),
                )

            if item_type == CodexItemType.MESSAGE:
                for block in item.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        self._check_marker_text(block.get("text", ""))
                return SessionEvent(
                    kind=BackendEventKind.TOOL_OUTPUT,
                    is_terminal=False,
                    has_marker=False,
                    backend_data=CodexEventData(
                        record_type="item.completed",
                        thread_id="",
                        item_type="message",
                        raw=obj,
                    ),
                )

            if item_type in (
                CodexItemType.FILE_CHANGE,
                CodexItemType.COMMAND_EXECUTION,
                CodexItemType.FUNCTION_CALL,
                CodexItemType.MCP_TOOL_CALL,
                CodexItemType.COLLAB_TOOL_CALL,
                CodexItemType.WEB_SEARCH,
            ):
                return SessionEvent(
                    kind=BackendEventKind.TOOL_OUTPUT,
                    is_terminal=False,
                    has_marker=False,
                    backend_data=CodexEventData(
                        record_type="item.completed",
                        thread_id="",
                        item_type=item_type.value,
                        raw=obj,
                    ),
                )

            if item_type in (CodexItemType.REASONING, CodexItemType.TODO_LIST):
                return SessionEvent(
                    kind=BackendEventKind.IGNORED,
                    is_terminal=False,
                    has_marker=False,
                )

            logger.warning("codex_ndjson_unknown_item_type", item_type=item.get("type", ""))
            return SessionEvent(
                kind=BackendEventKind.IGNORED,
                is_terminal=False,
                has_marker=False,
            )

        if event_type == CodexEventType.TURN_COMPLETED:
            return SessionEvent(
                kind=BackendEventKind.COMPLETION,
                is_terminal=True,
                has_marker=self._saw_marker,
                backend_data=CodexEventData(
                    record_type="turn.completed",
                    thread_id="",
                    item_type="",
                    raw=obj,
                    usage=obj.get("usage"),
                ),
            )

        if event_type == CodexEventType.TURN_FAILED:
            return SessionEvent(
                kind=BackendEventKind.COMPLETION,
                is_terminal=True,
                has_marker=False,
                backend_data=CodexEventData(
                    record_type="turn.failed",
                    thread_id="",
                    item_type="",
                    raw=obj,
                ),
            )

        if event_type == CodexEventType.ERROR:
            return SessionEvent(
                kind=BackendEventKind.ERROR,
                is_terminal=True,
                has_marker=False,
                backend_data=CodexEventData(
                    record_type="error",
                    thread_id="",
                    item_type="",
                    raw=obj,
                ),
            )

        logger.warning("codex_ndjson_unknown_event_type", type=obj.get("type", ""))
        return SessionEvent(
            kind=BackendEventKind.IGNORED,
            is_terminal=False,
            has_marker=False,
        )
