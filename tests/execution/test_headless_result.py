"""Tests for _build_skill_result idle_stall lifespan_started propagation."""

from __future__ import annotations

import json
import unittest.mock
from unittest.mock import Mock

import pytest
import structlog.testing

from autoskillit.core import AGENT_BACKEND_CLAUDE_CODE
from autoskillit.core.types import (
    AgentSessionResult,
    CliSubtype,
    KillReason,
    RetryReason,
    SkillResult,
    SubprocessResult,
    TerminationReason,
    WriteBehaviorSpec,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend, ClaudeResultParser
from autoskillit.execution.backends.codex import CodexBackend
from autoskillit.execution.headless import (
    _build_skill_result,
    _extract_missing_token_hints,
    _parse_stdout,
    _synthesize_from_write_artifacts,
)
from autoskillit.execution.headless._headless_evidence import (
    _adapt_agent_result,
    _compute_write_evidence,
    _stdout_mentions_write_tools,
)
from autoskillit.execution.session import ClaudeSessionResult
from autoskillit.execution.session._session_outcome import _compute_outcome
from tests.execution.conftest import _make_tool_use_line, _sr, _success_session_json
from tests.fixtures.codex import HAPPY_PATH_SINGLE_TURN, TURN_FAILED_ERROR, fixture_path

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


def _adapt_codex_result(agent_result: AgentSessionResult) -> ClaudeSessionResult:
    return _adapt_agent_result(agent_result)


def _make_codex_parse_stdout() -> object:
    """Return a _parse_stdout replacement that delegates to CodexResultParser.

    Simulates Phase C wiring: parses Codex NDJSON via CodexResultParser,
    adapts via _adapt_codex_result, and extracts error codes from turn.failed
    events into session.errors for API error detection.

    The ``backend`` parameter is accepted for signature compatibility with
    _parse_stdout (called as ``_parse_stdout(stdout, backend=backend)`` inside
    _build_skill_result) but is intentionally ignored — this monkeypatch
    unconditionally routes through CodexResultParser.
    """

    def _patched(stdout: str, backend: object) -> ClaudeSessionResult:  # noqa: ARG001
        agent_result = CodexBackend().result_parser().parse_stdout(stdout)
        session = _adapt_codex_result(agent_result)
        for line in stdout.strip().splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "turn.failed":
                error = obj.get("error", {})
                if isinstance(error, dict):
                    code = error.get("code", "")
                    if code:
                        session.errors.append(code)
        return session

    return _patched  # type: ignore[return-value]


def _codex_subprocess_result(
    stdout: str,
    *,
    returncode: int = 0,
    stderr: str = "",
    termination: TerminationReason = TerminationReason.NATURAL_EXIT,
    kill_reason: KillReason = KillReason.NATURAL_EXIT,
) -> SubprocessResult:
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        termination=termination,
        kill_reason=kill_reason,
        pid=12345,
        session_id="",
        channel_b_session_id="",
    )


def _make_session(tool_name: str, file_path: str) -> ClaudeSessionResult:
    return ClaudeSessionResult(
        subtype=CliSubtype.SUCCESS,
        is_error=False,
        result="",
        session_id="s-wiring",
        tool_uses=[{"name": tool_name, "id": "t1", "file_path": file_path}],
    )


class TestIdleStallLifespanStarted:
    def test_idle_stall_failure_preserves_lifespan_started_true(self):
        stdout = _tool_use_ndjson("Write", file_path="/worktree/src/foo.py")
        result = _idle_stall_result(stdout)
        skill_result = _build_skill_result(result, backend=ClaudeCodeBackend())
        assert skill_result.lifespan_started is True

    def test_idle_stall_failure_preserves_lifespan_started_false(self):
        result = _idle_stall_result("")
        skill_result = _build_skill_result(result, backend=ClaudeCodeBackend())
        assert skill_result.lifespan_started is False


class TestKillReasonPropagation:
    def test_stale_failure_propagates_infra_kill(self):
        result = _stale_result(kill_reason=KillReason.INFRA_KILL)
        skill_result = _build_skill_result(result, backend=ClaudeCodeBackend())
        assert skill_result.kill_reason == KillReason.INFRA_KILL

    def test_idle_stall_failure_propagates_infra_kill(self):
        result = _idle_stall_result_with_kill(kill_reason=KillReason.INFRA_KILL)
        skill_result = _build_skill_result(result, backend=ClaudeCodeBackend())
        assert skill_result.kill_reason == KillReason.INFRA_KILL

    def test_recovered_stale_propagates_infra_kill(self):
        stdout = _success_result_json()
        result = _stale_result(kill_reason=KillReason.INFRA_KILL, stdout=stdout)
        skill_result = _build_skill_result(
            result, completion_marker="done", backend=ClaudeCodeBackend()
        )
        assert skill_result.kill_reason == KillReason.INFRA_KILL

    def test_recovered_idle_stall_propagates_infra_kill(self):
        stdout = _success_result_json()
        result = _idle_stall_result_with_kill(kill_reason=KillReason.INFRA_KILL, stdout=stdout)
        skill_result = _build_skill_result(
            result, completion_marker="done", backend=ClaudeCodeBackend()
        )
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
        skill_result = _build_skill_result(result, cwd="/wrong/path", backend=ClaudeCodeBackend())
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
        skill_result = _build_skill_result(result, backend=ClaudeCodeBackend())
        assert skill_result.token_usage is not None
        tu = skill_result.token_usage
        assert tu["input_tokens"] == 100
        assert tu["output_tokens"] == 200
        assert tu["cache_write_tokens"] == 50
        assert tu["cache_read_tokens"] == 75


