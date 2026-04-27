"""Tests for headless_runner.py extracted helpers."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit.core._type_plugin_source import DirectInstall, MarketplaceInstall
from autoskillit.core.types import (
    CONTEXT_EXHAUSTION_MARKER,
    ChannelConfirmation,
    RetryReason,
    SkillResult,
    SubprocessResult,
    TerminationReason,
)
from autoskillit.execution.commands import _ensure_skill_prefix
from autoskillit.execution.headless import (
    _build_skill_result,
    _extract_missing_token_hints,
    _extract_worktree_path,
    _scan_jsonl_write_paths,
)
from tests.conftest import _make_result, _make_timeout_result

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_inject_completion_directive_appends_marker():
    from autoskillit.execution.commands import _inject_completion_directive

    result = _inject_completion_directive("/investigate foo", "%%DONE%%")
    assert "%%DONE%%" in result
    assert "/investigate foo" in result
    assert "ORCHESTRATION DIRECTIVE" in result


def _success_session_json(result_text: str) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "result": result_text,
            "session_id": "test-session",
            "is_error": False,
        }
    )


def _failed_session_json() -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "error",
            "result": "Task failed with an error",
            "session_id": "test-session",
            "is_error": True,
        }
    )


def _context_exhausted_session_json() -> str:
    """Session result that triggers context exhaustion / needs_retry detection."""
    return json.dumps(
        {
            "type": "result",
            "subtype": "error",
            "result": "prompt is too long",
            "session_id": "test-session",
            "is_error": True,
            "errors": ["prompt is too long"],
        }
    )


def _sr(
    returncode=0,
    stdout="",
    stderr="",
    termination=TerminationReason.NATURAL_EXIT,
    session_id: str = "",
    channel_b_session_id: str = "",
):
    """Build a minimal SubprocessResult for _build_skill_result tests."""
    return SubprocessResult(
        returncode,
        stdout,
        stderr,
        termination,
        pid=12345,
        session_id=session_id,
        channel_b_session_id=channel_b_session_id,
    )


class TestSessionLogDir:
    """Unit tests for _session_log_dir — path derivation and log emission."""

    # --- path derivation (from test_tools_execution.py TestSessionLogDir) ---

    def test_replaces_slashes(self):
        from autoskillit.execution.headless import _session_log_dir

        result = _session_log_dir("/home/user/project")
        assert result == Path.home() / ".claude" / "projects" / "-home-user-project"

    def test_replaces_underscores(self):
        from autoskillit.execution.headless import _session_log_dir

        result = _session_log_dir("/home/user/my_project")
        assert result == Path.home() / ".claude" / "projects" / "-home-user-my-project"

    def test_replaces_both_slashes_and_underscores(self):
        from autoskillit.execution.headless import _session_log_dir

        result = _session_log_dir("/home/user_name/my_project/sub_dir")
        assert (
            result == Path.home() / ".claude" / "projects" / "-home-user-name-my-project-sub-dir"
        )

    # --- log behavior (from test_server_init.py TestGateTransitionLogs) ---

    def test_warns_when_dir_missing(self, tmp_path, monkeypatch):
        import structlog.testing

        from autoskillit.execution.headless import _session_log_dir

        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        cwd = str(tmp_path / "my-project")
        with structlog.testing.capture_logs() as logs:
            _session_log_dir(cwd)
        assert any(
            e.get("event") == "session_log_dir_precreating"
            for e in logs
            if e.get("log_level") == "info"
        )

    def test_no_warning_when_dir_present(self, tmp_path, monkeypatch):
        import structlog.testing

        from autoskillit.execution.headless import _session_log_dir

        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        cwd = str(tmp_path)
        project_hash = cwd.replace("/", "-").replace("_", "-")
        log_dir = tmp_path / "home" / ".claude" / "projects" / project_hash
        log_dir.mkdir(parents=True, exist_ok=True)
        with structlog.testing.capture_logs() as logs:
            _session_log_dir(cwd)
        assert not any(e.get("event") == "session_log_dir_missing" for e in logs)

    def test_logs_path_when_dir_exists(self, tmp_path, monkeypatch):
        import structlog.testing

        from autoskillit.execution.headless import _session_log_dir

        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        cwd = str(tmp_path)
        project_hash = cwd.replace("/", "-").replace("_", "-")
        log_dir = tmp_path / "home" / ".claude" / "projects" / project_hash
        log_dir.mkdir(parents=True, exist_ok=True)
        with structlog.testing.capture_logs() as logs:
            result = _session_log_dir(cwd)
        info_entries = [e for e in logs if e.get("log_level") == "info"]
        assert any(e.get("event") == "session_log_dir_computed" for e in info_entries)
        computed = next(e for e in info_entries if e.get("event") == "session_log_dir_computed")
        assert computed.get("path") == str(result)
        assert not any(e.get("event") == "session_log_dir_missing" for e in logs)

    def test_logs_path_when_dir_missing(self, tmp_path, monkeypatch):
        import structlog.testing

        from autoskillit.execution.headless import _session_log_dir

        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        cwd = str(tmp_path / "my-project")
        with structlog.testing.capture_logs() as logs:
            result = _session_log_dir(cwd)
        info_entries = [e for e in logs if e.get("log_level") == "info"]
        assert any(e.get("event") == "session_log_dir_computed" for e in info_entries)
        computed = next(e for e in info_entries if e.get("event") == "session_log_dir_computed")
        assert computed.get("path") == str(result)
        assert any(e.get("event") == "session_log_dir_precreating" for e in info_entries)
        assert not any(e.get("event") == "session_log_dir_missing" for e in logs)

    def test_headless_session_log_dir_uses_shared_util(self):
        from autoskillit.core.paths import claude_code_project_dir
        from autoskillit.execution.headless import _session_log_dir

        cwd = "/home/user/project"
        assert _session_log_dir(cwd) == claude_code_project_dir(cwd)

    def test_session_log_dir_creates_missing_directory(self, tmp_path, monkeypatch):
        """_session_log_dir must create the directory if absent, so Channel B
        always has a directory to poll."""
        from autoskillit.execution.headless import _session_log_dir

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
        cwd = "/some/fresh/clone/path"
        result = _session_log_dir(cwd)
        assert result.exists()
        assert result.is_dir()


class TestResolveSessionId:
    """Unit tests for _resolve_skill_session_id — the session UUID resolution helper."""

    def test_prefers_session_session_id(self):
        """stdout-parsed session_id is preferred over Channel B when both present."""
        from autoskillit.execution.headless import _resolve_skill_session_id
        from autoskillit.execution.session import ClaudeSessionResult

        session = ClaudeSessionResult(
            session_id="from-stdout", subtype="success", is_error=False, result="", errors=[]
        )
        result = _sr(channel_b_session_id="from-channel-b")
        assert _resolve_skill_session_id(session, result) == "from-stdout"

    def test_falls_back_to_channel_b_when_session_empty(self):
        """Channel B UUID is used when stdout-parsed session_id is empty."""
        from autoskillit.execution.headless import _resolve_skill_session_id
        from autoskillit.execution.session import ClaudeSessionResult

        session = ClaudeSessionResult(
            session_id="", subtype="success", is_error=False, result="", errors=[]
        )
        result = _sr(channel_b_session_id="from-channel-b")
        assert _resolve_skill_session_id(session, result) == "from-channel-b"

    def test_returns_empty_when_both_empty(self):
        """Returns empty string when neither source has a session ID."""
        from autoskillit.execution.headless import _resolve_skill_session_id
        from autoskillit.execution.session import ClaudeSessionResult

        session = ClaudeSessionResult(
            session_id="", subtype="success", is_error=False, result="", errors=[]
        )
        result = _sr(channel_b_session_id="")
        assert _resolve_skill_session_id(session, result) == ""

    def test_handles_none_session(self):
        """When session is None, falls back to Channel B UUID."""
        from autoskillit.execution.headless import _resolve_skill_session_id

        result = _sr(channel_b_session_id="from-channel-b")
        assert _resolve_skill_session_id(None, result) == "from-channel-b"


class TestBuildSkillResult:
    """Coverage for _build_skill_result — the primary output-routing function."""

    def test_natural_exit_with_success_json_returns_success(self):
        """COMPLETED + valid type=result success JSON → success=True, needs_retry=False."""
        from autoskillit.execution.headless import _build_skill_result

        payload = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Task completed.",
                "session_id": "sess-abc",
            }
        )
        skill = _build_skill_result(_sr(stdout=payload))
        assert skill.success is True
        assert skill.needs_retry is False

    def test_timed_out_returns_failure_no_retry(self):
        """TIMED_OUT termination → success=False, needs_retry=False (timeout is non-retriable)."""
        from autoskillit.execution.headless import _build_skill_result

        skill = _build_skill_result(_sr(returncode=-1, termination=TerminationReason.TIMED_OUT))
        assert skill.success is False
        assert skill.needs_retry is False

    def test_stale_with_valid_result_in_stdout_recovers(self):
        """STALE termination + valid result JSON in stdout → recovered_from_stale."""
        from autoskillit.execution.headless import _build_skill_result

        payload = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Recovered output.",
                "session_id": "sess-stale",
            }
        )
        skill = _build_skill_result(
            _sr(returncode=-15, stdout=payload, termination=TerminationReason.STALE)
        )
        assert skill.success is True
        assert skill.subtype == "recovered_from_stale"

    def test_stale_with_empty_stdout_returns_failure_and_retry(self):
        """STALE termination + no result in stdout → success=False, needs_retry=True."""
        from autoskillit.execution.headless import _build_skill_result

        skill = _build_skill_result(
            _sr(returncode=-15, stdout="", termination=TerminationReason.STALE)
        )
        assert skill.success is False
        assert skill.needs_retry is True

    def test_build_skill_result_stale_path_uses_channel_b_session_id(self):
        """_build_skill_result must populate session_id from Channel B on stale path."""
        from autoskillit.execution.headless import _build_skill_result

        result = SubprocessResult(
            returncode=1,
            stdout="",
            stderr="",
            termination=TerminationReason.STALE,
            pid=12345,
            channel_b_session_id="b077addc-926d-4869-b27a-7465a4c0fda4",
        )
        skill_result = _build_skill_result(
            result,
            completion_marker="ORDER_UP",
            skill_command="/autoskillit:review-approach",
            audit=None,
            expected_output_patterns=[],
            cwd="/tmp",
        )
        assert skill_result.session_id == "b077addc-926d-4869-b27a-7465a4c0fda4"

    def test_build_skill_result_idle_stall_is_retriable(self):
        """IDLE_STALL termination → success=False, needs_retry=True, subtype=idle_stall."""
        from autoskillit.execution.headless import _build_skill_result

        skill = _build_skill_result(
            _sr(returncode=-15, stdout="", termination=TerminationReason.IDLE_STALL)
        )
        assert skill.success is False
        assert skill.needs_retry is True
        assert skill.subtype == "idle_stall"

    def test_build_skill_result_timeout_empty_stdout_uses_channel_b_session_id(self):
        """_build_skill_result must use Channel B session_id on TIMED_OUT with empty stdout."""
        from autoskillit.execution.headless import _build_skill_result

        result = SubprocessResult(
            returncode=1,
            stdout="",
            stderr="",
            termination=TerminationReason.TIMED_OUT,
            pid=12345,
            channel_b_session_id="c1ee2a00-1234-5678-abcd-deadbeefcafe",
        )
        skill_result = _build_skill_result(
            result,
            completion_marker="ORDER_UP",
            skill_command="/autoskillit:implement-worktree",
            audit=None,
            expected_output_patterns=[],
            cwd="/tmp",
        )
        assert skill_result.session_id == "c1ee2a00-1234-5678-abcd-deadbeefcafe"
        assert skill_result.success is False

    def test_build_skill_result_prefers_stdout_session_id_over_channel_b(self):
        """stdout-parsed session_id takes precedence over Channel B UUID."""
        from autoskillit.execution.headless import _build_skill_result

        stdout_session = "aaaaaaaa-0000-0000-0000-000000000001"
        channel_b_uuid = "bbbbbbbb-0000-0000-0000-000000000002"
        stdout = (
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "session_id": stdout_session,
                    "result": "ORDER_UP\n",
                    "is_error": False,
                }
            )
            + "\n"
        )
        result = SubprocessResult(
            returncode=0,
            stdout=stdout,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=12345,
            channel_b_session_id=channel_b_uuid,
        )
        skill_result = _build_skill_result(
            result,
            completion_marker="ORDER_UP",
            skill_command="/autoskillit:smoke-task",
            audit=None,
            expected_output_patterns=[],
            cwd="/tmp",
        )
        assert skill_result.session_id == stdout_session  # NOT channel_b_uuid

    def test_make_result_exposes_channel_b_session_id(self):
        """_make_result must accept channel_b_session_id to enable Channel B path tests."""
        result = _make_result(channel_b_session_id="test-uuid-123")
        assert result.channel_b_session_id == "test-uuid-123"


class TestRecoverFromSeparateMarker:
    """Recovery path integration: marker in separate assistant message."""

    def _make_result(
        self,
        *,
        stdout: str,
        marker: str = "%%DONE%%",
        termination: TerminationReason = TerminationReason.NATURAL_EXIT,
        returncode: int = 0,
        channel: ChannelConfirmation = ChannelConfirmation.UNMONITORED,
        stderr: str = "",
    ):
        from autoskillit.execution.headless import _build_skill_result

        result = SubprocessResult(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            termination=termination,
            pid=0,
            channel_confirmation=channel,
        )
        return _build_skill_result(result, completion_marker=marker)

    def test_recovery_yields_success_when_marker_in_separate_message(self):
        """CHANNEL_B + standalone marker in separate assistant msg → result text populated.

        Old code: success=True via CHANNEL_B bypass, result="" (recovery skipped by
        ``if not success`` gate). New code: recovery runs before _compute_outcome so the
        result field is populated from assistant message content.
        """
        msg1 = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Substantive work completed."}]},
            }
        )
        msg2 = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "%%DONE%%"}]},
            }
        )
        result_rec = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "session_id": "s1",
            }
        )
        stdout = "\n".join([msg1, msg2, result_rec])

        skill = self._make_result(
            stdout=stdout, marker="%%DONE%%", channel=ChannelConfirmation.CHANNEL_B
        )
        assert skill.success is True
        assert "Substantive work completed." in skill.result

    def test_recovery_skipped_when_no_marker(self):
        """No completion_marker → _recover_from_separate_marker is not attempted."""
        msg1 = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Some output."}]},
            }
        )
        result_rec = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "session_id": "s1",
            }
        )
        stdout = "\n".join([msg1, result_rec])

        skill = self._make_result(stdout=stdout, marker="")
        assert skill.success is False  # empty result, no recovery possible

    def test_recovery_skipped_when_marker_inline(self):
        """Marker is inline in the result → _marker_is_standalone returns False → no recovery."""
        payload = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Task done. %%DONE%% and more text.",
                "session_id": "s1",
            }
        )

        skill = self._make_result(stdout=payload, marker="%%DONE%%")
        assert skill.success is True  # marker found inline → success
        assert "%%DONE%%" not in skill.result  # marker stripped from result_text

    def test_recovery_fails_gracefully_when_only_marker_content(self):
        """Standalone marker message with no other substantive content → no recovery."""
        msg_only_marker = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "%%DONE%%"}]},
            }
        )
        result_rec = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "session_id": "s1",
            }
        )
        stdout = "\n".join([msg_only_marker, result_rec])

        skill = self._make_result(stdout=stdout, marker="%%DONE%%")
        # Only the marker exists — stripped content is empty → _recover_from_separate_marker
        # returns None → no session replacement → success=False
        assert skill.success is False

    def test_recovery_fires_with_unmonitored_channel_and_realistic_cli_output(self):
        """UNMONITORED + assistant messages with standalone marker + empty result → success.

        Exercises the process-exits-first scenario: Channel B was never detected
        (UNMONITORED), but stdout contains type=assistant records with the marker
        on a standalone line. Recovery via _recover_from_separate_marker produces
        success=True.

        The marker occupies its own content block so that the newline-join fix
        (session.py) is required for _marker_is_standalone to return True.
        """
        msg_work = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Task completed successfully."}]},
            }
        )
        msg_marker = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "Signalling completion."},
                        {"type": "text", "text": "%%DONE%%"},
                    ]
                },
            }
        )
        result_rec = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "session_id": "s1",
            }
        )
        stdout = "\n".join([msg_work, msg_marker, result_rec])

        skill = self._make_result(
            stdout=stdout,
            marker="%%DONE%%",
            channel=ChannelConfirmation.UNMONITORED,
        )
        assert skill.success is True
        assert skill.needs_retry is False
        assert "Task completed successfully." in skill.result


class TestBuildSkillResultUsesComputeOutcome:
    """_build_skill_result derives success/needs_retry from _compute_outcome."""

    def test_success_maps_from_succeeded_outcome(self):
        """NATURAL_EXIT, returncode=0, valid result → success=True, needs_retry=False."""
        from autoskillit.execution.headless import _build_skill_result

        marker = "%%ORDER_UP%%"
        payload = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": f"Task done. {marker}",
                "session_id": "s1",
            }
        )
        skill = _build_skill_result(_sr(stdout=payload), completion_marker=marker)
        assert skill.success is True
        assert skill.needs_retry is False

    def test_needs_retry_maps_from_retriable_outcome(self):
        """error_max_turns session → success=False, needs_retry=True."""
        from autoskillit.execution.headless import _build_skill_result

        payload = json.dumps(
            {
                "type": "result",
                "subtype": "error_max_turns",
                "is_error": True,
                "result": "Reached max turns.",
                "session_id": "s1",
            }
        )
        skill = _build_skill_result(_sr(returncode=1, stdout=payload))
        assert skill.success is False
        assert skill.needs_retry is True

    def test_failed_maps_from_failed_outcome(self):
        """Timeout session → success=False, needs_retry=False."""
        from autoskillit.execution.headless import _build_skill_result

        skill = _build_skill_result(_sr(returncode=-1, termination=TerminationReason.TIMED_OUT))
        assert skill.success is False
        assert skill.needs_retry is False

    def test_contradiction_guard_inside_compute_outcome(self):
        """CHANNEL_B + error_max_turns → success=False, needs_retry=True (retry wins)."""
        from autoskillit.execution.headless import _build_skill_result

        payload = json.dumps(
            {
                "type": "result",
                "subtype": "error_max_turns",
                "is_error": True,
                "result": "Reached max turns.",
                "session_id": "s1",
            }
        )
        skill = _build_skill_result(
            SubprocessResult(
                returncode=1,
                stdout=payload,
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=0,
                channel_confirmation=ChannelConfirmation.CHANNEL_B,
            )
        )
        # Contradiction guard: CHANNEL_B bypass makes success=True, error_max_turns
        # makes needs_retry=True. Retry signal is authoritative → success=False.
        assert skill.success is False
        assert skill.needs_retry is True

    def test_dead_end_guard_escalates_channel_a(self):
        """Empty result + CHANNEL_A → needs_retry=True (escalated from dead end)."""
        from autoskillit.execution.headless import _build_skill_result

        payload = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "session_id": "s1",
            }
        )
        skill = _build_skill_result(
            SubprocessResult(
                returncode=0,
                stdout=payload,
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=0,
                channel_confirmation=ChannelConfirmation.CHANNEL_A,
            )
        )
        # Dead-end guard: success=False (empty result), needs_retry=False (CHANNEL_A
        # returns False from _compute_retry), but CHANNEL_A confirms completion →
        # escalate to needs_retry=True.
        assert skill.success is False
        assert skill.needs_retry is True


class TestRunHeadlessCore:
    """Integration test for run_headless_core via the injected mock runner."""

    @pytest.mark.anyio
    async def test_run_headless_core_returns_success_result(self, tool_ctx):
        from autoskillit.execution.headless import run_headless_core

        marker = tool_ctx.config.run_skill.completion_marker
        payload = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": f"Task completed. {marker}",
                "session_id": "sess-xyz",
            }
        )
        tool_ctx.runner.push(
            SubprocessResult(0, payload, "", TerminationReason.NATURAL_EXIT, pid=1)
        )
        result = await run_headless_core("/investigate foo", cwd="/tmp", ctx=tool_ctx)
        assert result.success is True
        assert result.needs_retry is False
        assert result.result == "Task completed."
        # Assert the runner was called exactly once with a command containing the skill
        assert len(tool_ctx.runner.call_args_list) == 1
        cmd, _cwd, _timeout, _kwargs = tool_ctx.runner.call_args_list[0]
        # The command list must include the "-p" flag and the skill invocation
        assert any("-p" in part for part in cmd)
        assert any("/investigate" in part for part in cmd)
        # The command must include --output-format and the format value
        assert "--output-format" in cmd
        fmt_idx = cmd.index("--output-format")
        assert cmd[fmt_idx + 1] == "stream-json"

    @pytest.mark.anyio
    async def test_assembled_cmd_contains_format_required_flags(self, tool_ctx):
        """Assembled command must include all flags required by the output format."""
        from autoskillit.execution.headless import run_headless_core

        marker = tool_ctx.config.run_skill.completion_marker
        payload = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": f"Done. {marker}",
                "session_id": "sess-1",
            }
        )
        tool_ctx.runner.push(
            SubprocessResult(0, payload, "", TerminationReason.NATURAL_EXIT, pid=1)
        )
        await run_headless_core("/investigate bar", cwd="/tmp", ctx=tool_ctx)
        cmd, _cwd, _timeout, _kwargs = tool_ctx.runner.call_args_list[0]
        fmt = tool_ctx.config.run_skill.output_format
        for flag in fmt.required_cli_flags:
            assert flag in cmd, f"Missing required flag {flag!r} in assembled command: {cmd}"


class TestHeadlessTelemetryContainment:
    """Telemetry errors in run_headless_core must not suppress the
    fully-built SkillResult."""

    def _success_payload(self, completion_marker: str) -> str:
        import json

        return json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": f"Task completed. {completion_marker}",
                "session_id": "sess-telemetry-test",
            }
        )

    @pytest.mark.anyio
    async def test_run_headless_core_token_log_error_does_not_suppress_skill_result(
        self, tool_ctx, monkeypatch
    ):
        """token_log.record() raising must not suppress the skill_result."""
        import structlog.testing

        from autoskillit.core.types import SkillResult as _SkillResult
        from autoskillit.execution.headless import run_headless_core

        def bad_record(*args: object, **kwargs: object) -> None:
            raise TypeError("simulated bad token_usage shape")

        monkeypatch.setattr(tool_ctx.token_log, "record", bad_record)

        marker = tool_ctx.config.run_skill.completion_marker
        tool_ctx.runner.push(
            SubprocessResult(
                0, self._success_payload(marker), "", TerminationReason.NATURAL_EXIT, pid=1
            )
        )

        with structlog.testing.capture_logs() as cap:
            result = await run_headless_core(
                "/investigate foo", cwd="/tmp", ctx=tool_ctx, step_name="test-step"
            )

        assert isinstance(result, _SkillResult)
        assert result.success is True, f"Expected success=True, got result: {result}"
        assert any(e.get("event") == "token_log_record_failed" for e in cap), (
            f"Expected 'token_log_record_failed' in captured logs, got: {cap}"
        )


class TestEnsureSkillPrefix:
    """Unit tests for _ensure_skill_prefix helper."""

    def test_adds_use_to_slash_command(self):
        assert _ensure_skill_prefix("/investigate error") == "Use /investigate error"

    def test_adds_use_to_namespaced_skill(self):
        assert (
            _ensure_skill_prefix("/autoskillit:investigate error")
            == "Use /autoskillit:investigate error"
        )

    def test_no_double_prefix(self):
        assert _ensure_skill_prefix("Use /investigate error") == "Use /investigate error"

    def test_ignores_plain_prompts(self):
        assert _ensure_skill_prefix("Fix the bug in main.py") == "Fix the bug in main.py"

    def test_handles_leading_whitespace(self):
        assert _ensure_skill_prefix("  /investigate error") == "Use /investigate error"


class TestStalenessReturnsNeedsRetry:
    """Stale SubprocessResult triggers needs_retry response."""

    def test_staleness_returns_needs_retry(self):
        """A stale result produces needs_retry=True, retry_reason='stale'."""
        stale_result = SubprocessResult(
            returncode=-1,
            stdout="",
            stderr="",
            termination=TerminationReason.STALE,
            pid=12345,
        )
        response = json.loads(_build_skill_result(stale_result).to_json())
        assert response["needs_retry"] is True
        assert response["retry_reason"] == "stale"
        assert response["subtype"] == "stale"
        assert response["success"] is False


class TestBuildSkillResultCrossValidation:
    """_build_skill_result cross-validates signals to produce unambiguous success."""

    EXPECTED_SKILL_KEYS = {
        "success",
        "result",
        "session_id",
        "subtype",
        "cli_subtype",
        "is_error",
        "exit_code",
        "kill_reason",
        "last_stop_reason",
        "lifespan_started",
        "needs_retry",
        "retry_reason",
        "stderr",
        "token_usage",
        "write_path_warnings",
        "write_call_count",
    }

    def test_empty_stdout_exit_zero_is_failure(self):
        """Exit 0 with empty stdout is NOT success — output was lost."""
        result_obj = SubprocessResult(
            returncode=0, stdout="", stderr="", termination=TerminationReason.NATURAL_EXIT, pid=1
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["success"] is False
        assert response["is_error"] is True

    def test_timed_out_session_is_failure(self):
        """Timed-out sessions are always failures, regardless of partial stdout."""
        result_obj = SubprocessResult(
            returncode=-1, stdout="", stderr="", termination=TerminationReason.TIMED_OUT, pid=1
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["success"] is False
        assert response["is_error"] is True
        assert response["subtype"] == "timeout"

    def test_stale_session_is_failure(self):
        """Stale sessions are failures (even though retriable)."""
        result_obj = SubprocessResult(
            returncode=-1, stdout="", stderr="", termination=TerminationReason.STALE, pid=1
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["success"] is False
        assert response["needs_retry"] is True

    def test_normal_success_has_success_true(self):
        """A valid session result with non-empty output is success."""
        valid_json = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Task completed.",
                "session_id": "s1",
            }
        )
        result_obj = SubprocessResult(
            returncode=0,
            stdout=valid_json,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["success"] is True
        assert response["is_error"] is False
        assert response["result"] == "Task completed."

    def test_nonzero_exit_overrides_is_error_false(self):
        """Exit code != 0 means failure even if Claude wrote is_error=false."""
        valid_json = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "partial",
                "session_id": "s1",
            }
        )
        result_obj = SubprocessResult(
            returncode=1,
            stdout=valid_json,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["success"] is False

    @pytest.mark.parametrize(
        "result_obj",
        [
            SubprocessResult(
                returncode=-1, stdout="", stderr="", termination=TerminationReason.STALE, pid=1
            ),
            SubprocessResult(
                returncode=-1, stdout="", stderr="", termination=TerminationReason.TIMED_OUT, pid=1
            ),
            SubprocessResult(
                returncode=0,
                stdout=json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "Done.",
                        "session_id": "s1",
                    }
                ),
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=1,
            ),
            SubprocessResult(
                returncode=0, stdout="", stderr="", termination=TerminationReason.NATURAL_EXIT, pid=1
            ),
        ],
        ids=["stale", "timeout", "normal_success", "empty_stdout"],
    )
    def test_schema_keys(self, result_obj: SubprocessResult):
        """Response always exposes the full standard key set."""
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert set(response.keys()) == self.EXPECTED_SKILL_KEYS


def _make_subprocess_result_with_tool_uses(
    tool_use_names: list[str] | None = None,
) -> SubprocessResult:
    """Build a SubprocessResult whose stdout includes optional tool_use NDJSON blocks."""
    records = []
    if tool_use_names:
        content = [
            {"type": "tool_use", "name": name, "id": f"tu-{i}"}
            for i, name in enumerate(tool_use_names)
        ]
        records.append(json.dumps({"type": "assistant", "message": {"content": content}}))
    records.append(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "test-session",
            }
        )
    )
    return SubprocessResult(
        returncode=0,
        stdout="\n".join(records),
        stderr="",
        termination=TerminationReason.NATURAL_EXIT,
        pid=12345,
    )


class TestBuildSkillResultLifespanStarted:
    """_build_skill_result sets lifespan_started based on tool_uses in stdout."""

    def test_build_skill_result_sets_lifespan_started_true(self):
        """Non-empty tool_uses in stdout → _build_skill_result produces lifespan_started=True."""
        result = _make_subprocess_result_with_tool_uses(tool_use_names=["Write", "Edit"])
        sr = _build_skill_result(result)
        assert sr.lifespan_started is True

    def test_build_skill_result_sets_lifespan_started_false_no_tool_uses(self):
        """Empty tool_uses in stdout → _build_skill_result produces lifespan_started=False."""
        result = _make_subprocess_result_with_tool_uses(tool_use_names=None)
        sr = _build_skill_result(result)
        assert sr.lifespan_started is False


class TestBuildSkillResultStderr:
    """_build_skill_result includes stderr in responses."""

    def test_stderr_included_in_response(self):
        """Subprocess stderr is surfaced in the response."""
        valid_json = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
            }
        )
        result_obj = SubprocessResult(
            returncode=0,
            stdout=valid_json,
            stderr="queue contention",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["stderr"] == "queue contention"

    def test_stderr_truncated(self):
        """Stderr exceeding 5000 chars is truncated."""
        valid_json = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
            }
        )
        long_stderr = "x" * 6000
        result_obj = SubprocessResult(
            returncode=0,
            stdout=valid_json,
            stderr=long_stderr,
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert len(response["stderr"]) < len(long_stderr)
        assert "truncated" in response["stderr"]

    def test_empty_stderr_is_empty_string(self):
        """Empty stderr produces empty string, not omitted."""
        valid_json = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
            }
        )
        result_obj = SubprocessResult(
            returncode=0,
            stdout=valid_json,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["stderr"] == ""

    def test_stale_branch_has_empty_stderr(self):
        """Stale branch produces empty stderr (process killed before output)."""
        result_obj = SubprocessResult(
            returncode=-1, stdout="", stderr="", termination=TerminationReason.STALE, pid=1
        )
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["stderr"] == ""


class TestContextExhaustionStructured:
    """_is_context_exhausted uses structured detection, not substring on result."""

    def test_context_exhaustion_not_triggered_by_model_prose(self):
        """Model output discussing prompt length must NOT trigger context exhaustion."""
        from autoskillit.execution.session import ClaudeSessionResult

        session = ClaudeSessionResult(
            subtype="error_during_execution",
            is_error=True,
            result="The user said: prompt is too long for this task",
            session_id="s1",
        )
        assert session.needs_retry is False
        assert session._is_context_exhausted() is False

    def test_real_context_exhaustion_still_detected(self):
        """Genuine context exhaustion (specific subtype) is still detected."""
        from autoskillit.execution.session import ClaudeSessionResult

        session = ClaudeSessionResult(
            subtype="error_during_execution",
            is_error=True,
            result="prompt is too long",
            session_id="s1",
            errors=["prompt is too long"],
        )
        assert session._is_context_exhausted() is True
        assert session.needs_retry is True


class TestParseFallbackRejectsUntypedJson:
    """parse_session_result fallback path requires type == result."""

    def test_parse_fallback_rejects_untyped_json(self):
        """Single JSON object without type=result must be rejected."""
        from autoskillit.execution.session import parse_session_result

        parsed = parse_session_result('{"error": "something broke"}')
        assert parsed.subtype == "unparseable"
        assert parsed.is_error is True


class TestCompletionViaMonitorKill:
    """Completion detected by monitor + kill returncode is not failure."""

    MARKER = "%%ORDER_UP%%"

    def test_completion_via_monitor_kill_is_not_failure(self):
        """When the session monitor detects completion and kills the process,
        returncode is -15 (SIGTERM). _compute_success should treat this as
        success when the session result envelope says success.
        """
        from autoskillit.execution.session import ClaudeSessionResult, _compute_success

        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=f"Task completed successfully.\n\n{self.MARKER}",
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=-15,
                termination=TerminationReason.COMPLETED,
                completion_marker=self.MARKER,
            )
            is True
        )

    def test_completion_via_monitor_kill_returncode_zero(self):
        """PTY may mask signal codes to returncode=0 — COMPLETED still works."""
        from autoskillit.execution.session import ClaudeSessionResult, _compute_success

        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=f"Task completed successfully.\n\n{self.MARKER}",
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=0,
                termination=TerminationReason.COMPLETED,
                completion_marker=self.MARKER,
            )
            is True
        )


def _context_exhausted_with_worktree_ndjson(worktree_path: str) -> str:
    """NDJSON where context exhaustion occurred after the skill emitted
    worktree_path= in Step 1's assistant message."""
    assistant = json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": (
                    "Worktree created successfully.\n\n"
                    f"worktree_path={worktree_path}\n"
                    "branch_name=impl-fix-20260307\n"
                ),
            },
        }
    )
    result = json.dumps(
        {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "result": "prompt is too long",
            "session_id": "s1",
            "errors": ["prompt is too long"],
        }
    )
    return f"{assistant}\n{result}\n"


