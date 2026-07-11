"""Tests for the pure Channel A lifecycle / candidate normalization helpers.

The helpers in ``_claude_lifecycle.py`` are side-effect-free; every test
exercises one input shape and asserts the produced observations/marker.
The tests are deterministic and xdist-safe — they never spawn a process
or read a file.
"""

from __future__ import annotations

from typing import Any

import pytest

from autoskillit.core import (
    ChildAttemptState,
    CompletionCandidateSource,
    ParentAssistantMarker,
)
from autoskillit.execution.backends._claude_lifecycle import (
    extract_lifecycle_observations,
    extract_parent_assistant_marker,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _agent_system_subtype(
    *,
    subtype: str = "task_started",
    agent_id: str = "agent_X",
    task_id: str = "task_X",
    tool_use_id: str = "toolu_X",
    status: str | None = None,
    replaces: str | None = None,
    replaced_by: str | None = None,
) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "type": "system",
        "subtype": subtype,
        "agent_id": agent_id,
        "task_id": task_id,
        "tool_use_id": tool_use_id,
        "uuid": "uuid_system",
        "parent_message_id": "msg_parent",
    }
    if status is not None:
        obj["status"] = status
    if replaces is not None:
        obj["replaces"] = replaces
    if replaced_by is not None:
        obj["replaced_by"] = replaced_by
    return obj


def _bash_system_subtype(
    *,
    subtype: str = "task_started",
    background_task_id: str = "bg_X",
    task_id: str = "task_X",
    status: str | None = None,
) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "type": "system",
        "subtype": subtype,
        "background_task_id": background_task_id,
        "task_id": task_id,
        "uuid": "uuid_system",
    }
    if status is not None:
        obj["status"] = status
    return obj


def _assistant(
    *,
    marker_text: str = "",
    uuid: str = "uuid_assistant",
    message_id: str = "msg_assistant",
    session_id: str = "session_X",
) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    if marker_text:
        content.append({"type": "text", "text": f"prelude\n{marker_text}\npostlude"})
    return {
        "type": "assistant",
        "uuid": uuid,
        "session_id": session_id,
        "message": {"id": message_id, "content": content},
    }


class TestSystemObservations:
    def test_task_started_yields_active_observation(self) -> None:
        obs = extract_lifecycle_observations(
            _agent_system_subtype(subtype="task_started"), "system", byte_offset=64
        )
        assert len(obs) == 1
        o = obs[0]
        assert o.task_kind == "Agent"
        assert o.attempt_state == ChildAttemptState.ACTIVE
        assert o.is_parent_declaration
        assert o.byte_offset == 64

    def test_task_progress_yields_active_observation(self) -> None:
        obs = extract_lifecycle_observations(
            _agent_system_subtype(subtype="task_progress"), "system", byte_offset=128
        )
        assert len(obs) == 1
        assert obs[0].attempt_state == ChildAttemptState.ACTIVE
        assert obs[0].byte_offset == 128

    def test_task_notification_terminal_state(self) -> None:
        obs = extract_lifecycle_observations(
            _agent_system_subtype(subtype="task_notification", status="completed"),
            "system",
            byte_offset=256,
        )
        assert obs[0].attempt_state == ChildAttemptState.COMPLETED

    def test_replaces_replaced_by_native_uuid(self) -> None:
        obs = extract_lifecycle_observations(
            _agent_system_subtype(
                subtype="task_started",
                replaces="evt_old",
                replaced_by="evt_new",
            ),
            "system",
            byte_offset=512,
        )
        assert obs[0].replaces_native_uuid == "evt_old"
        assert obs[0].replaced_by_native_uuid == "evt_new"

    def test_bash_kind_via_background_task_id(self) -> None:
        obs = extract_lifecycle_observations(_bash_system_subtype(), "system", byte_offset=0)
        assert obs[0].task_kind == "Bash"

    def test_unknown_kind_is_skipped(self) -> None:
        obj = {
            "type": "system",
            "subtype": "task_started",
            "task_id": "task_X",
            "uuid": "uuid_system",
        }
        assert extract_lifecycle_observations(obj, "system") == ()

    def test_unknown_subtype_is_skipped(self) -> None:
        obj = {
            "type": "system",
            "subtype": "api_retry",
            "agent_id": "agent_X",
            "task_id": "task_X",
            "uuid": "uuid_system",
        }
        assert extract_lifecycle_observations(obj, "system") == ()

    def test_non_string_alias_normalized_to_blank(self) -> None:
        obj: dict[str, Any] = {
            "type": "system",
            "subtype": "task_started",
            "agent_id": "agent_X",
            "task_id": 12345,
            "tool_use_id": None,
            "uuid": "uuid_system",
        }
        obs = extract_lifecycle_observations(obj, "system")
        assert obs[0].task_id == ""
        assert obs[0].tool_use_id == ""