class TestBackendDelegatedWriteToolNames:
    def test_claude_backend_uses_write_edit_tool_names(self):
        """ClaudeCodeBackend uses frozenset({'Write', 'Edit'})."""
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
        sr = _build_skill_result(result, backend=ClaudeCodeBackend())
        assert sr.evidence.write_call_count == 2

    def test_backend_provides_custom_tool_names(self):
        """Custom backend.write_tool_names() overrides the tool name set."""

        mock_backend = Mock()
        mock_backend.name = AGENT_BACKEND_CLAUDE_CODE
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
        assert sr.evidence.write_call_count == 1

    def test_default_backend_behavior_preserved(self):
        """backend=None with no Write/Edit tools yields write_call_count=0."""
        stdout = (
            _make_tool_use_line("Read", {"file_path": "/a/b.py"})
            + "\n"
            + _success_session_json("Done")
        )
        result = _sr(0, stdout, "", TerminationReason.NATURAL_EXIT)
        sr = _build_skill_result(result, backend=ClaudeCodeBackend())
        assert sr.evidence.write_call_count == 0

    def test_codex_backend_produces_zero_write_count(self):
        """CodexBackend.write_tool_names() is empty — write_call_count must be 0."""

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
        sr = _build_skill_result(result, backend=CodexBackend())
        assert sr.evidence.write_call_count == 0

    def test_claude_backend_counts_write_and_edit(self):
        """ClaudeCodeBackend.write_tool_names() includes Write and Edit."""
        from autoskillit.execution.backends.claude import ClaudeCodeBackend

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
        sr = _build_skill_result(result, backend=ClaudeCodeBackend())
        assert sr.evidence.write_call_count == 2

    def test_build_skill_result_threads_backend_to_parse_stdout(self, monkeypatch):
        """_build_skill_result passes backend to _parse_stdout on normal exit."""

        from autoskillit.execution.headless import _headless_result

        captured: dict = {}
        original_parse = _headless_result._parse_stdout

        def spy(stdout, backend):
            captured["backend"] = backend
            return original_parse(stdout, backend=backend)

        monkeypatch.setattr(_headless_result, "_parse_stdout", spy)

        mock_backend = Mock()
        mock_backend.name = AGENT_BACKEND_CLAUDE_CODE
        stdout = _success_session_json("Done")
        result = _sr(0, stdout, "", TerminationReason.NATURAL_EXIT)
        _build_skill_result(result, backend=mock_backend)
        assert "backend" in captured, "_parse_stdout was not called"
        assert captured["backend"] is mock_backend

    def test_build_skill_result_stale_threads_backend_to_parse_stdout(self, monkeypatch):
        """_build_skill_result passes backend to _parse_stdout on stale branch."""

        from autoskillit.execution.headless import _headless_result

        captured: dict = {}
        original_parse = _headless_result._parse_stdout

        def spy(stdout, backend):
            captured["backend"] = backend
            return original_parse(stdout, backend=backend)

        monkeypatch.setattr(_headless_result, "_parse_stdout", spy)

        mock_backend = Mock()
        mock_backend.name = AGENT_BACKEND_CLAUDE_CODE
        stdout = _success_session_json("Done")
        result = _sr(0, stdout, "", TerminationReason.STALE)
        _build_skill_result(result, backend=mock_backend)
        assert "backend" in captured, "_parse_stdout was not called"
        assert captured["backend"] is mock_backend

    def test_build_skill_result_idle_stall_threads_backend_to_parse_stdout(self, monkeypatch):
        """_build_skill_result passes backend to _parse_stdout on idle_stall branch."""

        from autoskillit.execution.headless import _headless_result

        captured: dict = {}
        original_parse = _headless_result._parse_stdout

        def spy(stdout, backend):
            captured["backend"] = backend
            return original_parse(stdout, backend=backend)

        monkeypatch.setattr(_headless_result, "_parse_stdout", spy)

        mock_backend = Mock()
        mock_backend.name = AGENT_BACKEND_CLAUDE_CODE
        stdout = _success_session_json("Done")
        result = _sr(0, stdout, "", TerminationReason.IDLE_STALL)
        _build_skill_result(result, backend=mock_backend)
        assert "backend" in captured, "_parse_stdout was not called"
        assert captured["backend"] is mock_backend

    def test_build_skill_result_timed_out_threads_backend_to_parse_stdout(self, monkeypatch):
        """_build_skill_result passes backend to _parse_stdout on timed_out branch."""

        from autoskillit.execution.headless import _headless_result

        captured: dict = {}
        original_parse = _headless_result._parse_stdout

        def spy(stdout, backend):
            captured["backend"] = backend
            return original_parse(stdout, backend=backend)

        monkeypatch.setattr(_headless_result, "_parse_stdout", spy)

        mock_backend = Mock()
        mock_backend.name = AGENT_BACKEND_CLAUDE_CODE
        stdout = _success_session_json("Done")
        result = _sr(0, stdout, "", TerminationReason.TIMED_OUT)
        _build_skill_result(result, backend=mock_backend)
        assert "backend" in captured, "_parse_stdout was not called"
        assert captured["backend"] is mock_backend


