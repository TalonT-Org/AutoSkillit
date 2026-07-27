"""Tests for _headless_recovery._attempt_contract_nudge pty_mode propagation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from autoskillit.core import CmdSpec, PluginLoadMode, RetryReason, SkillResult
from autoskillit.core.types import KillReason, SubprocessResult, TerminationReason
from autoskillit.core.types._type_results import WriteEvidence

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestNudgePtyMode:
    """_attempt_contract_nudge propagates pty_mode to the runner."""

    @pytest.mark.anyio
    async def test_nudge_passes_pty_mode_for_claude_backend(self, tmp_path: Path) -> None:
        """_attempt_contract_nudge passes pty_mode=True to runner for ClaudeCode backend."""
        from autoskillit.execution.headless._headless_recovery import _attempt_contract_nudge
        from tests.execution.conftest import _mock_backend
        from tests.fakes import MockSubprocessRunner

        marker = "%%NUDGE_DONE%%"

        mock_runner = MockSubprocessRunner()
        # The nudge runner will return a result whose "stdout" we can parse
        mock_runner.set_default(
            SubprocessResult(
                returncode=0,
                stdout="",
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=0,
            )
        )

        backend = _mock_backend(pty_required=True, session_resume_capable=True)

        result_parser = Mock()
        parsed_session = Mock()
        parsed_session.output = marker
        parsed_session.raw = {}
        result_parser.parse_stdout.return_value = parsed_session

        skill_result = SkillResult(
            success=False,
            result="",
            session_id="test-session",
            subtype="empty_output",
            is_error=False,
            exit_code=0,
            needs_retry=True,
            retry_reason=RetryReason.CONTRACT_RECOVERY,
            stderr="",
            kill_reason=KillReason.NATURAL_EXIT,
            evidence=WriteEvidence.none_observed(),
        )

        subprocess_result = SubprocessResult(
            returncode=0,
            stdout="",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=0,
        )

        await _attempt_contract_nudge(
            skill_result=skill_result,
            subprocess_result=subprocess_result,
            expected_output_patterns=[],
            completion_marker=marker,
            cwd=str(tmp_path),
            runner=mock_runner,
            backend=backend,
            result_parser=result_parser,
            retry_reason=RetryReason.EARLY_STOP,
        )

        # After fix: runner must have been called with pty_mode=True
        assert mock_runner.last_pty_mode is True, (
            f"Expected last_pty_mode=True for ClaudeCode backend (pty_required=True), "
            f"got {mock_runner.last_pty_mode!r}"
        )

    @pytest.mark.anyio
    async def test_nudge_respects_pty_override_false(self, tmp_path: Path) -> None:
        """_attempt_contract_nudge with pty_override=False uses pty_mode=False."""
        from autoskillit.execution.headless._headless_recovery import _attempt_contract_nudge
        from tests.execution.conftest import _mock_backend
        from tests.fakes import MockSubprocessRunner

        marker = "%%NUDGE_DONE%%"

        mock_runner = MockSubprocessRunner()
        mock_runner.set_default(
            SubprocessResult(
                returncode=0,
                stdout="",
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=0,
            )
        )

        backend = _mock_backend(pty_required=True, session_resume_capable=True)

        result_parser = Mock()
        parsed_session = Mock()
        parsed_session.output = marker
        parsed_session.raw = {}
        result_parser.parse_stdout.return_value = parsed_session

        skill_result = SkillResult(
            success=False,
            result="",
            session_id="test-session",
            subtype="empty_output",
            is_error=False,
            exit_code=0,
            needs_retry=True,
            retry_reason=RetryReason.CONTRACT_RECOVERY,
            stderr="",
            kill_reason=KillReason.NATURAL_EXIT,
            evidence=WriteEvidence.none_observed(),
        )

        subprocess_result = SubprocessResult(
            returncode=0,
            stdout="",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=0,
        )

        await _attempt_contract_nudge(
            skill_result=skill_result,
            subprocess_result=subprocess_result,
            expected_output_patterns=[],
            completion_marker=marker,
            cwd=str(tmp_path),
            runner=mock_runner,
            backend=backend,
            result_parser=result_parser,
            retry_reason=RetryReason.EARLY_STOP,
            pty_override=False,
        )

        assert mock_runner.last_pty_mode is False, (
            f"Expected last_pty_mode=False when pty_override=False, "
            f"got {mock_runner.last_pty_mode!r}"
        )

    @pytest.mark.anyio
    async def test_nudge_acquires_an_independent_full_lifetime_binding(
        self, tmp_path: Path
    ) -> None:
        from autoskillit.execution.headless._headless_recovery import _attempt_contract_nudge
        from tests.execution.conftest import _mock_backend
        from tests.fakes import MockSubprocessRunner

        class Binding:
            inherited_fds = (91,)

            def __init__(self) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

        class Authority:
            def __init__(self) -> None:
                self.binding = Binding()
                self.request = None

            def acquire_launch_binding(self, *, backend, load_mode):
                self.request = (backend, load_mode)
                return self.binding

        marker = "%%NUDGE_DONE%%"
        runner = MockSubprocessRunner()
        runner.set_default(
            SubprocessResult(
                returncode=0,
                stdout="",
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=0,
            )
        )
        backend = _mock_backend(pty_required=False, session_resume_capable=True)
        backend.build_resume_cmd.return_value = CmdSpec(
            cmd=("claude", "--print", "--resume", "test-session"),
            env={},
            inherited_fds=(91,),
        )
        parser = Mock()
        parsed = Mock(output=marker, raw={})
        parser.parse_stdout.return_value = parsed
        authority = Authority()

        result = await _attempt_contract_nudge(
            skill_result=SkillResult(
                success=False,
                result="",
                session_id="test-session",
                subtype="empty_output",
                is_error=False,
                exit_code=0,
                needs_retry=True,
                retry_reason=RetryReason.EARLY_STOP,
                stderr="",
                kill_reason=KillReason.NATURAL_EXIT,
                evidence=WriteEvidence.none_observed(),
            ),
            subprocess_result=SubprocessResult(
                returncode=0,
                stdout="",
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=0,
            ),
            expected_output_patterns=[],
            completion_marker=marker,
            cwd=str(tmp_path),
            runner=runner,
            backend=backend,
            result_parser=parser,
            retry_reason=RetryReason.EARLY_STOP,
            plugin_authority=authority,
            plugin_load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
            provider_extras={"ANTHROPIC_API_KEY": "fallback"},
        )

        assert result is not None and result.success is True
        assert authority.request == (backend, PluginLoadMode.EXPLICIT_PLUGIN_DIR)
        assert authority.binding.closed is True
        call_kwargs = backend.build_resume_cmd.call_args.kwargs
        assert call_kwargs["plugin_binding"] is authority.binding
        assert call_kwargs["env_extras"]["ANTHROPIC_API_KEY"] == "fallback"
        assert runner.call_args_list[0][3]["pass_fds"] == (91,)


@pytest.mark.anyio
async def test_nudge_skips_for_block_delimiter_patterns(tmp_path: Path) -> None:
    """Block-delimited patterns (---{name}---) are not path-capture, so hints=[].

    _is_path_capture_pattern returns None for block delimiters (no path-capture token
    in the pattern, no '=' after the token). The for-loop in _extract_missing_token_hints
    skips such patterns, leaving hints empty. This means _attempt_contract_nudge (which
    short-circuits to None when hints=[]) cannot recover block-delimited skills — the
    caller always falls through to the failure path.
    """
    from autoskillit.execution.headless._headless_recovery import (
        _attempt_contract_nudge,
        _extract_missing_token_hints,
    )
    from tests.execution.conftest import _mock_backend
    from tests.fakes import MockSubprocessRunner

    result_parser = Mock()
    parsed_session = Mock()
    parsed_session.output = "irrelevant output text"
    parsed_session.raw = {"tool_uses": []}
    result_parser.parse_stdout.return_value = parsed_session

    hints = _extract_missing_token_hints(
        stdout="",
        expected_output_patterns=["---prepare-issue-result---"],
        result_parser=result_parser,
        write_tool_names=frozenset({"Write", "Edit"}),
    )

    assert hints == []

    # Verify _attempt_contract_nudge short-circuits to None when hints is empty
    skill_result = SkillResult(
        success=False,
        result="",
        session_id="test-session",
        subtype="empty_output",
        is_error=False,
        exit_code=0,
        needs_retry=True,
        retry_reason=RetryReason.CONTRACT_RECOVERY,
        stderr="",
        kill_reason=KillReason.NATURAL_EXIT,
        evidence=WriteEvidence.none_observed(),
    )
    subprocess_result = SubprocessResult(
        returncode=0,
        stdout="",
        stderr="",
        termination=TerminationReason.NATURAL_EXIT,
        pid=0,
    )
    backend = _mock_backend(pty_required=False, session_resume_capable=True)
    runner = MockSubprocessRunner()

    nudge_result = await _attempt_contract_nudge(
        skill_result=skill_result,
        subprocess_result=subprocess_result,
        expected_output_patterns=["---prepare-issue-result---"],
        completion_marker="%%NUDGE_DONE%%",
        cwd=str(tmp_path),
        runner=runner,
        backend=backend,
        result_parser=result_parser,
    )

    assert nudge_result is None
    assert result_parser.parse_stdout.call_count == 2