class TestExtractWorktreePath:
    """Unit tests for _extract_worktree_path helper."""

    def test_extracts_path_from_single_message(self):
        """Finds worktree_path= token in a single assistant message."""
        msg = "Worktree created.\nworktree_path=/path/to/wt\nbranch_name=impl"
        assert _extract_worktree_path([msg]) == "/path/to/wt"

    def test_returns_last_occurrence_across_messages(self):
        """When multiple messages contain the token, last match wins."""
        msgs = [
            "worktree_path=/first/path",
            "worktree_path=/second/path",
        ]
        assert _extract_worktree_path(msgs) == "/second/path"

    def test_returns_none_when_no_token(self):
        """Returns None when no worktree_path= token is present."""
        assert _extract_worktree_path(["No token here."]) is None

    def test_returns_none_for_empty_messages(self):
        """Returns None for empty message list."""
        assert _extract_worktree_path([]) is None

    def test_strips_trailing_whitespace(self):
        """Extracted value has trailing whitespace stripped."""
        msg = "worktree_path=/some/path   \n"
        assert _extract_worktree_path([msg]) == "/some/path"

    def test_extract_worktree_path_with_spaces_around_equals(self):
        """Regex handles 'worktree_path = /path' format (spaces around =)."""
        msg = "Worktree created.\nworktree_path = /path/to/wt\nbranch_name = impl"
        result = _extract_worktree_path([msg])
        assert result == "/path/to/wt"

    def test_extract_worktree_path_mixed_spacing(self):
        """Regex handles mixed spacing: 'worktree_path= /path' and 'worktree_path =/path'."""
        for token in ["worktree_path= /path/to/wt", "worktree_path =/path/to/wt"]:
            msg = f"Done.\n{token}\nbranch_name=impl"
            result = _extract_worktree_path([msg])
            assert result == "/path/to/wt"

    def test_relative_path_with_dotdot_is_discarded(self) -> None:
        """Relative worktree_path tokens (../...) are silently discarded; returns None."""
        result = _extract_worktree_path(["worktree_path = ../worktrees/impl-fix-20260307"])
        assert result is None

    def test_relative_path_without_slash_prefix_is_discarded(self) -> None:
        """Any non-absolute form is silently discarded."""
        result = _extract_worktree_path(["worktree_path = worktrees/impl-fix-20260307"])
        assert result is None

    def test_absolute_wins_over_subsequent_relative(self) -> None:
        """If an absolute token appears before a relative one, the absolute path is returned."""
        result = _extract_worktree_path(
            [
                "worktree_path = /abs/worktrees/impl-first",
                "worktree_path = ../worktrees/impl-second",
            ]
        )
        assert result == "/abs/worktrees/impl-first"