class TestRecoveryWriteNameWiring:
    """Write-tool-name wiring: _synthesize and _extract respect write_tool_names."""

    def test_synthesize_recognizes_non_default_write_tool_name(self):
        session = _make_session("CustomWrite", "/tmp/plan.md")
        result = _synthesize_from_write_artifacts(
            session,
            expected_output_patterns=[r"plan_path\s*=\s*/.+"],
            write_call_count=1,
            write_tool_names=frozenset({"CustomWrite"}),
        )
        assert result is not None

    def test_synthesize_ignores_claude_names_when_write_tool_names_empty(self):
        session = _make_session("Write", "/tmp/plan.md")
        result = _synthesize_from_write_artifacts(
            session,
            expected_output_patterns=[r"plan_path\s*=\s*/.+"],
            write_call_count=1,
            write_tool_names=frozenset(),
        )
        assert result is None

    def test_synthesize_explicit_frozenset_overrides_default(self):
        session = ClaudeSessionResult(
            subtype=CliSubtype.SUCCESS,
            is_error=False,
            result="",
            session_id="s-wiring",
            tool_uses=[
                {"name": "Write", "id": "t1", "file_path": "/tmp/wrong.md"},
                {"name": "CustomEdit", "id": "t2", "file_path": "/tmp/right.md"},
            ],
        )
        result = _synthesize_from_write_artifacts(
            session,
            expected_output_patterns=[r"plan_path\s*=\s*/.+"],
            write_call_count=2,
            write_tool_names=frozenset({"CustomEdit"}),
        )
        assert result is not None
        assert "/tmp/right.md" in result.result
        assert "/tmp/wrong.md" not in result.result

    def test_extract_hints_recognizes_non_default_write_tool_name(self):
        mock_parser = Mock()
        mock_parser.parse_stdout.return_value = AgentSessionResult(
            success=True,
            exit_code=0,
            backend_name="test",
            elapsed_seconds=0.0,
            session_id="s1",
            output="done",
            error="",
            raw={
                "tool_uses": [{"name": "CustomWrite", "id": "t1", "file_path": "/tmp/plan.md"}],
            },
        )
        hints = _extract_missing_token_hints(
            "",
            expected_output_patterns=[r"plan_path\s*=\s*/.+"],
            result_parser=mock_parser,
            write_tool_names=frozenset({"CustomWrite"}),
        )
        assert len(hints) > 0

    def test_extract_hints_ignores_claude_names_when_write_tool_names_empty(self):
        stdout = (
            _tool_use_ndjson("Write", file_path="/tmp/plan.md")
            + "\n"
            + _success_result_json("done")
        )
        hints = _extract_missing_token_hints(
            stdout,
            expected_output_patterns=[r"plan_path\s*=\s*/.+"],
            result_parser=ClaudeResultParser(),
            write_tool_names=frozenset(),
        )
        assert len(hints) == 0


class TestComputeWriteEvidenceCodex:
    """Codex file_changes path: _compute_write_evidence with file_changes parameter."""

    def test_single_file_change_has_evidence(self):

        session = ClaudeSessionResult(
            subtype=CliSubtype.SUCCESS,
            is_error=False,
            result="done",
            session_id="s1",
            tool_uses=[],
        )
        backend = Mock()
        backend.write_tool_names.return_value = frozenset()
        evidence = _compute_write_evidence(
            session,
            False,
            False,
            backend=backend,
            file_changes=["src/foo.py"],
        )
        assert evidence.has_evidence is True
        assert evidence.file_changes_count == 1

    def test_multiple_file_changes_has_evidence(self):

        session = ClaudeSessionResult(
            subtype=CliSubtype.SUCCESS,
            is_error=False,
            result="done",
            session_id="s1",
            tool_uses=[],
        )
        backend = Mock()
        backend.write_tool_names.return_value = frozenset()
        evidence = _compute_write_evidence(
            session,
            False,
            False,
            backend=backend,
            file_changes=["a.py", "b.py", "c.py"],
        )
        assert evidence.has_evidence is True
        assert evidence.file_changes_count == 3

    def test_empty_file_changes_no_write_tools_no_evidence(self):

        session = ClaudeSessionResult(
            subtype=CliSubtype.SUCCESS,
            is_error=False,
            result="done",
            session_id="s1",
            tool_uses=[],
        )
        backend = Mock()
        backend.write_tool_names.return_value = frozenset()
        evidence = _compute_write_evidence(
            session,
            False,
            False,
            backend=backend,
            file_changes=[],
        )
        assert evidence.has_evidence is False
        assert evidence.file_changes_count == 0


