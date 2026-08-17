"""Claude Code stream + result parsers.

Extracted from `claude.py`. This module owns the NDJSON line classifier
(ClaudeStreamParser.parse_line) and the final-result reader
(ClaudeResultParser.parse_result / parse_stdout). The backend file imports
them and exposes them via its public surface.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    CONTEXT_EXHAUSTION_MARKER,
    AgentSessionResult,
    BackendEventKind,
    ClaudeEventData,
    SessionEvent,
    fast_loads,
)
from autoskillit.execution.backends._claude_prompt import _extract_write_artifacts
from autoskillit.execution.process import _marker_is_standalone
from autoskillit.execution.session import parse_session_result


@dataclass(frozen=True, slots=True)
class ClaudeStreamParser:
    completion_marker: str = ""

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

        record_type = obj.get("type", "")

        if record_type in {"task_started", "task_progress", "task_notification", "task_updated"}:
            task_id = obj.get("task_id")
            if not isinstance(task_id, str) or not task_id.strip():
                return SessionEvent(
                    kind=BackendEventKind.IGNORED,
                    is_terminal=False,
                    has_marker=False,
                )
            status: object = obj.get("status")
            if record_type == "task_updated":
                patch = obj.get("patch")
                if not isinstance(patch, dict):
                    return SessionEvent(
                        kind=BackendEventKind.IGNORED,
                        is_terminal=False,
                        has_marker=False,
                    )
                status = patch.get("status")
            active_statuses = {"pending", "running", "paused"}
            terminal_statuses = {"completed", "failed", "stopped", "killed"}
            if record_type in {"task_started", "task_progress"}:
                task_active = True
            elif status in active_statuses:
                task_active = True
            elif status in terminal_statuses:
                task_active = False
            else:
                return SessionEvent(
                    kind=BackendEventKind.IGNORED,
                    is_terminal=False,
                    has_marker=False,
                )
            return SessionEvent(
                kind=BackendEventKind.TASK_LIFECYCLE,
                is_terminal=False,
                has_marker=False,
                task_id=task_id.strip(),
                task_active=task_active,
            )

        if record_type == "system":
            subtype = obj.get("subtype", "")
            session_id = obj.get("session_id", "")
            if subtype == "api_retry":
                return SessionEvent(
                    kind=BackendEventKind.API_RETRY,
                    is_terminal=False,
                    has_marker=False,
                    backend_data=ClaudeEventData(
                        record_type="system",
                        subtype="api_retry",
                        session_id=session_id,
                        raw=obj,
                    ),
                )
            return SessionEvent(
                kind=BackendEventKind.SESSION_META,
                is_terminal=False,
                has_marker=False,
                session_id=session_id if subtype == "init" else None,
            )

        if record_type == "result":
            result_field = obj.get("result", "")
            if not (isinstance(result_field, str) and result_field.strip()):
                return SessionEvent(
                    kind=BackendEventKind.IGNORED,
                    is_terminal=False,
                    has_marker=False,
                )
            has_marker = bool(
                self.completion_marker
                and _marker_is_standalone(result_field, self.completion_marker)
            )
            return SessionEvent(
                kind=BackendEventKind.COMPLETION,
                is_terminal=True,
                has_marker=has_marker,
                backend_data=ClaudeEventData(
                    record_type="result",
                    subtype=obj.get("subtype", ""),
                    session_id=obj.get("session_id", ""),
                    raw=obj,
                ),
            )

        if record_type == "assistant":
            if "message" not in obj and obj.get("output_tokens", -1) == 0:
                flat_content = obj.get("content", [])
                if isinstance(flat_content, list) and any(
                    isinstance(block, dict)
                    and block.get("type") == "text"
                    and CONTEXT_EXHAUSTION_MARKER in block.get("text", "").lower()
                    for block in flat_content
                ):
                    return SessionEvent(
                        kind=BackendEventKind.TOOL_OUTPUT,
                        is_terminal=False,
                        has_marker=False,
                        backend_data=ClaudeEventData(
                            record_type="assistant",
                            subtype="context_exhaustion",
                            session_id="",
                            raw=obj,
                        ),
                    )
            message = obj.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list) and any(
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") == "ScheduleWakeup"
                for block in content
            ):
                return SessionEvent(
                    kind=BackendEventKind.SCHEDULE_WAKEUP,
                    is_terminal=False,
                    has_marker=False,
                )
            return SessionEvent(
                kind=BackendEventKind.IGNORED,
                is_terminal=False,
                has_marker=False,
            )

        return SessionEvent(
            kind=BackendEventKind.IGNORED,
            is_terminal=False,
            has_marker=False,
        )


@dataclass(frozen=True, slots=True)
class ClaudeResultParser:
    def parse_result(self, events: Sequence[SessionEvent]) -> AgentSessionResult:
        session_id: str | None = None
        has_completion = False
        has_marker = False
        last_backend_data: ClaudeEventData | None = None
        for event in events:
            if event.kind == BackendEventKind.SESSION_META and event.session_id:
                session_id = event.session_id
            if event.kind == BackendEventKind.COMPLETION:
                has_completion = True
                if event.has_marker:
                    has_marker = True
                if isinstance(event.backend_data, ClaudeEventData):
                    last_backend_data = event.backend_data
        output = ""
        if last_backend_data and last_backend_data.raw:
            output = last_backend_data.raw.get("result", "")
        success = has_completion and has_marker
        return AgentSessionResult(
            success=success,
            exit_code=0 if success else 1,
            backend_name=AGENT_BACKEND_CLAUDE_CODE,
            elapsed_seconds=0.0,
            session_id=session_id,
            output=output if isinstance(output, str) else "",
        )

    def parse_stdout(self, stdout: str, *, exit_code: int = 0) -> AgentSessionResult:
        result = parse_session_result(stdout)
        write_artifacts = _extract_write_artifacts(result.tool_uses)
        return AgentSessionResult(
            success=result.session_complete,
            exit_code=0 if result.session_complete else 1,
            backend_name=AGENT_BACKEND_CLAUDE_CODE,
            elapsed_seconds=0.0,
            session_id=result.session_id or None,
            output=result.result,
            error="\n".join(result.errors) if result.errors else "",
            raw={
                "subtype": result.subtype.value,
                "is_error": result.is_error,
                "token_usage": result.token_usage,
                "write_artifacts": write_artifacts,
                "tool_uses": result.tool_uses,
                "assistant_messages": result.assistant_messages,
                "jsonl_context_exhausted": result.jsonl_context_exhausted,
                "stop_reasons": result.stop_reasons,
                "has_thinking_only_turn": result.has_thinking_only_turn,
                "seen_block_types": list(result.seen_block_types),
            },
        )


__all__ = ["ClaudeResultParser", "ClaudeStreamParser"]