class TestBuildSkillResultWorktreePath:
    """_build_skill_result extracts worktree_path on context exhaustion."""

    def test_extracts_worktree_path_on_context_exhaustion(self):
        """worktree_path from early Step 1 emission flows into SkillResult."""
        path = "/tmp/worktrees/impl-fix-20260307"
        sub_result = SubprocessResult(
            returncode=-1,
            stdout=_context_exhausted_with_worktree_ndjson(path),
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1234,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
        )
        sr = _build_skill_result(sub_result, "", "/test", None)
        assert sr.success is False
        assert sr.needs_retry is True
        assert sr.worktree_path == path

    def test_worktree_path_none_when_token_absent(self):
        """If the skill never emitted worktree_path=, the field is None."""
        sub_result = SubprocessResult(
            returncode=-1,
            stdout=_context_exhausted_session_json(),
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1234,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
        )
        sr = _build_skill_result(sub_result, "", "/test", None)
        assert sr.success is False
        assert sr.needs_retry is True
        assert sr.worktree_path is None

    def test_worktree_path_none_on_success(self):
        """On success, worktree_path is not extracted (field stays None)."""
        sub_result = _make_result(
            returncode=0,
            stdout=_success_session_json("worktree_path=/path\nbranch_name=impl-fix"),
        )
        sr = _build_skill_result(sub_result, "", "/test", None)
        assert sr.success is True
        assert sr.worktree_path is None

    def test_worktree_path_uses_last_occurrence(self):
        """When worktree_path= appears multiple times, the last value wins."""
        assistant1 = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": "worktree_path=/first/path\nbranch_name=b1",
                },
            }
        )
        assistant2 = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": "worktree_path=/second/path\nbranch_name=b1",
                },
            }
        )
        result = json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "result": "prompt is too long",
                "session_id": "s1",
                "errors": ["prompt is too long"],
            }
        )
        ndjson = f"{assistant1}\n{assistant2}\n{result}\n"
        sub_result = SubprocessResult(
            returncode=-1,
            stdout=ndjson,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1234,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
        )
        sr = _build_skill_result(sub_result, "", "/test", None)
        assert sr.worktree_path == "/second/path"