class TestParseStdout:
    def test_claude_backend_calls_parse_session_result(self):
        """_parse_stdout with ClaudeCodeBackend returns the same result as parse_session_result."""
        from autoskillit.execution.session import parse_session_result

        stdout = _success_session_json("test result")
        result = _parse_stdout(stdout, backend=ClaudeCodeBackend())
        expected = parse_session_result(stdout)
        assert result.result == expected.result
        assert result.session_id == expected.session_id
        assert result.session_complete == expected.session_complete

    def test_default_backend_returns_claude_session_result(self):
        """_parse_stdout with no backend arg returns a ClaudeSessionResult."""

        stdout = _success_session_json("test result")
        result = _parse_stdout(stdout, ClaudeCodeBackend())
        assert isinstance(result, ClaudeSessionResult)
        assert result.result == "test result"

    def test_parse_stdout_accepts_backend_kwarg(self):
        """_parse_stdout accepts an optional backend keyword argument."""

        mock_backend = Mock()
        mock_backend.name = AGENT_BACKEND_CLAUDE_CODE
        stdout = _success_session_json("test result")
        result = _parse_stdout(stdout, backend=mock_backend)
        assert isinstance(result, ClaudeSessionResult)
        assert result.result == "test result"

    def test_parse_stdout_with_backend_matches_fallback(self):
        """_parse_stdout with backend produces same result as without."""

        mock_backend = Mock()
        mock_backend.name = AGENT_BACKEND_CLAUDE_CODE
        stdout = _success_session_json("test result")
        with_backend = _parse_stdout(stdout, backend=mock_backend)
        without_backend = _parse_stdout(stdout, ClaudeCodeBackend())
        assert with_backend.result == without_backend.result
        assert with_backend.session_id == without_backend.session_id
        assert with_backend.session_complete == without_backend.session_complete

    def test_parse_stdout_claude_code_backend_falls_through(self):
        """ClaudeCodeBackend backend falls through to parse_session_result."""
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.execution.session import parse_session_result

        stdout = _success_session_json("test result")
        result = _parse_stdout(stdout, backend=ClaudeCodeBackend())
        expected = parse_session_result(stdout)
        assert result.result == expected.result
        assert result.session_id == expected.session_id
        assert result.session_complete == expected.session_complete

    def test_parse_stdout_non_claude_backend_dispatches_through_result_parser(self):
        """Non-Claude backend dispatches through result_parser().parse_stdout()."""

        mock_backend = Mock()
        mock_backend.name = "not-claude-code"
        parser = mock_backend.result_parser.return_value
        parser.parse_stdout.return_value = AgentSessionResult(
            success=True,
            exit_code=0,
            backend_name="not-claude-code",
            elapsed_seconds=0.0,
            session_id="s1",
            output="adapter output",
            error="",
            raw={"subtype": "success", "is_error": False, "stop_reasons": []},
        )

        stdout = _success_session_json("test result")
        result = _parse_stdout(stdout, backend=mock_backend)
        mock_backend.result_parser.return_value.parse_stdout.assert_called_once_with(stdout)
        assert isinstance(result, ClaudeSessionResult)
        assert result.result == "adapter output"

    def test_parse_stdout_codex_backend_dispatches_through_adapter(self, monkeypatch):
        """CodexBackend dispatches through _adapt_agent_result (non-Claude path)."""
        from autoskillit.execution.headless import _headless_result
        from autoskillit.execution.headless._headless_result import _parse_stdout

        spy = Mock(wraps=_headless_result._adapt_agent_result)
        monkeypatch.setattr(_headless_result, "_adapt_agent_result", spy)

        stdout = _success_session_json("test result")
        result = _parse_stdout(stdout, backend=CodexBackend())
        spy.assert_called_once()
        (agent_result,), _ = spy.call_args
        assert isinstance(agent_result, AgentSessionResult)
        assert isinstance(result, ClaudeSessionResult)
        assert result.result is not None


class TestStaleRecoveryWriteEvidence:
    def test_stale_recovery_carries_write_evidence(self):
        stdout = (
            _tool_use_ndjson("Edit", file_path="/a/b.py", old_string="a", new_string="b")
            + "\n"
            + _success_result_json("done")
        )
        result = _stale_result(kill_reason=KillReason.NATURAL_EXIT, stdout=stdout)
        sr = _build_skill_result(result, completion_marker="done", backend=ClaudeCodeBackend())
        assert sr.evidence.write_call_count > 0, (
            "STALE recovery must propagate write evidence from parsed stdout"
        )

    def test_stale_failure_carries_write_evidence(self):
        stdout = _tool_use_ndjson("Edit", file_path="/a/b.py", old_string="a", new_string="b")
        result = _stale_result(kill_reason=KillReason.NATURAL_EXIT, stdout=stdout)
        sr = _build_skill_result(result, backend=ClaudeCodeBackend())
        assert sr.evidence.write_call_count > 0, (
            "STALE failure must propagate write evidence from parsed stdout"
        )


class TestIdleStallRecoveryWriteEvidence:
    def test_idle_stall_recovery_carries_write_evidence(self):
        stdout = (
            _tool_use_ndjson("Edit", file_path="/a/b.py", old_string="a", new_string="b")
            + "\n"
            + _success_result_json("done")
        )
        result = _idle_stall_result_with_kill(kill_reason=KillReason.NATURAL_EXIT, stdout=stdout)
        sr = _build_skill_result(result, completion_marker="done", backend=ClaudeCodeBackend())
        assert sr.evidence.write_call_count > 0, (
            "IDLE_STALL recovery must propagate write evidence from parsed stdout"
        )

    def test_idle_stall_failure_carries_write_evidence(self):
        stdout = _tool_use_ndjson("Edit", file_path="/a/b.py", old_string="a", new_string="b")
        result = _idle_stall_result_with_kill(kill_reason=KillReason.NATURAL_EXIT, stdout=stdout)
        sr = _build_skill_result(result, backend=ClaudeCodeBackend())
        assert sr.evidence.write_call_count > 0, (
            "IDLE_STALL failure must propagate write evidence from parsed stdout"
        )


def _truncated_tool_use_line() -> str:
    """Truncated NDJSON containing '"Edit"' that fails to parse — simulates a parse failure."""
    return '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit","id":"t1"'


def _system_init_ndjson(tools: list[str] | None = None) -> str:
    """Return a realistic system/init NDJSON line listing tools in a manifest array."""
    if tools is None:
        tools = ["Read", "Write", "Edit", "Bash", "Grep", "Glob", "Agent", "Task", "WebSearch"]
    return json.dumps(
        {
            "type": "system",
            "subtype": "init",
            "session_id": "test-session-id",
            "tools": [{"name": t, "type": "tool"} for t in tools],
        }
    )


