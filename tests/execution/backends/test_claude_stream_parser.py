from __future__ import annotations

import json
from typing import Any

import pytest

from autoskillit.core import (
    BackendEventKind,
    ChildAttemptState,
    ChildLifecycleObservation,
    ClaudeEventData,
    LifecycleEvidenceIssue,
    LifecycleEvidenceIssueKind,
    LifecycleEvidenceResolution,
    ParentAssistantMarker,
    StreamParser,
)
from autoskillit.execution.backends import ClaudeStreamParser

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _result_line(*, result: str = "done", subtype: str = "success", session_id: str = "s1") -> str:
    return json.dumps(
        {"type": "result", "result": result, "subtype": subtype, "session_id": session_id}
    )


def _system_line(*, session_id: str = "test-session") -> str:
    return json.dumps({"type": "system", "session_id": session_id})


def _assistant_line(**overrides: Any) -> str:
    base: dict[str, Any] = {"type": "assistant", "message": {"content": "hello"}}
    base.update(overrides)
    return json.dumps(base)


def _context_exhaustion_line() -> str:
    return json.dumps(
        {
            "type": "assistant",
            "output_tokens": 0,
            "content": [{"type": "text", "text": "prompt is too long"}],
        }
    )


class TestClaudeStreamParser:
    def test_parse_line_system_api_retry_returns_api_retry_kind(self) -> None:
        parser = ClaudeStreamParser()
        line = json.dumps(
            {
                "type": "system",
                "subtype": "api_retry",
                "error": "unknown",
                "error_status": None,
                "attempt": 5,
                "max_retries": 10,
            }
        )
        result = parser.parse_line(line)
        assert result is not None
        assert result.kind == BackendEventKind.API_RETRY
        assert result.is_terminal is False
        assert result.backend_data is not None
        assert result.backend_data.record_type == "system"
        assert result.backend_data.subtype == "api_retry"
        assert result.backend_data.raw["error"] == "unknown"
        assert result.backend_data.raw["attempt"] == 5

    def test_parse_line_system_non_init_subtype_no_session_id(self) -> None:
        # Regression guard: non-api_retry subtypes must not be misrouted to API_RETRY,
        # but non-init subtypes must not yield a session_id (they carry process UUIDs).
        parser = ClaudeStreamParser()
        line = json.dumps({"type": "system", "subtype": "other_subtype", "session_id": "s1"})
        result = parser.parse_line(line)
        assert result is not None
        assert result.kind == BackendEventKind.SESSION_META
        assert result.session_id is None
        assert result.is_terminal is False

    def test_parse_line_system_record_session_meta(self) -> None:
        parser = ClaudeStreamParser()
        line = '{"type": "system", "subtype": "init", "session_id": "test-session-123"}'
        result = parser.parse_line(line)
        assert result is not None
        assert result.kind == BackendEventKind.SESSION_META
        assert result.session_id == "test-session-123"

    def test_parse_line_result_with_marker(self) -> None:
        parser = ClaudeStreamParser(completion_marker="<result>")
        line = '{"type": "result", "result": "<result>", "subtype": "success", "session_id": "s1"}'
        result = parser.parse_line(line)
        assert result is not None
        assert result.kind == BackendEventKind.COMPLETION
        assert result.has_marker is True

    def test_parse_line_result_without_marker(self) -> None:
        parser = ClaudeStreamParser(completion_marker="<result>")
        line = (
            '{"type": "result", "result": "some text", "subtype": "success", "session_id": "s1"}'
        )
        result = parser.parse_line(line)
        assert result is not None
        assert result.kind == BackendEventKind.COMPLETION
        assert result.has_marker is False

    def test_parse_line_result_empty_ignored(self) -> None:
        parser = ClaudeStreamParser()
        line = '{"type": "result", "result": ""}'
        result = parser.parse_line(line)
        assert result is not None
        assert result.kind == BackendEventKind.IGNORED

    def test_parse_line_assistant_context_exhaustion(self) -> None:
        parser = ClaudeStreamParser()
        line = (
            '{"type": "assistant", "output_tokens": 0,'
            ' "content": [{"type": "text", "text": "prompt is too long"}]}'
        )
        result = parser.parse_line(line)
        assert result is not None
        assert result.kind == BackendEventKind.TOOL_OUTPUT

    def test_parse_line_assistant_with_message_key_and_zero_tokens_ignored(self) -> None:
        parser = ClaudeStreamParser()
        line = '{"type": "assistant", "message": {"content": "hello"}, "output_tokens": 0}'
        result = parser.parse_line(line)
        assert result is not None
        assert result.kind == BackendEventKind.IGNORED

    def test_parse_line_regular_assistant_ignored(self) -> None:
        parser = ClaudeStreamParser()
        line = '{"type": "assistant", "message": {"content": "hello"}}'
        result = parser.parse_line(line)
        assert result is not None
        assert result.kind == BackendEventKind.IGNORED

    def test_parse_line_unknown_type_ignored(self) -> None:
        parser = ClaudeStreamParser()
        line = '{"type": "user", "content": "hello"}'
        result = parser.parse_line(line)
        assert result is not None
        assert result.kind == BackendEventKind.IGNORED

    def test_parse_line_malformed_json_none(self) -> None:
        parser = ClaudeStreamParser()
        result = parser.parse_line("not valid json")
        assert result is None

    def test_parse_line_empty_string_none(self) -> None:
        parser = ClaudeStreamParser()
        result = parser.parse_line("")
        assert result is None

    def test_parse_line_non_dict_json_none(self) -> None:
        parser = ClaudeStreamParser()
        result = parser.parse_line("123")
        assert result is None

    def test_structural_conformance_stream_parser(self) -> None:
        assert isinstance(ClaudeStreamParser(), StreamParser)

    def test_parse_line_system_init_subtype_records_session_id(self) -> None:
        parser = ClaudeStreamParser()
        line = json.dumps(
            {"type": "system", "subtype": "init", "session_id": "conversation-uuid-456"}
        )
        result = parser.parse_line(line)
        assert result is not None
        assert result.kind == BackendEventKind.SESSION_META
        assert result.session_id == "conversation-uuid-456"

    def test_parse_line_system_hook_started_no_session_id(self) -> None:
        parser = ClaudeStreamParser()
        line = json.dumps(
            {"type": "system", "subtype": "hook_started", "session_id": "process-uuid-123"}
        )
        result = parser.parse_line(line)
        assert result is not None
        assert result.kind == BackendEventKind.SESSION_META
        assert result.session_id is None