class TestWorktreePathOnContextExhaustion:
    """Contract: worktree_path appears as top-level JSON field on needs_retry."""

    def test_worktree_path_in_json_response_on_context_limit(self):
        """Full stack: NDJSON with early token → SkillResult → to_json()."""
        path = "/tmp/worktrees/impl-fix"
        assistant = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": f"worktree_path={path}\nbranch_name=impl-fix",
                },
            }
        )
        result = json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "result": "prompt is too long",
                "session_id": "s1",
                "errors": ["prompt is too long"],
            }
        )
        ndjson = f"{assistant}\n{result}\n"
        sub = SubprocessResult(
            returncode=-1,
            stdout=ndjson,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1234,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
        )
        sr = _build_skill_result(sub, "", "/test", None)
        data = json.loads(sr.to_json())

        assert sr.success is False
        assert data["needs_retry"] is True
        assert data["worktree_path"] == path


def test_relative_worktree_path_causes_adjudicated_failure() -> None:
    """Regression guard for issue #412.

    implement-worktree-no-merge emitting worktree_path = ../worktrees/...
    must classify as adjudicated_failure, not success.
    Uses the real contract pattern from skill_contracts.yaml.
    """
    ndjson = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": (
                "worktree_path = ../worktrees/impl-fix-20260316\n"
                "branch_name = impl-fix-20260316\n"
                "%%ORDER_UP%%"
            ),
            "session_id": "test-412",
        }
    )
    skill_result = _build_skill_result(
        _sr(stdout=ndjson),
        completion_marker="%%ORDER_UP%%",
        expected_output_patterns=["worktree_path\\s*=\\s*/.+"],
        cwd="/some/project",
        skill_command="implement-worktree-no-merge",
    )
    assert skill_result.subtype == "adjudicated_failure"
    assert skill_result.success is False
    assert skill_result.needs_retry is False