@pytest.mark.parametrize(
    "stdout,expected",
    [
        # system/init only — must not match
        pytest.param(
            _system_init_ndjson(),
            False,
            id="system_init_only",
        ),
        # assistant/tool_use with Edit — must match
        pytest.param(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "tool_use", "name": "Edit", "id": "t1", "input": {}}]
                    },
                }
            ),
            True,
            id="assistant_edit_tool_use",
        ),
        # assistant/tool_use with Read only — must not match
        pytest.param(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "tool_use", "name": "Read", "id": "t1", "input": {}}]
                    },
                }
            ),
            False,
            id="assistant_read_only",
        ),
        # system/init + assistant/tool_use with Edit — must match
        pytest.param(
            _system_init_ndjson()
            + "\n"
            + json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "tool_use", "name": "Edit", "id": "t1", "input": {}}]
                    },
                }
            ),
            True,
            id="init_plus_assistant_edit",
        ),
        # empty string — must not match
        pytest.param("", False, id="empty_string"),
        # parseable assistant with text content mentioning "Edit" — no tool_use block → False
        pytest.param(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": 'I will use "Edit" to change it'}]
                    },
                }
            ),
            False,
            id="assistant_text_mentions_edit_no_tool_use",
        ),
        # truncated assistant record that fails JSON parse, containing "Edit" as tool name → True
        pytest.param(
            _truncated_tool_use_line(),
            True,
            id="truncated_assistant_edit",
        ),
    ],
)
def test_stdout_mentions_write_tools_unit(stdout: str, expected: bool) -> None:
    assert _stdout_mentions_write_tools(stdout) is expected


class TestWriteEvidenceCrossCheck:
    def test_cross_check_logs_mismatch(self) -> None:
        result_line = _success_result_json()
        result = _sr(
            0,
            _truncated_tool_use_line() + "\n" + result_line,
            "",
            TerminationReason.NATURAL_EXIT,
        )

        from autoskillit.execution.headless import _headless_result

        cap = structlog.testing.CapturingLogger()
        with unittest.mock.patch.object(_headless_result, "logger", cap):
            _build_skill_result(result, backend=ClaudeCodeBackend())

        assert any(
            call.method_name == "warning"
            and call.args
            and "write_call_count_cross_check_mismatch" in call.args[0]
            for call in cap.calls
        ), "Cross-check must warn when count=0 but stdout contains write tool names"

    def test_cross_check_corrects_evidence_when_stdout_mentions_write_tools(self) -> None:
        result_line = _success_result_json()
        result = _sr(
            0,
            _truncated_tool_use_line() + "\n" + result_line,
            "",
            TerminationReason.NATURAL_EXIT,
        )
        sr = _build_skill_result(result, backend=ClaudeCodeBackend())
        assert sr.subtype != "zero_writes"
        assert sr.evidence.write_call_count >= 1

    def test_cross_check_sets_has_write_evidence(self) -> None:
        result_line = _success_result_json()
        result = _sr(
            0,
            _truncated_tool_use_line() + "\n" + result_line,
            "",
            TerminationReason.NATURAL_EXIT,
        )
        sr = _build_skill_result(
            result,
            write_behavior=WriteBehaviorSpec(mode="always"),
            backend=ClaudeCodeBackend(),
        )
        assert sr.success is True

    def test_rate_limit_session_produces_rate_limited_retry_reason(self) -> None:
        """api_error_status=429 → retry_reason=RATE_LIMITED (not RESUME or EARLY_STOP)."""
        result_line = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "result": "Rate limited",
                "session_id": "s1",
                "is_error": False,
                "api_error_status": 429,
            }
        )
        result = _sr(
            0,
            result_line,
            "",
            TerminationReason.NATURAL_EXIT,
        )
        sr = _build_skill_result(result, completion_marker="DONE", backend=ClaudeCodeBackend())
        assert sr.retry_reason != RetryReason.EARLY_STOP
        assert sr.retry_reason == RetryReason.RATE_LIMITED

    def test_stale_session_api_error_status_429_classified_as_rate_limited(self) -> None:
        """STALE session with api_error_status=429 → infra.exit_category=rate_limited."""
        result_line = json.dumps(
            {
                "type": "result",
                "subtype": "empty_output",
                "result": "",
                "session_id": "s1",
                "is_error": True,
                "api_error_status": 429,
            }
        )
        result = _stale_result(stdout=result_line)
        sr = _build_skill_result(result, backend=ClaudeCodeBackend())
        assert sr.infra.exit_category == "rate_limited"
        assert sr.retry_reason == RetryReason.RATE_LIMITED

    def test_idle_stall_session_api_error_status_429_classified_as_rate_limited(self) -> None:
        """IDLE_STALL session with api_error_status=429 → infra.exit_category=rate_limited."""
        result_line = json.dumps(
            {
                "type": "result",
                "subtype": "empty_output",
                "result": "",
                "session_id": "s1",
                "is_error": True,
                "api_error_status": 429,
            }
        )
        result = _idle_stall_result(result_line)
        sr = _build_skill_result(result, backend=ClaudeCodeBackend())
        assert sr.infra.exit_category == "rate_limited"
        assert sr.retry_reason == RetryReason.RATE_LIMITED

    def test_cross_check_ignores_init_manifest(self) -> None:
        stdout = _system_init_ndjson() + "\n" + _success_result_json()
        result = _sr(0, stdout, "", TerminationReason.NATURAL_EXIT)
        sr = _build_skill_result(result, backend=ClaudeCodeBackend())
        assert sr.evidence.write_call_count == 0, (
            "system/init tool manifest must not trigger write-tool cross-check"
        )
        assert sr.retry_reason != RetryReason.CONTRACT_RECOVERY

    def test_cross_check_detects_real_write_in_assistant_record(self) -> None:
        stdout = (
            _system_init_ndjson()
            + "\n"
            + _truncated_tool_use_line()
            + "\n"
            + _success_result_json()
        )
        result = _sr(0, stdout, "", TerminationReason.NATURAL_EXIT)
        sr = _build_skill_result(result, backend=ClaudeCodeBackend())
        assert sr.evidence.write_call_count >= 1, (
            "truncated assistant/tool_use with 'Edit' must still trigger cross-check "
            "even when a system/init line also appears in stdout"
        )

    def test_contract_recovery_suppressed_for_read_only(self) -> None:
        from autoskillit.pipeline.audit import DefaultAuditLog

        # Produce adjudicated_failure: success subtype + completion_marker present in result
        # but expected_output_patterns not matched.
        result_line = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "result": "plan summary\n%%ORDER_UP%%",
                "session_id": "s1",
                "is_error": False,
            }
        )
        write_tool_line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "name": "Write", "id": "t1", "input": {}}]
                },
            }
        )
        stdout = write_tool_line + "\n" + result_line
        result = _sr(0, stdout, "", TerminationReason.NATURAL_EXIT)
        audit = DefaultAuditLog()

        sr = _build_skill_result(
            result,
            completion_marker="%%ORDER_UP%%",
            expected_output_patterns=[r"plan_path\s*=\s*/.+"],
            skill_command="/test:read-only-skill",
            audit=audit,
            readonly_skill=True,
            backend=ClaudeCodeBackend(),
        )
        assert sr.retry_reason != RetryReason.CONTRACT_RECOVERY
        assert sr.needs_retry is False
        contract_recovery_entries = [
            f
            for f in audit.get_report()
            if f.skill_command == "/test:read-only-skill" and f.retry_reason == "contract_recovery"
        ]
        assert not contract_recovery_entries, (
            "Audit log must not record CONTRACT_RECOVERY for read-only skills"
        )


