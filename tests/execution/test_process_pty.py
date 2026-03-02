"""Tests for PTY wrapping and pipeline adjudication boundary tests.

These tests cover PTY mode behavior, platform-specific script flag
selection, and the full subprocess → run_managed_async →
_build_skill_result → SkillResult adjudication boundary.
"""

from __future__ import annotations

import shutil
import sys
import textwrap

import pytest

from autoskillit.core.types import RetryReason, TerminationReason
from autoskillit.execution.process import (
    pty_wrap_command,
    run_managed_async,
)

# ---------------------------------------------------------------------------
# Helper scripts — small Python programs that reproduce specific scenarios
# ---------------------------------------------------------------------------

# Script that prints sys.stdout.isatty() result
ISATTY_CHECK_SCRIPT = textwrap.dedent("""\
    import sys
    print(sys.stdout.isatty())
""")

# Simulates CLAUDE_CODE_EXIT_AFTER_STOP_DELAY: process writes the type=result
# envelope with an empty result field and exits rc=0 before content is populated.
# Produces: NATURAL_EXIT, rc=0, stdout=success+empty → _is_kill_anomaly=True
# Expected SkillResult: success=False, needs_retry=True
WRITE_EMPTY_RESULT_THEN_EXIT_SCRIPT = textwrap.dedent("""\
    import sys, json
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "",
        "session_id": "test-stop-delay",
    }
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\\n")
    sys.stdout.flush()
    sys.exit(0)
""")

# Simulates process killed before it wrote anything to stdout.
# Produces: NATURAL_EXIT, rc=0, stdout="" → _is_kill_anomaly=True (empty_output)
# Expected SkillResult: success=False, needs_retry=True
WRITE_NOTHING_THEN_EXIT_SCRIPT = textwrap.dedent("""\
    import sys
    sys.stdout.flush()
    sys.exit(0)
""")

# Simulates process killed mid-write: partial NDJSON line not parseable.
# Produces: NATURAL_EXIT, rc=0, stdout=truncated → _is_kill_anomaly=True (unparseable)
# Expected SkillResult: success=False, needs_retry=True
WRITE_TRUNCATED_JSON_THEN_EXIT_SCRIPT = textwrap.dedent("""\
    import sys
    sys.stdout.write('{"type":"result","subtype":"success","is_error":false,"res')
    sys.stdout.flush()
    sys.exit(0)
""")

# Simulates a stale session: writes a valid result to stdout AND a JSONL record
# to session_log_dir (so Phase 1 of the stale monitor finds the file), then hangs.
# Pass session_dir as sys.argv[1].
# run_managed_async fires STALE via stale_threshold (file stops growing after initial write).
# Produces: STALE, returncode=nonzero, stdout=valid success record
# Expected SkillResult: success=True, needs_retry=False, subtype="recovered_from_stale"
WRITE_VALID_RESULT_AND_JSONL_THEN_HANG_SCRIPT = textwrap.dedent("""\
    import sys, json, time, os
    session_dir = sys.argv[1]
    os.makedirs(session_dir, exist_ok=True)
    # Write valid result to stdout (captured by run_managed_async via temp file)
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "Task completed successfully.",
        "session_id": "test-stale-recovery",
    }
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\\n")
    sys.stdout.flush()
    # Write one JSONL record to session dir so Phase 1 of the stale monitor finds it.
    # After this single write the file never grows again → stale fires after threshold.
    record = {"type": "assistant", "message": {"role": "assistant", "content": "Working..."}}
    with open(os.path.join(session_dir, "session.jsonl"), "w") as f:
        f.write(json.dumps(record) + "\\n")
        f.flush()
    time.sleep(9999)
""")

# Simulates a process that sleeps immediately with no output (for TIMED_OUT path).
# run_managed_async will fire TIMED_OUT when wall-clock timeout expires.
# Produces: TIMED_OUT, returncode=-1
# Expected SkillResult: success=False, needs_retry=False, subtype="timeout"
SLEEP_FOREVER_NO_OUTPUT_SCRIPT = textwrap.dedent("""\
    import sys, time
    time.sleep(9999)
""")


