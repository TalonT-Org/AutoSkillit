"""Tests for _build_skill_result idle_stall lifespan_started propagation."""

from __future__ import annotations

import json

import pytest

from autoskillit.core.types import KillReason, SubprocessResult, TerminationReason
from autoskillit.execution.headless import _build_skill_result
from tests.execution.conftest import _make_tool_use_line, _sr, _success_session_json

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small, pytest.mark.feature("fleet")]


def _idle_stall_result(stdout: str) -> SubprocessResult:
    """Build a SubprocessResult with IDLE_STALL termination."""
    return SubprocessResult(
        returncode=-1,
        stdout=stdout,
        stderr="",
        termination=TerminationReason.IDLE_STALL,
        pid=12345,
        session_id="sess-idle-1",
        channel_b_session_id="",
    )


def _tool_use_ndjson(tool_name: str = "Write", **input_kwargs: object) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": tool_name,
                        "id": "tool-1",
                        "input": input_kwargs,
                    }
                ]
            },
        }
    )


def _success_result_json(result_text: str = "done", session_id: str = "test-sess") -> str:
    """Build a success result NDJSON line."""
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "result": result_text,
            "session_id": session_id,
            "is_error": False,
        }
    )


def _stale_result(
    kill_reason: KillReason = KillReason.NATURAL_EXIT, stdout: str = ""
) -> SubprocessResult:
    """Build a SubprocessResult with STALE termination and explicit kill_reason."""
    return SubprocessResult(
        returncode=-1,
        stdout=stdout,
        stderr="",
        termination=TerminationReason.STALE,
        kill_reason=kill_reason,
        pid=12345,
        session_id="sess-stale-1",
        channel_b_session_id="",
    )


def _idle_stall_result_with_kill(
    kill_reason: KillReason = KillReason.NATURAL_EXIT,
    stdout: str = "",
) -> SubprocessResult:
    """Build a SubprocessResult with IDLE_STALL termination and explicit kill_reason."""
    return SubprocessResult(
        returncode=-1,
        stdout=stdout,
        stderr="",
        termination=TerminationReason.IDLE_STALL,
        kill_reason=kill_reason,
        pid=12345,
        session_id="sess-idle-1",
        channel_b_session_id="",
    )


def _stale_result_with_token_usage(
    usage: dict[str, int],
    kill_reason: KillReason = KillReason.INFRA_KILL,
) -> SubprocessResult:
    """Build a stale SubprocessResult whose stdout contains token usage."""
    result_json = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "result": "work complete",
            "session_id": "sess-stale-token",
            "is_error": False,
            "usage": usage,
        }
    )
    return SubprocessResult(
        returncode=-1,
        stdout=result_json,
        stderr="",
        termination=TerminationReason.STALE,
        kill_reason=kill_reason,
        pid=12345,
        session_id="sess-stale-token",
        channel_b_session_id="",
    )


class TestIdleStallLifespanStarted:
    def test_idle_stall_failure_preserves_lifespan_started_true(self):
        stdout = _tool_use_ndjson("Write", file_path="/worktree/src/foo.py")
        result = _idle_stall_result(stdout)
        skill_result = _build_skill_result(result)
        assert skill_result.lifespan_started is True

    def test_idle_stall_failure_preserves_lifespan_started_false(self):
        result = _idle_stall_result("")
        skill_result = _build_skill_result(result)
        assert skill_result.lifespan_started is False


class TestKillReasonPropagation:
    def test_stale_failure_propagates_infra_kill(self):
        result = _stale_result(kill_reason=KillReason.INFRA_KILL)
        skill_result = _build_skill_result(result)
        assert skill_result.kill_reason == KillReason.INFRA_KILL

    def test_idle_stall_failure_propagates_infra_kill(self):
        result = _idle_stall_result_with_kill(kill_reason=KillReason.INFRA_KILL)
        skill_result = _build_skill_result(result)
        assert skill_result.kill_reason == KillReason.INFRA_KILL

    def test_recovered_stale_propagates_infra_kill(self):
        stdout = _success_result_json()
        result = _stale_result(kill_reason=KillReason.INFRA_KILL, stdout=stdout)
        skill_result = _build_skill_result(result, completion_marker="done")
        assert skill_result.kill_reason == KillReason.INFRA_KILL

    def test_recovered_idle_stall_propagates_infra_kill(self):
        stdout = _success_result_json()
        result = _idle_stall_result_with_kill(kill_reason=KillReason.INFRA_KILL, stdout=stdout)
        skill_result = _build_skill_result(result, completion_marker="done")
        assert skill_result.kill_reason == KillReason.INFRA_KILL

    def test_path_contamination_propagates_kill_reason(self):
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "result": "done",
                "session_id": "sess-contam",
                "is_error": False,
            }
        )
        result = SubprocessResult(
            returncode=-1,
            stdout=stdout,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            kill_reason=KillReason.INFRA_KILL,
            pid=12345,
            session_id="sess-contam",
            channel_b_session_id="",
        )
        skill_result = _build_skill_result(result, cwd="/wrong/path")
        assert skill_result.kill_reason == KillReason.INFRA_KILL