class TestCodexPipelineHappyPath:
    """Codex NDJSON happy-path through _parse_stdout and _build_skill_result."""

    def test_parse_stdout_with_codex_backend(self):
        content = fixture_path(HAPPY_PATH_SINGLE_TURN).read_text()
        session = _parse_stdout(content, backend=CodexBackend())
        assert session.session_id == "thread_hp_abc123"
        assert session.is_error is False
        assert session.token_usage is not None
        assert "input_tokens" in session.token_usage
        assert "output_tokens" in session.token_usage
        assert session.token_usage["input_tokens"] == 150
        assert session.token_usage["output_tokens"] == 75
        assert session.token_usage["cache_read_tokens"] == 30

    def test_happy_path_pipeline(self):
        content = fixture_path(HAPPY_PATH_SINGLE_TURN).read_text()
        result = _codex_subprocess_result(content)
        sr = _build_skill_result(
            result,
            backend=CodexBackend(),
            supports_claude_format_stdout=False,
        )
        assert sr.success is True
        assert sr.session_id == "thread_hp_abc123"
        assert sr.is_error is False
        assert sr.token_usage is not None

    def test_subtype_via_compute_outcome(self):
        content = fixture_path(HAPPY_PATH_SINGLE_TURN).read_text()
        backend = CodexBackend()
        session = _parse_stdout(content, backend=backend)
        result = _codex_subprocess_result(content)
        sr = _build_skill_result(
            result,
            backend=backend,
            supports_claude_format_stdout=False,
        )
        outcome, _ = _compute_outcome(session, 0, TerminationReason.NATURAL_EXIT)
        expected_subtype = session.normalize_subtype(outcome, "")
        assert sr.subtype == expected_subtype


class TestCodexPipelineTurnFailed:
    """Codex NDJSON turn_failed_error through _build_skill_result."""

    @pytest.fixture(autouse=True)
    def _patch_parse_stdout(self, monkeypatch):
        from autoskillit.execution.headless import _headless_result

        monkeypatch.setattr(_headless_result, "_parse_stdout", _make_codex_parse_stdout())
        self._content = fixture_path(TURN_FAILED_ERROR).read_text()
        self._result = _codex_subprocess_result(
            self._content,
            returncode=1,
            termination=TerminationReason.NATURAL_EXIT,
            kill_reason=KillReason.NATURAL_EXIT,
        )

    def _build(self) -> SkillResult:
        return _build_skill_result(
            self._result, supports_claude_format_stdout=False, backend=CodexBackend()
        )

    def test_failure_success_is_false(self):
        sr = self._build()
        assert sr.success is False

    def test_failure_session_id(self):
        sr = self._build()
        assert sr.session_id == "thread_fail_001"

    def test_failure_is_error(self):
        sr = self._build()
        assert sr.is_error is True

    def test_failure_subtype(self):
        sr = self._build()
        assert sr.subtype == "error_during_execution"

    def test_failure_needs_retry(self):
        sr = self._build()
        assert sr.needs_retry is True

    def test_failure_token_usage_none(self):
        sr = self._build()
        assert sr.token_usage is None