class TestBuildSkillResultCompleted:
    """_build_skill_result and _compute_success handle COMPLETED termination correctly."""

    def test_build_skill_result_completed_nonempty_result_is_success(self):
        """COMPLETED + valid JSON stdout with non-empty result → success=True."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Task done.",
                "session_id": "s1",
            }
        )
        result = _make_result(
            returncode=-15,
            stdout=stdout,
            termination_reason=TerminationReason.COMPLETED,
        )
        parsed = json.loads(_build_skill_result(result).to_json())
        assert parsed["success"] is True

    def test_build_skill_result_completed_empty_result_is_failure(self):
        """COMPLETED + empty stdout + rc=-15 → success=False, needs_retry=True."""
        result = _make_result(
            returncode=-15,
            stdout="",
            termination_reason=TerminationReason.COMPLETED,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
        )
        parsed = json.loads(_build_skill_result(result).to_json())
        assert parsed["success"] is False
        assert parsed["needs_retry"] is True

    def test_compute_success_completed_empty_result_returns_false(self):
        """Empty result with COMPLETED termination: bypass does NOT engage → returns False."""
        from autoskillit.execution.session import ClaudeSessionResult, _compute_success

        session = ClaudeSessionResult(
            subtype="empty_output",
            result="",
            is_error=True,
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=-15,
                termination=TerminationReason.COMPLETED,
            )
            is False
        )

    def test_success_empty_completed_returns_needs_retry_true(self, tool_ctx):
        """Full path: stdout with success+empty under COMPLETED → needs_retry=True."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "session_id": "s1",
            }
        )
        result = _make_result(
            returncode=0,
            stdout=stdout,
            termination_reason=TerminationReason.COMPLETED,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
        )
        parsed = json.loads(
            _build_skill_result(result, completion_marker="", skill_command="/test").to_json()
        )
        assert parsed["success"] is False
        assert parsed["needs_retry"] is True
        assert parsed["retry_reason"] == RetryReason.RESUME.value
        assert parsed["subtype"] == "empty_result"
        assert parsed["cli_subtype"] == "success"

    def test_success_empty_completed_subtype_captured_in_audit_log(self, tool_ctx):
        """_capture_failure receives the normalized subtype for audit log integrity."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "session_id": "s1",
            }
        )
        result = _make_result(
            returncode=0,
            stdout=stdout,
            termination_reason=TerminationReason.COMPLETED,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
        )
        sr = _build_skill_result(
            result, completion_marker="", skill_command="/test", audit=tool_ctx.audit
        )
        report = tool_ctx.audit.get_report()
        assert len(report) == 1
        assert report[0].subtype == "empty_result"
        assert report[0].needs_retry is True
        assert sr.cli_subtype == "success"

    def test_build_skill_result_subtype_never_contradicts_success(self, tool_ctx):
        """Test B: _build_skill_result never produces contradictory (success, subtype) pairs."""
        # Path 1: COMPLETED + UNMONITORED + "success" + empty result → RETRIABLE
        stdout_empty = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "session_id": "s1",
            }
        )
        result1 = _make_result(
            returncode=0,
            stdout=stdout_empty,
            termination_reason=TerminationReason.COMPLETED,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
        )
        sr1 = _build_skill_result(result1, completion_marker="", skill_command="/test")
        assert sr1.success is False
        assert sr1.subtype != "success", (
            f"subtype must not be 'success' when success=False, got {sr1.subtype!r}"
        )
        assert sr1.cli_subtype == "success"

        # Path 2: NATURAL_EXIT + rc=0 + "success" + missing marker → RETRIABLE (EARLY_STOP)
        stdout_missing_marker = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "I did the work but forgot the marker.",
                "session_id": "s2",
            }
        )
        result2 = _make_result(
            returncode=0,
            stdout=stdout_missing_marker,
            termination_reason=TerminationReason.NATURAL_EXIT,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
        )
        sr2 = _build_skill_result(result2, completion_marker="%%ORDER_UP%%", skill_command="/test")
        assert sr2.success is False
        assert sr2.subtype != "success", (
            f"subtype must not be 'success' when success=False, got {sr2.subtype!r}"
        )
        assert sr2.cli_subtype == "success"

    def test_build_skill_result_channel_b_empty_stdout_subtype_is_success(self, tool_ctx):
        """Test C: CHANNEL_B + empty stdout normalizes subtype up to 'success'."""
        result = _make_result(
            returncode=0,
            stdout="",
            termination_reason=TerminationReason.COMPLETED,
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
        )
        sr = _build_skill_result(result, completion_marker="", skill_command="/test")
        assert sr.success is True
        assert sr.subtype == "success"
        assert sr.cli_subtype == "empty_output"


class TestMarkerCrossValidation:
    """Completion marker cross-validation catches misclassified sessions."""

    MARKER = "%%ORDER_UP%%"

    def test_marker_only_result_is_not_success(self):
        """Result containing only the marker with no real content is failure."""
        from autoskillit.execution.session import ClaudeSessionResult, _compute_success

        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=self.MARKER,
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=0,
                termination=TerminationReason.NATURAL_EXIT,
                completion_marker=self.MARKER,
            )
            is False
        )

    def test_marker_stripped_from_result(self):
        """_build_skill_result strips the completion marker from result text."""
        valid_json = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": f"Task completed.\n\n{self.MARKER}",
                "session_id": "s1",
            }
        )
        result_obj = SubprocessResult(
            returncode=0,
            stdout=valid_json,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
        response = json.loads(
            _build_skill_result(result_obj, completion_marker=self.MARKER).to_json()
        )
        assert self.MARKER not in response["result"]
        assert "Task completed." in response["result"]

    def test_natural_exit_without_marker_not_success(self):
        """Session claims success but never wrote the marker — not success."""
        from autoskillit.execution.session import ClaudeSessionResult, _compute_success

        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="Some partial output",
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=0,
                termination=TerminationReason.NATURAL_EXIT,
                completion_marker=self.MARKER,
            )
            is False
        )

    def test_termination_reason_natural_exit(self):
        """NATURAL_EXIT with marker in result is success."""
        from autoskillit.execution.session import ClaudeSessionResult, _compute_success

        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=f"Done.\n\n{self.MARKER}",
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=0,
                termination=TerminationReason.NATURAL_EXIT,
                completion_marker=self.MARKER,
            )
            is True
        )

    def test_termination_reason_completed(self):
        """COMPLETED termination with marker in result is success."""
        from autoskillit.execution.session import ClaudeSessionResult, _compute_success

        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=f"Done.\n\n{self.MARKER}",
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=0,
                termination=TerminationReason.COMPLETED,
                completion_marker=self.MARKER,
            )
            is True
        )

    def test_termination_reason_completed_without_marker_fails(self):
        """COMPLETED but result doesn't contain marker — not success."""
        from autoskillit.execution.session import ClaudeSessionResult, _compute_success

        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="Some output without marker",
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=0,
                termination=TerminationReason.COMPLETED,
                completion_marker=self.MARKER,
            )
            is False
        )

    @pytest.mark.parametrize(
        "termination,returncode,result_text,expected",
        [
            (TerminationReason.NATURAL_EXIT, 0, f"Done.\n\n{MARKER}", True),
            (TerminationReason.NATURAL_EXIT, 0, "No marker here", False),
            (TerminationReason.NATURAL_EXIT, 0, MARKER, False),  # marker-only
            (TerminationReason.COMPLETED, 0, f"Done.\n\n{MARKER}", True),
            (TerminationReason.COMPLETED, -15, f"Done.\n\n{MARKER}", True),
            (TerminationReason.COMPLETED, -9, f"Done.\n\n{MARKER}", True),
            (TerminationReason.COMPLETED, -9, "No marker here", False),
            (TerminationReason.COMPLETED, 0, "No marker here", False),
            (TerminationReason.STALE, -15, f"Done.\n\n{MARKER}", False),
            (TerminationReason.TIMED_OUT, -1, f"Done.\n\n{MARKER}", False),
        ],
        ids=[
            "natural_exit+marker=success",
            "natural_exit+no_marker=failure",
            "natural_exit+marker_only=failure",
            "completed+marker=success",
            "completed_sigterm+marker=success",
            "completed_sigkill+marker=success",
            "completed_sigkill+no_marker=failure",
            "completed+no_marker=failure",
            "stale+marker=failure",
            "timed_out+marker=failure",
        ],
    )
    def test_cross_validation_matrix(self, termination, returncode, result_text, expected):
        """Full cross-validation matrix for termination x marker presence."""
        from autoskillit.execution.session import ClaudeSessionResult, _compute_success

        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=result_text,
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=returncode,
                termination=termination,
                completion_marker=self.MARKER,
            )
            is expected
        )

    def test_build_skill_result_channel_a_win_preserves_success_with_minus_9(self):
        """COMPLETED + CHANNEL_A + returncode=-9 + content → success=True (1d).

        Verifies that the adjudicator correctly marks a SIGKILL'd session as
        successful when Channel A confirmed completion and the result has content
        with the marker. This is the key assertion that the -9 bug existed at the
        kill level, not the adjudication level.
        """
        ndjson = (
            '{"type":"result","subtype":"success",'
            f'"result":"Work done.\\n\\n{self.MARKER}",'
            '"session_id":"s1","is_error":false}\n'
        )
        result = _build_skill_result(
            SubprocessResult(
                returncode=-9,
                stdout=ndjson,
                stderr="",
                termination=TerminationReason.COMPLETED,
                pid=1,
                channel_confirmation=ChannelConfirmation.CHANNEL_A,
            ),
            completion_marker=self.MARKER,
            skill_command="test-skill",
            audit=None,
        )
        assert result.success is True, (
            f"Expected success=True for COMPLETED+CHANNEL_A+rc=-9, got success={result.success}, "
            f"subtype={result.subtype!r}"
        )

    def test_build_skill_result_recovers_when_marker_in_separate_assistant_message(self):
        """
        If the model emits substantive content in an assistant record and %%ORDER_UP%%
        as a separate final message, _build_skill_result must return success=True with
        the substantive content — not success=False with empty result.
        """
        marker = self.MARKER
        ndjson = (
            '{"type":"assistant","message":{"role":"assistant",'
            '"content":"Detailed audit report.\\nGO verdict."}}\n'
            '{"type":"assistant","message":{"role":"assistant","content":"%%ORDER_UP%%"}}\n'
            '{"type":"result","subtype":"success","result":"%%ORDER_UP%%",'
            '"session_id":"s1","is_error":false}\n'
        )
        result = _build_skill_result(
            SubprocessResult(
                returncode=0,
                stdout=ndjson,
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=1,
            ),
            completion_marker=marker,
            skill_command="audit-impl",
            audit=None,
        )
        assert result.success is True
        assert "Detailed audit report." in result.result
        assert marker not in result.result
        assert result.needs_retry is False

    def test_build_skill_result_does_not_recover_when_only_marker_in_assistant(self):
        """
        If ALL assistant records contain only the marker and result is also marker-only,
        recovery must not produce a false positive — there is no substantive content.
        """
        marker = self.MARKER
        ndjson = (
            '{"type":"assistant","message":{"role":"assistant","content":"%%ORDER_UP%%"}}\n'
            '{"type":"result","subtype":"success","result":"%%ORDER_UP%%",'
            '"session_id":"s1","is_error":false}\n'
        )
        result = _build_skill_result(
            SubprocessResult(
                returncode=0,
                stdout=ndjson,
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=1,
            ),
            completion_marker=marker,
            skill_command="",
            audit=None,
        )
        assert result.success is False