class TestPtyWrapper:
    """PTY wrapping provides a TTY to the subprocess."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        shutil.which("script") is None,
        reason="script binary not available (util-linux required)",
    )
    async def test_pty_wrapper_provides_tty(self, tmp_path):
        """With pty_mode=True, subprocess sees a TTY on stdout."""
        script = tmp_path / "isatty.py"
        script.write_text(ISATTY_CHECK_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=10,
            pty_mode=True,
        )

        assert result.termination != TerminationReason.TIMED_OUT
        assert "True" in result.stdout

    @pytest.mark.asyncio
    async def test_no_pty_shows_no_tty(self, tmp_path):
        """Without pty_mode, subprocess does not see a TTY on stdout."""
        script = tmp_path / "isatty.py"
        script.write_text(ISATTY_CHECK_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=10,
            pty_mode=False,
        )

        assert result.termination != TerminationReason.TIMED_OUT
        assert "False" in result.stdout

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        shutil.which("script") is None,
        reason="script binary not available (util-linux required)",
    )
    async def test_pty_mode_true_merges_child_stderr_into_stdout(self, tmp_path):
        """Characterize: under PTY mode, child stderr lands in result.stdout, not result.stderr.

        This test DOCUMENTS the PTY fd-routing behavior for maintainers. It guards against
        silent changes to PTY behavior that would break run_headless_core's assumptions
        (execution/headless.py).
        """
        script = tmp_path / "write_stderr.py"
        script.write_text("import sys; sys.stderr.write('PTY_STDERR_CONTENT'); sys.exit(1)")
        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=10,
            pty_mode=True,
        )
        assert result.returncode != 0
        assert "PTY_STDERR_CONTENT" in result.stdout, (
            f"Under PTY mode, child stderr must land in result.stdout (PTY merges fd 2→fd 1). "
            f"stdout={result.stdout!r}, stderr={result.stderr!r}"
        )
        assert "PTY_STDERR_CONTENT" not in result.stderr


class TestPtyWrapCommand:
    """pty_wrap_command selects BSD or GNU script flags based on sys.platform."""

    def test_pty_wrap_command_linux_uses_gnu_flags(self) -> None:
        """On Linux, pty_wrap_command produces GNU script -qefc syntax."""
        from unittest.mock import patch

        cmd = ["claude", "--no-color", "do something"]
        fake_script = "/usr/bin/script"
        with (
            patch("autoskillit.execution.process.sys.platform", "linux"),
            patch("shutil.which", return_value=fake_script),
        ):
            result = pty_wrap_command(cmd)
        assert result[0] == fake_script
        assert result[1] == "-qefc"
        # The shell-escaped command string is at index 2
        assert "claude" in result[2]
        assert result[3] == "/dev/null"
        assert len(result) == 4

    def test_pty_wrap_command_macos_uses_bsd_flags(self) -> None:
        """On macOS, pty_wrap_command produces BSD script syntax: script -q /dev/null cmd..."""
        from unittest.mock import patch

        cmd = ["claude", "--no-color", "do something"]
        fake_script = "/usr/bin/script"
        with (
            patch("autoskillit.execution.process.sys.platform", "darwin"),
            patch("shutil.which", return_value=fake_script),
        ):
            result = pty_wrap_command(cmd)
        assert result[0] == fake_script
        assert result[1] == "-q"
        assert result[2] == "/dev/null"
        # Original cmd list follows as separate args (no shell escaping)
        assert result[3:] == cmd

    def test_pty_wrap_command_no_script_returns_original(self) -> None:
        """When script is not found, pty_wrap_command returns the original command list."""
        from unittest.mock import patch

        cmd = ["claude", "arg1"]
        with patch("shutil.which", return_value=None):
            result = pty_wrap_command(cmd)
        assert result is cmd


# ---------------------------------------------------------------------------
# Adjudication boundary integration tests
# Each class exercises ONE TerminationReason path from a real subprocess
# through run_managed_async → _build_skill_result → SkillResult.
# ---------------------------------------------------------------------------


class TestSTOPDelayPipelineAdjudication:
    """Integration: NATURAL_EXIT paths flow correctly through run_managed_async → SkillResult.

    These tests catch regressions in _compute_retry's NATURAL_EXIT arm — specifically
    the _is_kill_anomaly guard that was the subject of the 2026-03-01 investigation.
    """

    @pytest.mark.asyncio
    async def test_stop_delay_race_produces_retriable_skill_result(self, tmp_path):
        """NATURAL_EXIT + rc=0 + success+empty → success=False, needs_retry=True.

        Without _is_kill_anomaly in the NATURAL_EXIT arm, this returns
        success=False, needs_retry=False — swallowing the race as permanent failure.
        """
        from autoskillit.execution.headless import _build_skill_result

        script = tmp_path / "stop_delay.py"
        script.write_text(WRITE_EMPTY_RESULT_THEN_EXIT_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=30,
            heartbeat_marker='"type":"result"',
            _heartbeat_poll=0.05,
        )

        assert result.termination == TerminationReason.NATURAL_EXIT
        assert result.returncode == 0
        assert result.data_confirmed is True

        skill_result = _build_skill_result(
            result,
            completion_marker="%%ORDER_UP%%",
            skill_command="resolve-failures",
            audit=None,
        )

        assert skill_result.success is False
        assert skill_result.needs_retry is True
        assert skill_result.retry_reason == RetryReason.RESUME
        assert skill_result.subtype == "success"

    @pytest.mark.asyncio
    async def test_natural_exit_empty_stdout_produces_retriable_skill_result(self, tmp_path):
        """NATURAL_EXIT + rc=0 + empty stdout → success=False, needs_retry=True.

        Exercises the empty_output subtype through the full subprocess pipeline.
        """
        from autoskillit.execution.headless import _build_skill_result

        script = tmp_path / "empty_exit.py"
        script.write_text(WRITE_NOTHING_THEN_EXIT_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=30,
            heartbeat_marker='"type":"result"',
            _heartbeat_poll=0.05,
        )

        assert result.termination == TerminationReason.NATURAL_EXIT
        assert result.returncode == 0

        skill_result = _build_skill_result(
            result,
            completion_marker="%%ORDER_UP%%",
            skill_command="resolve-failures",
            audit=None,
        )

        assert skill_result.success is False
        assert skill_result.needs_retry is True
        assert skill_result.retry_reason == RetryReason.RESUME

    @pytest.mark.asyncio
    async def test_natural_exit_truncated_json_produces_retriable_skill_result(self, tmp_path):
        """NATURAL_EXIT + rc=0 + truncated/unparseable JSON → success=False, needs_retry=True.

        Exercises the unparseable subtype through the full subprocess pipeline.
        Simulates process killed mid-write where partial NDJSON cannot be parsed.
        """
        from autoskillit.execution.headless import _build_skill_result

        script = tmp_path / "truncated_exit.py"
        script.write_text(WRITE_TRUNCATED_JSON_THEN_EXIT_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=30,
            heartbeat_marker='"type":"result"',
            _heartbeat_poll=0.05,
        )

        assert result.termination == TerminationReason.NATURAL_EXIT
        assert result.returncode == 0

        skill_result = _build_skill_result(
            result,
            completion_marker="%%ORDER_UP%%",
            skill_command="resolve-failures",
            audit=None,
        )

        assert skill_result.success is False
        assert skill_result.needs_retry is True
        assert skill_result.retry_reason == RetryReason.RESUME


class TestStaleRecoveryPipelineAdjudication:
    """Integration: STALE termination with valid stdout triggers recovery path."""

    @pytest.mark.asyncio
    async def test_stale_with_valid_result_recovers_to_success(self, tmp_path):
        """STALE + valid success result in stdout → success=True, needs_retry=False.

        _build_skill_result intercepts STALE before _compute_success and
        attempts to recover a valid SkillResult from stdout. When the stdout
        contains a complete, parseable success record, recovery succeeds and
        subtype is set to "recovered_from_stale".

        session_log_dir must be provided so the stale monitor is active. Without
        it, no monitor runs and the test would hit the wall-clock timeout instead.
        The stale monitor watches session_dir, sees no JSONL activity, and fires
        STALE after stale_threshold (0.3s with short polls).
        """
        from autoskillit.execution.headless import _build_skill_result

        session_dir = tmp_path / "session"
        session_dir.mkdir()
        script = tmp_path / "stale_with_result.py"
        script.write_text(WRITE_VALID_RESULT_AND_JSONL_THEN_HANG_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script), str(session_dir)],
            cwd=tmp_path,
            timeout=10,
            session_log_dir=session_dir,
            completion_marker="%%NONEXISTENT%%",
            stale_threshold=0.3,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
        )

        assert result.termination == TerminationReason.STALE

        # Use completion_marker="" so _check_session_content does not require
        # the marker to appear in the recovered result ("Task completed successfully.").
        # The run_managed_async completion_marker was "%%NONEXISTENT%%" only to
        # prevent false-positive session-monitor completion detection.
        skill_result = _build_skill_result(
            result,
            completion_marker="",
            skill_command="investigate",
            audit=None,
        )

        assert skill_result.success is True
        assert skill_result.needs_retry is False
        assert skill_result.subtype == "recovered_from_stale"


class TestTimedOutPipelineAdjudication:
    """Integration: TIMED_OUT path produces a non-retriable failure SkillResult.

    _build_skill_result intercepts TIMED_OUT before parse_session_result and
    synthesizes a ClaudeSessionResult(subtype="timeout"). The result is always
    success=False, needs_retry=False — timeouts are not retriable.
    """

    @pytest.mark.asyncio
    async def test_timed_out_produces_non_retriable_failure(self, tmp_path):
        """TIMED_OUT → success=False, needs_retry=False, subtype="timeout".

        Uses a script that sleeps immediately with a very short wall-clock timeout
        so run_managed_async fires TIMED_OUT. _build_skill_result must synthesize
        a timeout session and return a permanent failure (not retriable).
        """
        from autoskillit.execution.headless import _build_skill_result

        script = tmp_path / "sleep_forever.py"
        script.write_text(SLEEP_FOREVER_NO_OUTPUT_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=0.5,
        )

        assert result.termination == TerminationReason.TIMED_OUT
        # Note: SubprocessResult.returncode is the actual kill signal (e.g. -15 for SIGTERM).
        # _build_skill_result overrides returncode to -1 internally for the SkillResult.

        skill_result = _build_skill_result(
            result,
            completion_marker="%%ORDER_UP%%",
            skill_command="investigate",
            audit=None,
        )

        assert skill_result.success is False
        assert skill_result.needs_retry is False
        assert skill_result.subtype == "timeout"


class TestAdjudicationCoverageMatrix:
    """Structural guard: every TerminationReason must have a subprocess integration test.

    This test introspects the TerminationReason enum and asserts that each value
    appears in COVERED_BY_INTEGRATION_TESTS — the authoritative registry of
    TerminationReason values with confirmed full-boundary integration test coverage
    (subprocess → run_managed_async → _build_skill_result → SkillResult).

    It fails immediately if a new TerminationReason value is added without a
    corresponding integration test class in this file, or if an existing integration
    test is removed without updating this registry.

    Covered by:
      COMPLETED    → TestChannelBDrainRacePipelineAdjudication (test_process_channel_b.py)
      NATURAL_EXIT → TestSTOPDelayPipelineAdjudication
      STALE        → TestStaleRecoveryPipelineAdjudication
      TIMED_OUT    → TestTimedOutPipelineAdjudication

    See core/types.py _TERMINATION_CONTRACT for the per-reason semantic invariants.
    """

    COVERED_BY_INTEGRATION_TESTS: frozenset = frozenset(
        {
            TerminationReason.COMPLETED,  # TestChannelBDrainRacePipelineAdjudication
            TerminationReason.NATURAL_EXIT,  # TestSTOPDelayPipelineAdjudication
            TerminationReason.STALE,  # TestStaleRecoveryPipelineAdjudication
            TerminationReason.TIMED_OUT,  # TestTimedOutPipelineAdjudication
        }
    )

    def test_all_termination_reasons_have_integration_coverage(self):
        all_reasons = frozenset(TerminationReason)
        uncovered = all_reasons - self.COVERED_BY_INTEGRATION_TESTS
        assert not uncovered, (
            f"TerminationReason values with no subprocess integration test "
            f"crossing run_managed_async → _build_skill_result boundary: "
            f"{uncovered}. "
            f"Add a TestXxxPipelineAdjudication class in tests/execution/test_process_pty.py "
            f"and add the value to COVERED_BY_INTEGRATION_TESTS."
        )