class TestCodexPipelineTerminationBranches:
    """STALE and IDLE_STALL branches with Codex NDJSON fixtures."""

    @pytest.fixture(autouse=True)
    def _patch_parse_stdout(self, monkeypatch):
        from autoskillit.execution.headless import _headless_result

        monkeypatch.setattr(_headless_result, "_parse_stdout", _make_codex_parse_stdout())

    def _happy_content(self) -> str:
        return fixture_path(HAPPY_PATH_SINGLE_TURN).read_text()

    def _error_content(self) -> str:
        return fixture_path(TURN_FAILED_ERROR).read_text()

    def test_stale_codex_happy_path_with_backend_succeeds(self):
        result = _codex_subprocess_result(
            self._happy_content(),
            returncode=-1,
            termination=TerminationReason.STALE,
            kill_reason=KillReason.INFRA_KILL,
        )
        sr = _build_skill_result(
            result,
            completion_marker="%%ORDER_UP%%",
            supports_claude_format_stdout=False,
            backend=CodexBackend(),
        )
        assert sr.success is True

    def test_stale_codex_empty_stdout_fails(self):
        result = _codex_subprocess_result(
            "",
            returncode=-1,
            termination=TerminationReason.STALE,
            kill_reason=KillReason.INFRA_KILL,
        )
        sr = _build_skill_result(
            result, supports_claude_format_stdout=False, backend=CodexBackend()
        )
        assert sr.success is False

    def test_stale_codex_error_fixture_fails(self):
        result = _codex_subprocess_result(
            self._error_content(),
            returncode=-1,
            termination=TerminationReason.STALE,
            kill_reason=KillReason.INFRA_KILL,
        )
        sr = _build_skill_result(
            result, supports_claude_format_stdout=False, backend=CodexBackend()
        )
        assert sr.success is False

    def test_idle_stall_codex_happy_path_with_backend_succeeds(self):
        result = _codex_subprocess_result(
            self._happy_content(),
            returncode=-1,
            termination=TerminationReason.IDLE_STALL,
            kill_reason=KillReason.INFRA_KILL,
        )
        sr = _build_skill_result(
            result,
            completion_marker="%%ORDER_UP%%",
            supports_claude_format_stdout=False,
            backend=CodexBackend(),
        )
        assert sr.success is True

    def test_idle_stall_codex_empty_stdout_fails(self):
        result = _codex_subprocess_result(
            "",
            returncode=-1,
            termination=TerminationReason.IDLE_STALL,
            kill_reason=KillReason.INFRA_KILL,
        )
        sr = _build_skill_result(
            result, supports_claude_format_stdout=False, backend=CodexBackend()
        )
        assert sr.success is False

    def test_idle_stall_codex_error_fixture_fails(self):
        result = _codex_subprocess_result(
            self._error_content(),
            returncode=-1,
            termination=TerminationReason.IDLE_STALL,
            kill_reason=KillReason.INFRA_KILL,
        )
        sr = _build_skill_result(
            result, supports_claude_format_stdout=False, backend=CodexBackend()
        )
        assert sr.success is False


class TestStaleApiRetryExhaustion:
    """T5: stale/idle_stall + api_retry exhaustion → infra_exit_category=api_error."""

    def test_stale_with_api_retry_exhaustion_sets_infra_exit_category(self):
        """Stale + exhausted api_retry → infra_exit_category='api_error', exhausted=True."""
        ndjson = "\n".join(
            [
                json.dumps(
                    {
                        "type": "system",
                        "subtype": "api_retry",
                        "error": "overloaded",
                        "error_status": 529,
                        "attempt": 10,
                        "max_retries": 10,
                    }
                ),
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "empty_output",
                        "is_error": True,
                        "result": "",
                        "session_id": "s1",
                    }
                ),
            ]
        )
        result = _sr(0, ndjson, "", TerminationReason.STALE)
        sr = _build_skill_result(result, backend=ClaudeCodeBackend())
        assert sr.success is False
        assert sr.infra.exit_category == "api_error"
        assert sr.api_retry.exhausted is True
        assert sr.api_retry.count == 1

    def test_stale_without_api_retry_has_empty_infra(self):
        """Stale with no api_retry → infra_exit_category='', count=0."""
        ndjson = json.dumps(
            {
                "type": "result",
                "subtype": "empty_output",
                "is_error": True,
                "result": "",
                "session_id": "s1",
            }
        )
        result = _sr(0, ndjson, "", TerminationReason.STALE)
        sr = _build_skill_result(result, backend=ClaudeCodeBackend())
        assert sr.success is False
        assert sr.infra.exit_category == ""
        assert sr.api_retry.count == 0

    def test_stale_recovery_with_api_retry_does_not_set_infra_error(self):
        """Stale + exhausted api_retry BUT valid result → recovered, infra='' (not api_error)."""
        ndjson = "\n".join(
            [
                json.dumps(
                    {
                        "type": "system",
                        "subtype": "api_retry",
                        "error": "overloaded",
                        "error_status": 529,
                        "attempt": 10,
                        "max_retries": 10,
                    }
                ),
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "Done.",
                        "session_id": "s1",
                    }
                ),
            ]
        )
        result = _sr(0, ndjson, "", TerminationReason.STALE)
        sr = _build_skill_result(result, backend=ClaudeCodeBackend())
        assert sr.success is True
        assert sr.subtype == "recovered_from_stale"
        assert sr.infra.exit_category == ""
        assert sr.api_retry.exhausted is True

    def test_idle_stall_with_api_retry_exhaustion_sets_infra(self):
        """Idle_stall + exhausted api_retry → infra_exit_category='api_error'."""
        ndjson = "\n".join(
            [
                json.dumps(
                    {
                        "type": "system",
                        "subtype": "api_retry",
                        "error": "overloaded",
                        "error_status": 529,
                        "attempt": 10,
                        "max_retries": 10,
                    }
                ),
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "empty_output",
                        "is_error": True,
                        "result": "",
                        "session_id": "s1",
                    }
                ),
            ]
        )
        result = _sr(0, ndjson, "", TerminationReason.IDLE_STALL)
        sr = _build_skill_result(result, backend=ClaudeCodeBackend())
        assert sr.success is False
        assert sr.infra.exit_category == "api_error"
        assert sr.api_retry.exhausted is True