class TestUserObservations:
    def _user_tool_result(
        self,
        *,
        tool_use_id: str,
        status: str | None = None,
        async_launched: bool = False,
        agent_id: str = "",
        background_task_id: str = "",
        is_error: bool = False,
    ) -> dict[str, Any]:
        content_obj: dict[str, Any] = {}
        if status is not None:
            content_obj["status"] = status
        if async_launched:
            content_obj["async_launched"] = True
        if agent_id:
            content_obj["agentId"] = agent_id
        if background_task_id:
            content_obj["backgroundTaskId"] = background_task_id
        return {
            "type": "user",
            "uuid": "uuid_user",
            "message": {
                "id": "msg_user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": content_obj,
                        "is_error": is_error,
                    }
                ],
            },
        }

    def test_async_launched_yields_active(self) -> None:
        obj = self._user_tool_result(tool_use_id="toolu_A", async_launched=True)
        obs = extract_lifecycle_observations(obj, "user", byte_offset=10)
        assert obs[0].attempt_state == ChildAttemptState.ACTIVE
        assert obs[0].is_user_result

    def test_completed_status_yields_completed(self) -> None:
        obj = self._user_tool_result(tool_use_id="toolu_A", status="completed")
        obs = extract_lifecycle_observations(obj, "user")
        assert obs[0].attempt_state == ChildAttemptState.COMPLETED

    def test_failed_status_yields_failed(self) -> None:
        obj = self._user_tool_result(tool_use_id="toolu_A", status="failed")
        obs = extract_lifecycle_observations(obj, "user")
        assert obs[0].attempt_state == ChildAttemptState.FAILED

    def test_cancelled_status_yields_cancelled(self) -> None:
        obj = self._user_tool_result(tool_use_id="toolu_A", status="cancelled")
        obs = extract_lifecycle_observations(obj, "user")
        assert obs[0].attempt_state == ChildAttemptState.CANCELLED

    def test_no_status_no_async_is_skipped(self) -> None:
        obj = self._user_tool_result(tool_use_id="toolu_A")
        assert extract_lifecycle_observations(obj, "user") == ()

    def test_unknown_tool_use_id_with_background_task_id_correlates_bash(self) -> None:
        obj = self._user_tool_result(
            tool_use_id="not_toolu_prefix",
            background_task_id="bg_B",
            status="completed",
        )
        obs = extract_lifecycle_observations(obj, "user")
        assert obs[0].task_kind == "Bash"

    def test_unknown_tool_use_id_no_correlation_skipped(self) -> None:
        obj = self._user_tool_result(
            tool_use_id="not_toolu_prefix",
            status="completed",
        )
        assert extract_lifecycle_observations(obj, "user") == ()

    def test_multiple_tool_results_in_one_record(self) -> None:
        obj: dict[str, Any] = {
            "type": "user",
            "uuid": "uuid_user",
            "message": {
                "id": "msg_user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_A",
                        "content": {"async_launched": True},
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_B",
                        "content": {"status": "completed"},
                    },
                ],
            },
        }
        obs = extract_lifecycle_observations(obj, "user", byte_offset=99)
        assert len(obs) == 2
        assert obs[0].byte_offset == 99
        assert obs[1].byte_offset == 99