class TestStaleTokenUsagePropagation:
    """Verify stale branch propagates token_usage from parsed session, not hardcoded None."""

    def test_stale_failure_propagates_token_usage(self):
        usage = {
            "input_tokens": 100,
            "output_tokens": 200,
            "cache_write_tokens": 50,
            "cache_read_tokens": 75,
        }
        result = _stale_result_with_token_usage(usage, kill_reason=KillReason.INFRA_KILL)
        skill_result = _build_skill_result(result)
        assert skill_result.token_usage is not None
        tu = skill_result.token_usage
        assert tu["input_tokens"] == 100
        assert tu["output_tokens"] == 200
        assert tu["cache_write_tokens"] == 50
        assert tu["cache_read_tokens"] == 75


class TestBackendDelegatedWriteToolNames:
    def test_backend_none_uses_fallback_write_edit(self):
        """backend=None falls back to frozenset({'Write', 'Edit'})."""
        stdout = (
            _make_tool_use_line("Write", {"file_path": "/a/b.py", "content": "x"})
            + "\n"
            + _make_tool_use_line(
                "Edit", {"file_path": "/a/c.py", "old_string": "a", "new_string": "b"}
            )
            + "\n"
            + _success_session_json("Done")
        )
        result = _sr(0, stdout, "", TerminationReason.NATURAL_EXIT)
        sr = _build_skill_result(result, backend=None)
        assert sr.write_call_count == 2

    def test_backend_provides_custom_tool_names(self):
        """Custom backend.write_tool_names() overrides the tool name set."""
        from unittest.mock import Mock

        mock_backend = Mock()
        mock_backend.write_tool_names.return_value = frozenset({"CustomWrite"})
        stdout = (
            _make_tool_use_line("CustomWrite", {"file_path": "/a/b.py", "content": "x"})
            + "\n"
            + _make_tool_use_line("Write", {"file_path": "/a/c.py", "content": "x"})
            + "\n"
            + _success_session_json("Done")
        )
        result = _sr(0, stdout, "", TerminationReason.NATURAL_EXIT)
        sr = _build_skill_result(result, backend=mock_backend)
        assert sr.write_call_count == 1

    def test_backend_none_default_preserves_behavior(self):
        """backend=None with no Write/Edit tools yields write_call_count=0."""
        stdout = (
            _make_tool_use_line("Read", {"file_path": "/a/b.py"})
            + "\n"
            + _success_session_json("Done")
        )
        result = _sr(0, stdout, "", TerminationReason.NATURAL_EXIT)
        sr = _build_skill_result(result, backend=None)
        assert sr.write_call_count == 0


class TestParseStdout:
    def test_backend_none_calls_parse_session_result(self):
        """_parse_stdout with backend=None returns the same result as parse_session_result."""
        from autoskillit.execution.headless import _parse_stdout
        from autoskillit.execution.session import parse_session_result

        stdout = _success_session_json("test result")
        result = _parse_stdout(stdout, backend=None)
        expected = parse_session_result(stdout)
        assert result.result == expected.result
        assert result.session_id == expected.session_id
        assert result.session_complete == expected.session_complete

    def test_default_backend_returns_claude_session_result(self):
        """_parse_stdout with no backend arg returns a ClaudeSessionResult."""
        from autoskillit.execution.headless import _parse_stdout

        stdout = _success_session_json("test result")
        result = _parse_stdout(stdout)
        assert result is not None
        assert result.result == "test result"