class TestClaudeStreamParserLifecycleEvents:
    def test_system_declaration_carries_exact_normalized_observation(self) -> None:
        record = {
            "type": "system",
            "subtype": "task_started",
            "task_id": "agent-task-7",
            "tool_use_id": "toolu_parent_agent_7",
            "agent_id": "agent-task-7",
            "status": "async_launched",
            "uuid": "sys-agent-started-7",
            "parent_message_id": "msg-parent-7",
            "session_id": "claude-child-lifecycle-session",
        }

        event = ClaudeStreamParser().parse_line(json.dumps(record))

        assert event is not None
        assert event.kind is BackendEventKind.SESSION_META
        assert event.observations == (
            ChildLifecycleObservation(
                task_kind="Agent",
                task_id="agent-task-7",
                tool_use_id="toolu_parent_agent_7",
                agent_id="agent-task-7",
                background_task_id="",
                attempt_state=ChildAttemptState.ACTIVE,
                source_event_id="sys-agent-started-7",
                parent_turn_id="msg-parent-7",
                byte_offset=0,
                is_parent_declaration=True,
                is_user_result=False,
                replaces_native_uuid="",
                replaced_by_native_uuid="",
                attempt_generation=0,
            ),
        )
        assert event.lifecycle_issues == ()
        assert event.parent_marker is None

    def test_terminal_tool_result_outranks_async_flag_in_session_event(self) -> None:
        record = {
            "type": "user",
            "message": {
                "id": "msg-user-result-7",
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_parent_agent_7",
                        "content": {
                            "async_launched": True,
                            "agentId": "agent-task-7",
                            "status": "completed",
                        },
                        "is_error": False,
                    }
                ],
            },
            "uuid": "user-result-7",
            "parent_tool_use_id": None,
            "session_id": "claude-child-lifecycle-session",
        }

        event = ClaudeStreamParser().parse_line(json.dumps(record))

        assert event is not None
        assert event.kind is BackendEventKind.IGNORED
        assert event.observations == (
            ChildLifecycleObservation(
                task_kind="Agent",
                task_id="",
                tool_use_id="toolu_parent_agent_7",
                agent_id="agent-task-7",
                background_task_id="",
                attempt_state=ChildAttemptState.COMPLETED,
                source_event_id="user-result-7",
                parent_turn_id="msg-user-result-7",
                byte_offset=0,
                is_parent_declaration=False,
                is_user_result=True,
                replaces_native_uuid="",
                replaced_by_native_uuid="",
                attempt_generation=0,
            ),
        )
        assert event.lifecycle_issues == ()
        assert event.parent_marker is None

    def test_unknown_terminal_status_carries_exact_blocking_issue(self) -> None:
        record = {
            "type": "system",
            "subtype": "task_notification",
            "task_id": "agent-task-7",
            "tool_use_id": "toolu_parent_agent_7",
            "agent_id": "agent-task-7",
            "status": "paused",
            "uuid": "sys-agent-notification-7",
            "parent_message_id": "msg-parent-7",
            "session_id": "claude-child-lifecycle-session",
        }

        event = ClaudeStreamParser().parse_line(json.dumps(record))

        assert event is not None
        assert event.kind is BackendEventKind.SESSION_META
        assert event.lifecycle_issues == (
            LifecycleEvidenceIssue(
                issue_kind=LifecycleEvidenceIssueKind.UNKNOWN_STATUS,
                task_kind="Agent",
                native_aliases=(
                    "agent-task-7",
                    "toolu_parent_agent_7",
                    "agent-task-7",
                    "",
                ),
                source_event_uuid="sys-agent-notification-7",
                canonical_fingerprint=(
                    "Agent|task_id=agent-task-7|tool_use_id=toolu_parent_agent_7|"
                    "agent_id=agent-task-7"
                ),
                channel_relative_byte_offset=0,
                native_alias_kinds=(
                    "task_id",
                    "tool_use_id",
                    "agent_id",
                    "background_task_id",
                ),
                resolution=LifecycleEvidenceResolution.PENDING,
                detail="unknown task_notification status: 'paused'",
            ),
        )

    def test_parent_assistant_marker_carries_exact_session_event_provenance(self) -> None:
        marker = "%autoskillit:fresh-parent-marker:abc12345%"
        record = {
            "type": "assistant",
            "message": {
                "id": "msg-parent-marker-7",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [
                    {"type": "text", "text": f"Child work delivered.\n{marker}"},
                ],
            },
            "uuid": "parent-marker-uuid-7",
            "parent_tool_use_id": None,
            "session_id": "claude-child-lifecycle-session",
        }

        event = ClaudeStreamParser(completion_marker=marker).parse_line(json.dumps(record))

        assert event is not None
        assert event.kind is BackendEventKind.IGNORED
        assert event.parent_marker == ParentAssistantMarker(
            native_uuid="parent-marker-uuid-7",
            message_id="msg-parent-marker-7",
            byte_offset=0,
            backend_session_id="claude-child-lifecycle-session",
        )
        assert event.observations == ()
        assert event.lifecycle_issues == ()

    @pytest.mark.parametrize(
        "marker_text",
        [
            "prefix %autoskillit:fresh-parent-marker:abc12345% suffix",
            '"%autoskillit:fresh-parent-marker:abc12345%"',
            "`%autoskillit:fresh-parent-marker:abc12345%`",
        ],
        ids=["embedded", "quoted", "backticked"],
    )
    def test_parent_assistant_rejects_non_standalone_marker_text(self, marker_text: str) -> None:
        marker = "%autoskillit:fresh-parent-marker:abc12345%"
        record = {
            "type": "assistant",
            "message": {
                "id": "msg-parent-marker-7",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": f"Status:\n{marker_text}"}],
            },
            "uuid": "parent-marker-uuid-7",
            "parent_tool_use_id": None,
            "session_id": "claude-child-lifecycle-session",
        }

        event = ClaudeStreamParser(completion_marker=marker).parse_line(json.dumps(record))

        assert event is not None
        assert event.kind is BackendEventKind.IGNORED
        assert event.parent_marker is None

    @pytest.mark.parametrize(
        "marker_text",
        [
            "prefix %autoskillit:fresh-parent-marker:abc12345% suffix",
            '"%autoskillit:fresh-parent-marker:abc12345%"',
            "`%autoskillit:fresh-parent-marker:abc12345%`",
        ],
        ids=["embedded", "quoted", "backticked"],
    )
    def test_result_rejects_non_standalone_completion_marker(self, marker_text: str) -> None:
        marker = "%autoskillit:fresh-parent-marker:abc12345%"
        record = {
            "type": "result",
            "subtype": "success",
            "result": f"Completed work.\n{marker_text}",
            "session_id": "claude-child-lifecycle-session",
        }

        event = ClaudeStreamParser(completion_marker=marker).parse_line(json.dumps(record))

        assert event is not None
        assert event.kind is BackendEventKind.COMPLETION
        assert event.has_marker is False
        assert event.parent_marker is None