class TestNormalApiRetry:
    """T10: normal-path SkillResult carries api_retry data."""

    def test_normal_completion_with_api_retry_carries_data(self):
        """Normal exit with non-exhausted api_retry → api_retry data preserved."""
        ndjson = "\n".join(
            [
                json.dumps(
                    {
                        "type": "system",
                        "subtype": "api_retry",
                        "error": "unknown",
                        "error_status": None,
                        "attempt": 3,
                        "max_retries": 10,
                    }
                ),
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "Done.",
                        "session_id": "s1",
                    }
                ),
            ]
        )
        result = _sr(0, ndjson, "", TerminationReason.NATURAL_EXIT)
        sr = _build_skill_result(result, backend=ClaudeCodeBackend())
        assert sr.success is True
        assert sr.api_retry.count == 1
        assert sr.api_retry.exhausted is False
        assert sr.api_retry.last_error == "unknown"


class TestExtractFileChanges:
    def test_extract_file_changes_returns_empty_for_claude_backend(self) -> None:
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.execution.headless._headless_evidence import _extract_file_changes

        assert _extract_file_changes("", ClaudeCodeBackend()) == []

    def test_extract_file_changes_extracts_from_codex_stdout(self) -> None:
        import json as _json

        from autoskillit.execution.backends.codex import CodexBackend
        from autoskillit.execution.headless._headless_evidence import _extract_file_changes

        lines = [
            _json.dumps(
                {"type": "item.completed", "item": {"type": "file_change", "path": "/a.py"}}
            ),
            _json.dumps(
                {"type": "item.completed", "item": {"type": "file_change", "path": "/b.py"}}
            ),
            _json.dumps(
                {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 50}}
            ),
        ]
        stdout = "\n".join(lines)
        backend = CodexBackend()
        result = _extract_file_changes(stdout, backend)
        assert result == ["/a.py", "/b.py"]


class TestComputeWriteEvidenceWithFileChanges:
    def test_compute_write_evidence_sets_file_changes_count_when_no_write_calls(self) -> None:

        session = ClaudeSessionResult(
            subtype=CliSubtype.SUCCESS,
            is_error=False,
            result="done",
            session_id="s1",
            tool_uses=[],
        )
        evidence = _compute_write_evidence(
            session, False, False, backend=ClaudeCodeBackend(), file_changes=["a.py", "b.py"]
        )
        assert evidence.file_changes_count == 2
        assert evidence.has_evidence is True

    def test_compute_write_evidence_ignores_file_changes_when_write_calls_exist(self) -> None:

        session = ClaudeSessionResult(
            subtype=CliSubtype.SUCCESS,
            is_error=False,
            result="done",
            session_id="s1",
            tool_uses=[{"name": "Write", "id": "t1"}],
        )
        evidence = _compute_write_evidence(
            session, False, False, backend=ClaudeCodeBackend(), file_changes=["a.py"]
        )
        assert evidence.file_changes_count == 0
        assert evidence.write_call_count == 1


def test_build_skill_result_ansi_only_stdout_exit_143() -> None:
    """ANSI-only stdout + exit 143 produces success=False, lifespan_started=False."""
    ANSI_TUI_CLEANUP = (
        "\x1b[?1006l\x1b[?1003l\x1b[?1002l\x1b[?1000l"
        "\x1b[>4m\x1b[<u\x1b[?1004l\x1b[?2031l\x1b[?2004l"
        "\x1b[?25h\x1b7\x1b[r\x1b8\x1b]0;\x07\x1b[?25h"
    )
    proc_result = SubprocessResult(
        returncode=143,
        stdout=ANSI_TUI_CLEANUP,
        stderr="",
        termination=TerminationReason.NATURAL_EXIT,
        pid=12345,
    )
    skill_result = _build_skill_result(
        result=proc_result,
        backend=ClaudeCodeBackend(),
        skill_command="test",
        completion_marker="%%DONE%%",
    )
    assert skill_result.success is False
    assert skill_result.lifespan_started is False


def test_build_skill_result_signal_death_returncode_yields_resume() -> None:
    """SIGNAL_DEATH + rc=143 yields retry_reason=RESUME and exit_category=process_killed."""
    proc_result = SubprocessResult(
        returncode=143,
        stdout="",
        stderr="",
        termination=TerminationReason.SIGNAL_DEATH,
        pid=12345,
        kill_reason=KillReason.NATURAL_EXIT,
    )
    skill_result = _build_skill_result(
        result=proc_result,
        backend=ClaudeCodeBackend(),
        skill_command="test",
        completion_marker="%%DONE%%",
    )
    assert skill_result.success is False
    assert skill_result.retry_reason == RetryReason.RESUME
    assert skill_result.infra.exit_category == "process_killed"