class TestBuildSkillResultTokenUsage:
    """token_usage field in _build_skill_result output."""

    def _make_ndjson(self, *, model: str = "claude-sonnet-4-6") -> str:
        """Build a two-line NDJSON with an assistant record and a result record with usage."""
        assistant = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": model,
                    "usage": {
                        "input_tokens": 120,
                        "output_tokens": 45,
                        "cache_creation_input_tokens": 8,
                        "cache_read_input_tokens": 3,
                    },
                },
            }
        )
        result_rec = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Task complete.",
                "session_id": "sess-abc",
                "usage": {
                    "input_tokens": 200,
                    "output_tokens": 80,
                    "cache_creation_input_tokens": 8,
                    "cache_read_input_tokens": 3,
                },
            }
        )
        return assistant + "\n" + result_rec

    def test_token_usage_included_when_present(self):
        """JSON response includes token_usage when session has usage data."""
        stdout = self._make_ndjson()
        result_obj = _make_result(0, stdout, "")
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert "token_usage" in response
        usage = response["token_usage"]
        assert usage is not None
        assert usage["input_tokens"] == 200
        assert usage["output_tokens"] == 80
        assert usage["cache_creation_input_tokens"] == 8
        assert usage["cache_read_input_tokens"] == 3
        assert "model_breakdown" in usage
        assert "claude-sonnet-4-6" in usage["model_breakdown"]

    def test_token_usage_null_when_absent(self):
        """JSON response has token_usage: null when no usage data."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "s1",
                # no usage field
            }
        )
        result_obj = _make_result(0, stdout, "")
        response = json.loads(_build_skill_result(result_obj).to_json())
        assert response["token_usage"] is None

    def test_stale_result_has_null_token_usage(self):
        """Stale termination produces null token_usage."""
        stale_result = SubprocessResult(
            returncode=-1,
            stdout="",
            stderr="",
            termination=TerminationReason.STALE,
            pid=1,
        )
        response = json.loads(_build_skill_result(stale_result).to_json())
        assert response["token_usage"] is None

    def test_timeout_result_has_null_token_usage(self):
        """Timeout termination produces null token_usage."""
        timeout_result = _make_timeout_result(stdout="", stderr="")
        response = json.loads(_build_skill_result(timeout_result).to_json())
        assert response["token_usage"] is None


class TestFailureCaptureInBuildSkillResult:
    """_build_skill_result() must capture failures into tool_ctx.audit."""

    def test_captures_non_zero_exit_code(self, tool_ctx):
        result = _make_result(
            returncode=1,
            stdout=_failed_session_json(),
            channel_confirmation=ChannelConfirmation.UNMONITORED,
        )
        _build_skill_result(result, skill_command="/test:cmd", audit=tool_ctx.audit)
        assert len(tool_ctx.audit.get_report()) == 1

    def test_does_not_capture_clean_success(self, tool_ctx):
        result = _make_result(returncode=0, stdout=_success_session_json("done"))
        _build_skill_result(result, skill_command="/test:cmd", audit=tool_ctx.audit)
        assert tool_ctx.audit.get_report() == []

    def test_captured_record_has_correct_skill_command(self, tool_ctx):
        result = _make_result(
            returncode=1,
            stdout=_failed_session_json(),
            channel_confirmation=ChannelConfirmation.UNMONITORED,
        )
        _build_skill_result(
            result, skill_command="/autoskillit:implement-worktree", audit=tool_ctx.audit
        )
        assert tool_ctx.audit.get_report()[0].skill_command == "/autoskillit:implement-worktree"

    def test_captured_record_has_timestamp(self, tool_ctx):
        from datetime import datetime

        result = _make_result(
            returncode=1,
            stdout=_failed_session_json(),
            channel_confirmation=ChannelConfirmation.UNMONITORED,
        )
        _build_skill_result(result, skill_command="/test", audit=tool_ctx.audit)
        record = tool_ctx.audit.get_report()[0]
        assert record.timestamp  # non-empty ISO timestamp
        assert datetime.fromisoformat(record.timestamp)  # valid ISO 8601 format

    def test_stale_termination_is_captured(self, tool_ctx):
        result = _make_result(returncode=0, termination_reason=TerminationReason.STALE)
        _build_skill_result(result, skill_command="/test", audit=tool_ctx.audit)
        report = tool_ctx.audit.get_report()
        assert len(report) == 1
        assert report[0].subtype == "stale"

    def test_needs_retry_is_captured(self, tool_ctx):
        result = _make_result(returncode=1, stdout=_context_exhausted_session_json())
        _build_skill_result(result, skill_command="/test", audit=tool_ctx.audit)
        report = tool_ctx.audit.get_report()
        assert len(report) == 1
        assert report[0].needs_retry is True

    def test_stderr_truncated_to_500_chars(self, tool_ctx):
        long_stderr = "e" * 2000
        result = _make_result(
            returncode=1,
            stderr=long_stderr,
            stdout=_failed_session_json(),
            channel_confirmation=ChannelConfirmation.UNMONITORED,
        )
        _build_skill_result(result, skill_command="/test", audit=tool_ctx.audit)
        assert len(tool_ctx.audit.get_report()[0].stderr) <= 500


class TestStalePathStdoutCheck:
    """STALE termination recovers from stdout when a valid result record is present."""

    def _make_stale_result(self, stdout: str, returncode: int = -15) -> SubprocessResult:
        return SubprocessResult(
            returncode=returncode,
            stdout=stdout,
            stderr="",
            termination=TerminationReason.STALE,
            pid=12345,
        )

    def test_stale_kill_with_completed_result_in_stdout_is_success(self):
        """Session wrote a valid type=result record before going stale — should recover."""
        valid_completed_jsonl = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Task completed successfully.",
                "session_id": "sess-stale-recovery",
            }
        )
        result_obj = self._make_stale_result(stdout=valid_completed_jsonl)
        parsed = json.loads(_build_skill_result(result_obj).to_json())
        assert parsed["success"] is True
        assert parsed["subtype"] == "recovered_from_stale"

    def test_stale_with_empty_stdout_returns_failure(self):
        """Stale session with no stdout — original failure response preserved."""
        result_obj = self._make_stale_result(stdout="")
        parsed = json.loads(_build_skill_result(result_obj).to_json())
        assert parsed["success"] is False
        assert parsed["subtype"] == "stale"

    def test_stale_with_error_result_returns_failure(self):
        """Stale session where the result record has is_error=True — not recovered."""
        error_jsonl = json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "result": "Tool call failed.",
                "session_id": "sess-err",
            }
        )
        result_obj = self._make_stale_result(stdout=error_jsonl)
        parsed = json.loads(_build_skill_result(result_obj).to_json())
        assert parsed["success"] is False
        assert parsed["subtype"] == "stale"


class TestBuildSkillResultDataConfirmedPropagation:
    """_build_skill_result propagates data_confirmed for provenance bypass."""

    def test_stale_recovery_channel_a_with_valid_stdout_succeeds(self):
        """STALE + CHANNEL_A + valid stdout → recovered_from_stale.

        When stale monitor fires simultaneously with heartbeat (CHANNEL_A),
        the stale recovery path succeeds via the stdout content check.
        CHANNEL_A guarantees stdout has valid type=result content, so
        can_attempt_stale_recovery passes and _compute_success returns True.
        """
        result = _make_result(
            stdout=(
                '{"type":"result","subtype":"success",'
                '"result":"task done %%ORDER_UP%%","is_error":false,"session_id":"s1"}'
            ),
            termination_reason=TerminationReason.STALE,
            channel_confirmation=ChannelConfirmation.CHANNEL_A,
        )
        skill_result = _build_skill_result(
            result,
            completion_marker="%%ORDER_UP%%",
            skill_command="cmd",
            audit=None,
        )
        assert skill_result.success is True
        assert skill_result.subtype == "recovered_from_stale"

    def test_stale_recovery_data_confirmed_true_preserves_existing_behavior(self):
        """STALE with empty stdout and data_confirmed=True (default) stays False."""
        result = _make_result(
            stdout="",
            termination_reason=TerminationReason.STALE,
            channel_confirmation=ChannelConfirmation.CHANNEL_A,
        )
        skill_result = _build_skill_result(
            result,
            completion_marker="%%ORDER_UP%%",
            skill_command="cmd",
            audit=None,
        )
        assert skill_result.success is False
        assert skill_result.subtype == "stale"

    def test_completed_empty_result_data_confirmed_false_produces_success(self):
        """COMPLETED with empty stdout and data_confirmed=False uses provenance bypass."""
        result = _make_result(
            stdout='{"type":"result","subtype":"success","result":"","is_error":false,'
            '"session_id":"s1"}',
            returncode=-15,
            termination_reason=TerminationReason.COMPLETED,
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
        )
        skill_result = _build_skill_result(
            result,
            completion_marker="%%ORDER_UP%%",
            skill_command="cmd",
            audit=None,
        )
        assert skill_result.success is True  # FAILS before fix: False
        assert skill_result.needs_retry is False  # FAILS before fix: True

    def test_completed_empty_result_data_confirmed_true_is_still_retriable(self):
        """COMPLETED with empty result and data_confirmed=True remains a retriable anomaly."""
        result = _make_result(
            stdout='{"type":"result","subtype":"success","result":"","is_error":false,'
            '"session_id":"s1"}',
            returncode=-15,
            termination_reason=TerminationReason.COMPLETED,
            channel_confirmation=ChannelConfirmation.CHANNEL_A,
        )
        skill_result = _build_skill_result(
            result,
            completion_marker="%%ORDER_UP%%",
            skill_command="cmd",
            audit=None,
        )
        assert skill_result.success is False
        assert skill_result.needs_retry is True


def test_context_exhaustion_marker_is_used_in_detection():
    """_is_context_exhausted() uses the CONTEXT_EXHAUSTION_MARKER constant."""
    from autoskillit.execution.session import ClaudeSessionResult

    session = ClaudeSessionResult(
        subtype="success",
        is_error=True,
        result=CONTEXT_EXHAUSTION_MARKER,
        session_id="s1",
    )
    assert session._is_context_exhausted() is True


class TestCrashSessionLog:
    """flush_session_log is called with success=False when runner raises."""

    @pytest.mark.anyio
    async def test_crash_session_log_written_when_runner_raises(
        self, monkeypatch, tool_ctx, tmp_path
    ):
        """flush_session_log is called with CRASHED termination_reason when runner raises."""
        from autoskillit.execution.headless import run_headless_core

        flushed: list[dict] = []

        def fake_flush(**kwargs: object) -> None:
            flushed.append(dict(kwargs))

        monkeypatch.setattr("autoskillit.execution.flush_session_log", fake_flush)

        async def raising_runner(*args: object, **kwargs: object) -> None:
            raise RuntimeError("simulated crash")

        tool_ctx.runner = raising_runner  # type: ignore[assignment]

        skill_result = await run_headless_core(
            "/investigate test", cwd=str(tmp_path), ctx=tool_ctx
        )
        assert isinstance(skill_result, SkillResult)
        assert skill_result.success is False
        assert skill_result.subtype == "crashed"
        assert skill_result.is_error is True
        assert skill_result.exit_code == -1
        assert skill_result.needs_retry is False
        assert "simulated crash" in skill_result.result

        crash_calls = [f for f in flushed if f.get("termination_reason") == "CRASHED"]
        assert len(crash_calls) >= 1
        assert crash_calls[0]["success"] is False

    @pytest.mark.anyio
    async def test_crash_exception_text_passed_to_flush(self, monkeypatch, tool_ctx, tmp_path):
        """flush_session_log receives exception_text with the traceback on crash."""
        from autoskillit.execution.headless import run_headless_core

        flushed: list[dict] = []

        def fake_flush(**kwargs: object) -> None:
            flushed.append(dict(kwargs))

        monkeypatch.setattr("autoskillit.execution.flush_session_log", fake_flush)

        async def raising_runner(*args: object, **kwargs: object) -> None:
            raise OSError("disk full")

        tool_ctx.runner = raising_runner  # type: ignore[assignment]

        await run_headless_core("/investigate test", cwd=str(tmp_path), ctx=tool_ctx)

        crash_calls = [f for f in flushed if f.get("termination_reason") == "CRASHED"]
        assert len(crash_calls) >= 1
        assert "exception_text" in crash_calls[0]
        assert "OSError: disk full" in crash_calls[0]["exception_text"]

    @pytest.mark.anyio
    async def test_crash_logs_exception_with_logger_error(self, monkeypatch, tool_ctx, tmp_path):
        """Runner crash is logged at ERROR level with exc_info."""
        from autoskillit.execution.headless import run_headless_core

        monkeypatch.setattr("autoskillit.execution.flush_session_log", lambda **kw: None)

        async def raising_runner(*args: object, **kwargs: object) -> None:
            raise ValueError("bad input")

        tool_ctx.runner = raising_runner  # type: ignore[assignment]

        with patch("autoskillit.execution.headless.logger") as mock_logger:
            result = await run_headless_core("/investigate test", cwd=str(tmp_path), ctx=tool_ctx)
            mock_logger.error.assert_called_once()
            call_kwargs = mock_logger.error.call_args
            assert call_kwargs[1].get("exc_info")
            assert result.success is False


class TestCancelledSessionLog:
    """flush_session_log is called with success=False when runner raises CancelledError."""

    @pytest.mark.anyio
    async def test_run_headless_core_cancellation_still_flushes_session_log(
        self, monkeypatch, tool_ctx, tmp_path
    ):
        """flush_session_log is called with CANCELLED termination_reason on cancellation."""
        import anyio

        from autoskillit.execution.headless import run_headless_core

        flushed: list[dict] = []

        def fake_flush(**kwargs: object) -> None:
            flushed.append(dict(kwargs))

        monkeypatch.setattr("autoskillit.execution.flush_session_log", fake_flush)

        async def cancelled_runner(*args: object, **kwargs: object) -> None:
            raise anyio.get_cancelled_exc_class()()

        tool_ctx.runner = cancelled_runner  # type: ignore[assignment]

        with pytest.raises(anyio.get_cancelled_exc_class()):
            await run_headless_core("/investigate test", cwd=str(tmp_path), ctx=tool_ctx)

        cancel_calls = [f for f in flushed if f.get("termination_reason") == "CANCELLED"]
        assert len(cancel_calls) == 1
        assert cancel_calls[0]["success"] is False
        assert cancel_calls[0]["subtype"] == "cancelled"
        assert cancel_calls[0]["exit_code"] == -1
        assert cancel_calls[0]["session_id"] == ""
        assert cancel_calls[0]["pid"] == 0

    @pytest.mark.anyio
    async def test_run_headless_core_cancelled_error_propagates_after_flush(
        self, monkeypatch, tool_ctx, tmp_path
    ):
        """CancelledError re-raises after flush — never swallowed."""
        import anyio

        from autoskillit.execution.headless import run_headless_core

        monkeypatch.setattr("autoskillit.execution.flush_session_log", lambda **kw: None)

        async def cancelled_runner(*args: object, **kwargs: object) -> None:
            raise anyio.get_cancelled_exc_class()()

        tool_ctx.runner = cancelled_runner  # type: ignore[assignment]

        with pytest.raises(anyio.get_cancelled_exc_class()):
            await run_headless_core("/investigate test", cwd=str(tmp_path), ctx=tool_ctx)

    @pytest.mark.anyio
    async def test_run_headless_core_flush_failure_does_not_suppress_cancellation(
        self, monkeypatch, tool_ctx, tmp_path
    ):
        """Even if flush_session_log raises, cancellation still propagates."""
        import anyio

        from autoskillit.execution.headless import run_headless_core

        def exploding_flush(**kwargs: object) -> None:
            raise OSError("log disk full")

        monkeypatch.setattr("autoskillit.execution.flush_session_log", exploding_flush)

        async def cancelled_runner(*args: object, **kwargs: object) -> None:
            raise anyio.get_cancelled_exc_class()()

        tool_ctx.runner = cancelled_runner  # type: ignore[assignment]

        with pytest.raises(anyio.get_cancelled_exc_class()):
            await run_headless_core("/investigate test", cwd=str(tmp_path), ctx=tool_ctx)


class TestRetryBudgetEnforcement:
    """_build_skill_result enforces max_consecutive_retries budget."""

    def _make_retry_record(self, skill_command: str) -> "object":
        from datetime import UTC, datetime

        from autoskillit.pipeline.audit import FailureRecord

        return FailureRecord(
            timestamp=datetime.now(UTC).isoformat(),
            skill_command=skill_command,
            exit_code=-1,
            subtype="stale",
            needs_retry=True,
            retry_reason="stale",
            stderr="",
        )

    def _make_audit_with_failures(self, skill_command: str, n: int) -> "object":
        from autoskillit.pipeline.audit import DefaultAuditLog

        audit = DefaultAuditLog()
        for _ in range(n):
            audit.record_failure(self._make_retry_record(skill_command))  # type: ignore[arg-type]
        return audit

    def test_budget_below_threshold_preserves_needs_retry(self) -> None:
        """Fewer than max_consecutive_retries failures: needs_retry=True is preserved."""
        audit = self._make_audit_with_failures("/autoskillit:open-pr", 2)
        result = _make_result(returncode=-1, termination_reason=TerminationReason.STALE)
        sr = _build_skill_result(
            result,
            skill_command="/autoskillit:open-pr",
            audit=audit,  # type: ignore[arg-type]
            max_consecutive_retries=3,
        )
        assert sr.needs_retry is True
        assert sr.retry_reason == RetryReason.STALE

    def test_budget_at_threshold_overrides_needs_retry_to_false(self) -> None:
        """At exactly max_consecutive_retries prior failures: needs_retry is overridden."""
        audit = self._make_audit_with_failures("/autoskillit:open-pr", 3)
        result = _make_result(returncode=-1, termination_reason=TerminationReason.STALE)
        sr = _build_skill_result(
            result,
            skill_command="/autoskillit:open-pr",
            audit=audit,  # type: ignore[arg-type]
            max_consecutive_retries=3,
        )
        assert sr.needs_retry is False
        assert sr.retry_reason == RetryReason.BUDGET_EXHAUSTED

    def test_budget_no_audit_does_not_override(self) -> None:
        """Without an audit log, budget enforcement is skipped; needs_retry unchanged."""
        result = _make_result(returncode=-1, termination_reason=TerminationReason.STALE)
        sr = _build_skill_result(
            result,
            skill_command="/autoskillit:open-pr",
            audit=None,
            max_consecutive_retries=3,
        )
        assert sr.needs_retry is True
        assert sr.retry_reason == RetryReason.STALE

    def test_budget_other_skill_command_not_counted(self) -> None:
        """Consecutive failures for a different skill_command don't exhaust this skill's budget."""
        audit = self._make_audit_with_failures("/autoskillit:other-skill", 3)
        result = _make_result(returncode=-1, termination_reason=TerminationReason.STALE)
        sr = _build_skill_result(
            result,
            skill_command="/autoskillit:open-pr",
            audit=audit,  # type: ignore[arg-type]
            max_consecutive_retries=3,
        )
        assert sr.needs_retry is True
        assert sr.retry_reason == RetryReason.STALE

    def test_budget_applies_to_normal_path_context_exhaustion(self) -> None:
        """Budget applies to the normal path (not just stale), e.g. context exhaustion."""
        audit = self._make_audit_with_failures("/autoskillit:open-pr", 3)
        result = _make_result(
            returncode=0,
            stdout=_context_exhausted_session_json(),
            channel_confirmation=ChannelConfirmation.CHANNEL_A,
        )
        sr = _build_skill_result(
            result,
            skill_command="/autoskillit:open-pr",
            audit=audit,  # type: ignore[arg-type]
            max_consecutive_retries=3,
        )
        assert sr.needs_retry is False
        assert sr.retry_reason == RetryReason.BUDGET_EXHAUSTED


# ---------------------------------------------------------------------------
# Test: _inject_cwd_anchor (Step 1a)
# ---------------------------------------------------------------------------


class TestInjectCwdAnchor:
    def test_appends_cwd_directive(self):
        from autoskillit.execution.commands import _inject_cwd_anchor

        result = _inject_cwd_anchor("/investigate foo", "/some/clone/path")
        assert "WORKING DIRECTORY ANCHOR" in result
        assert "/some/clone/path" in result
        assert "/investigate foo" in result

    def test_preserves_original_command(self):
        from autoskillit.execution.commands import _inject_cwd_anchor

        original = "Use /autoskillit:make-plan detailed prompt here"
        result = _inject_cwd_anchor(original, "/clone/dir")
        assert result.startswith(original)

    def test_directive_mentions_temp(self):
        from autoskillit.execution.commands import _inject_cwd_anchor

        result = _inject_cwd_anchor("cmd", "/wd")
        assert ".autoskillit/temp/" in result

    def test_skips_when_cwd_empty(self):
        from autoskillit.execution.commands import _inject_cwd_anchor

        result = _inject_cwd_anchor("cmd", "")
        assert result == "cmd"

    def test_skips_when_cwd_relative(self):
        from autoskillit.execution.commands import _inject_cwd_anchor

        result = _inject_cwd_anchor("cmd", "relative/path")
        assert result == "cmd"


# ---------------------------------------------------------------------------
# Test: _inject_narration_suppression
# ---------------------------------------------------------------------------


class TestInjectNarrationSuppression:
    def test_appends_efficiency_directive(self):
        from autoskillit.execution.commands import _inject_narration_suppression

        result = _inject_narration_suppression("Use /make-plan foo")
        assert "EFFICIENCY DIRECTIVE" in result

    def test_preserves_original_command(self):
        from autoskillit.execution.commands import _inject_narration_suppression

        original = "Use /autoskillit:investigate problem"
        result = _inject_narration_suppression(original)
        assert result.startswith(original)

    def test_directive_targets_inter_tool_prose(self):
        from autoskillit.execution.commands import _inject_narration_suppression

        result = _inject_narration_suppression("cmd")
        # Directive must reference tool calls specifically
        assert "between tool calls" in result

    def test_directive_exempts_final_response(self):
        from autoskillit.execution.commands import _inject_narration_suppression

        result = _inject_narration_suppression("cmd")
        # Must not suppress the final response where structured output tokens live
        assert "final response" in result