class TestClaudeStreamParserBackendData:
    def test_result_line_populates_claude_event_data(self) -> None:
        parser = ClaudeStreamParser()
        result = parser.parse_line(_result_line())
        assert result is not None
        assert result.backend_data is not None
        assert isinstance(result.backend_data, ClaudeEventData)
        assert result.backend_data.record_type == "result"
        assert result.backend_data.subtype == "success"
        assert result.backend_data.session_id == "s1"

    def test_result_line_raw_contains_original_dict(self) -> None:
        parser = ClaudeStreamParser()
        result = parser.parse_line(_result_line())
        assert result is not None
        assert result.backend_data is not None
        assert result.backend_data.raw["type"] == "result"
        assert result.backend_data.raw["result"] == "done"

    def test_context_exhaustion_populates_backend_data(self) -> None:
        parser = ClaudeStreamParser()
        result = parser.parse_line(_context_exhaustion_line())
        assert result is not None
        assert result.backend_data is not None
        assert result.backend_data.subtype == "context_exhaustion"
        assert result.backend_data.record_type == "assistant"

    def test_system_line_has_no_backend_data(self) -> None:
        parser = ClaudeStreamParser()
        result = parser.parse_line(_system_line())
        assert result is not None
        assert result.backend_data is None

    def test_ignored_assistant_retains_single_decode_payload(self) -> None:
        parser = ClaudeStreamParser()
        result = parser.parse_line(_assistant_line())
        assert result is not None
        assert isinstance(result.backend_data, ClaudeEventData)
        assert result.backend_data.record_type == "assistant"


class TestClaudeResultParserSessionId:
    def test_parse_result_uses_init_session_id(self) -> None:
        from autoskillit.execution.backends.claude import ClaudeResultParser

        parser_stream = ClaudeStreamParser()
        hook_event = parser_stream.parse_line(
            json.dumps({"type": "system", "subtype": "hook_started", "session_id": "proc-uuid"})
        )
        init_event = parser_stream.parse_line(
            json.dumps({"type": "system", "subtype": "init", "session_id": "conv-uuid"})
        )
        result_event = parser_stream.parse_line(
            json.dumps(
                {"type": "result", "result": "%%DONE%%", "subtype": "success", "session_id": "s1"}
            )
        )
        assert hook_event is not None
        assert init_event is not None
        assert result_event is not None

        result_parser = ClaudeResultParser()
        # hook_started arrives first, then init — parse_result must pick conv-uuid
        asr = result_parser.parse_result([hook_event, init_event, result_event])
        assert asr.session_id == "conv-uuid"