class TestParentAssistantMarker:
    def test_marker_with_text_emits_parent_marker(self) -> None:
        result = extract_parent_assistant_marker(
            _assistant(marker_text="AUTOSKILLIT_COMPLETION"),
            byte_offset=512,
            completion_marker="AUTOSKILLIT_COMPLETION",
        )
        assert isinstance(result.marker, ParentAssistantMarker)
        assert result.marker.native_uuid == "uuid_assistant"
        assert result.marker.message_id == "msg_assistant"
        assert result.marker.byte_offset == 512
        assert result.marker.backend_session_id == "session_X"

    def test_blank_uuid_yields_no_marker(self) -> None:
        result = extract_parent_assistant_marker(
            _assistant(uuid="", marker_text="AUTOSKILLIT_COMPLETION"),
            byte_offset=10,
            completion_marker="AUTOSKILLIT_COMPLETION",
        )
        assert result.marker is None

    def test_unknown_uuid_yields_no_marker(self) -> None:
        result = extract_parent_assistant_marker(
            _assistant(uuid="unknown", marker_text="AUTOSKILLIT_COMPLETION"),
            byte_offset=10,
            completion_marker="AUTOSKILLIT_COMPLETION",
        )
        assert result.marker is None

    def test_numeric_uuid_yields_no_marker(self) -> None:
        result = extract_parent_assistant_marker(
            _assistant(uuid="12345", marker_text="AUTOSKILLIT_COMPLETION"),
            byte_offset=10,
            completion_marker="AUTOSKILLIT_COMPLETION",
        )
        assert result.marker is None

    def test_blank_message_id_yields_no_marker(self) -> None:
        result = extract_parent_assistant_marker(
            _assistant(message_id="", marker_text="AUTOSKILLIT_COMPLETION"),
            byte_offset=10,
            completion_marker="AUTOSKILLIT_COMPLETION",
        )
        assert result.marker is None

    def test_no_marker_text_yields_no_marker(self) -> None:
        result = extract_parent_assistant_marker(
            _assistant(marker_text=""),
            byte_offset=10,
            completion_marker="AUTOSKILLIT_COMPLETION",
        )
        assert result.marker is None

    def test_non_assistant_record_yields_no_marker(self) -> None:
        result = extract_parent_assistant_marker(
            {"type": "system", "uuid": "u", "message": {"id": "m", "content": []}},
            byte_offset=10,
            completion_marker="X",
        )
        assert result.marker is None

    def test_no_completion_marker_configured_yields_no_marker(self) -> None:
        result = extract_parent_assistant_marker(
            _assistant(marker_text="AUTOSKILLIT_COMPLETION"),
            byte_offset=10,
            completion_marker="",
        )
        assert result.marker is None


class TestProvenanceRules:
    def test_process_exit_does_not_emit_marker(self) -> None:
        # A "result" record is not an "assistant" record; the helper
        # must not synthesize a parent marker from it.
        result = extract_parent_assistant_marker(
            {
                "type": "result",
                "uuid": "uuid_result",
                "message": {
                    "id": "msg_result",
                    "content": [{"type": "text", "text": "AUTOSKILLIT_COMPLETION"}],
                },
            },
            byte_offset=10,
            completion_marker="AUTOSKILLIT_COMPLETION",
        )
        assert result.marker is None

    def test_channel_b_session_id_propagated(self) -> None:
        result = extract_parent_assistant_marker(
            _assistant(
                marker_text="AUTOSKILLIT_COMPLETION",
                session_id="channel-b-session",
            ),
            byte_offset=10,
            completion_marker="AUTOSKILLIT_COMPLETION",
        )
        assert result.marker is not None
        assert result.marker.backend_session_id == "channel-b-session"


class TestSourceOnlyCandidates:
    def test_sources_remain_separate(self) -> None:
        # Source attribution is coordinator-side; the helper never
        # collapses Channel A / Channel B into a synthesized candidate.
        from autoskillit.execution.process._child_lifecycle import (
            make_coordinator_handle,
        )

        h = make_coordinator_handle()
        candidate = h.register_parent_marker(
            ParentAssistantMarker(
                native_uuid="uuid-A",
                message_id="msg-A",
                byte_offset=10,
            )
        )
        assert candidate.sources == (CompletionCandidateSource.CHANNEL_A,)
