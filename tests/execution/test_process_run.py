"""Integration tests for normal subprocess run, stdin, timeout, temp I/O, and logging.

These tests use REAL subprocesses (small Python scripts) to reproduce
exact failure modes. They validate that temp-file I/O eliminates
pipe blocking, that stdin round-trips work, and that the sync and async
variants behave identically.

NO MOCKS — that's the whole point.
"""

from __future__ import annotations

import sys
import textwrap
import time
from pathlib import Path

import anyio
import psutil
import pytest

from autoskillit.core.types import TerminationReason
from autoskillit.execution.process import (
    read_temp_output,
    run_managed_async,
    run_managed_sync,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

# ---------------------------------------------------------------------------
# Helper scripts — small Python programs that reproduce specific scenarios
# ---------------------------------------------------------------------------

# Script that forks a child: parent writes output and exits,
# child sleeps holding inherited FDs.
PARENT_EXITS_CHILD_HOLDS_FD = textwrap.dedent("""\
    import os, sys, time
    pid = os.fork()
    if pid == 0:
        # Child: sleep holding inherited stdout FD
        time.sleep(30)
        sys.exit(0)
    else:
        # Parent: write output and exit
        sys.stdout.write("parent output line\\n")
        sys.stdout.flush()
        sys.exit(0)
""")

# Script that sleeps forever (simulates Claude CLI hang)
HANG_FOREVER_SCRIPT = textwrap.dedent("""\
    import sys, time
    sys.stdout.write("before hang\\n")
    sys.stdout.flush()
    time.sleep(3600)
""")

# Script that writes multi-line output and exits cleanly
CLEAN_OUTPUT_SCRIPT = textwrap.dedent("""\
    import sys
    for i in range(10):
        sys.stdout.write(f"line {i}\\n")
    sys.stdout.flush()
""")

# Script that reads stdin and echoes it
ECHO_STDIN_SCRIPT = textwrap.dedent("""\
    import sys
    data = sys.stdin.read()
    sys.stdout.write(f"echo: {data}")
    sys.stdout.flush()
""")


class TestNormalCompletion:
    """Normal subprocess completion captures all output."""

    @pytest.mark.anyio
    async def test_normal_completion_captures_full_output(self, tmp_path):
        """Process writes multi-line output and exits — all captured."""
        script = tmp_path / "clean.py"
        script.write_text(CLEAN_OUTPUT_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=10,
        )

        assert result.termination != TerminationReason.TIMED_OUT
        assert result.returncode == 0
        for i in range(10):
            assert f"line {i}" in result.stdout

    def test_normal_completion_sync(self, tmp_path):
        """Same test for sync variant."""
        script = tmp_path / "clean.py"
        script.write_text(CLEAN_OUTPUT_SCRIPT)

        result = run_managed_sync(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=10,
        )

        assert result.termination != TerminationReason.TIMED_OUT
        assert result.returncode == 0
        for i in range(10):
            assert f"line {i}" in result.stdout


class TestStdinInput:
    """Stdin input via temp file works correctly."""

    @pytest.mark.anyio
    async def test_stdin_input_roundtrip(self, tmp_path):
        """Pass input via temp file, verify it's received and echoed."""
        script = tmp_path / "echo.py"
        script.write_text(ECHO_STDIN_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=10,
            input_data="hello world",
        )

        assert result.termination != TerminationReason.TIMED_OUT
        assert result.returncode == 0
        assert "echo: hello world" in result.stdout

    def test_stdin_input_roundtrip_sync(self, tmp_path):
        """Same test for sync variant."""
        script = tmp_path / "echo.py"
        script.write_text(ECHO_STDIN_SCRIPT)

        result = run_managed_sync(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=10,
            input_data="hello world",
        )

        assert result.termination != TerminationReason.TIMED_OUT
        assert result.returncode == 0
        assert "echo: hello world" in result.stdout


class TestTimeoutKillsHangingProcess:
    """Timeout fires and kills when process hangs."""

    @pytest.mark.anyio
    async def test_timeout_fires_and_kills_hanging_process(self, tmp_path):
        """Process sleeps forever, timeout kills it, partial output returned."""
        script = tmp_path / "hang.py"
        script.write_text(HANG_FOREVER_SCRIPT)

        start = time.monotonic()
        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=2,
        )
        elapsed = time.monotonic() - start

        assert result.termination == TerminationReason.TIMED_OUT
        assert elapsed < 5, f"Should return within ~2s timeout, took {elapsed:.1f}s"
        assert "before hang" in result.stdout  # Partial output captured
        # Process should be dead
        import anyio

        await anyio.sleep(0.5)
        assert not psutil.pid_exists(result.pid)


class TestTempFileIOEliminatesPipeBlocking:
    """Temp file I/O prevents pipe-inheritance blocking."""

    @pytest.mark.anyio
    async def test_child_holds_fd_does_not_block_read(self, tmp_path):
        """Parent exits, child holds FD — temp file read doesn't block."""
        script = tmp_path / "parent_child.py"
        script.write_text(PARENT_EXITS_CHILD_HOLDS_FD)

        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=10,
        )

        assert result.termination != TerminationReason.TIMED_OUT, (
            "Read should not block even though child holds FD"
        )
        assert result.returncode == 0
        assert "parent output line" in result.stdout

    def test_child_holds_fd_does_not_block_read_sync(self, tmp_path):
        """Same test for sync variant."""
        script = tmp_path / "parent_child.py"
        script.write_text(PARENT_EXITS_CHILD_HOLDS_FD)

        result = run_managed_sync(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=10,
        )

        assert result.termination != TerminationReason.TIMED_OUT
        assert result.returncode == 0
        assert "parent output line" in result.stdout


class TestReadTempOutputLogging:
    """OSError during temp file read should produce a warning log."""

    @pytest.fixture(autouse=True)
    def _reset_structlog_config(self):
        """Sync process and _process_io loggers with the current structlog config.

        Scoped to this test class only — no cross-module mutation.
        _process_io.logger must also be synced because read_temp_output lives there.
        """
        import structlog

        import autoskillit.execution.process._process_io as io_mod
        import autoskillit.execution.process as proc_mod

        structlog.reset_defaults()
        current_procs = structlog.get_config()["processors"]
        old_procs = getattr(proc_mod.logger, "_processors", None)
        if old_procs is not None:
            proc_mod.logger._processors = current_procs
        old_io_procs = getattr(io_mod.logger, "_processors", None)
        if old_io_procs is not None:
            io_mod.logger._processors = current_procs
        yield
        structlog.reset_defaults()
        if old_procs is not None:
            proc_mod.logger._processors = old_procs
        if old_io_procs is not None:
            io_mod.logger._processors = old_io_procs

    def test_oserror_logs_warning(self):
        """OSError during temp file read should produce a warning log."""
        import structlog

        with structlog.testing.capture_logs() as logs:
            stdout, stderr = read_temp_output(
                Path("/nonexistent/stdout.tmp"),
                Path("/nonexistent/stderr.tmp"),
            )
        assert stdout == ""
        assert stderr == ""
        assert any("Failed to read" in str(log.get("event", "")) for log in logs)


class TestSubprocessResultAndRunnerTypes:
    """Tests for SubprocessResult in types.py and SubprocessRunner protocol."""

    def test_subprocess_result_importable_from_execution_process(self):
        """SubprocessResult is importable from autoskillit.execution.process."""
        from autoskillit.execution.process import SubprocessResult

        assert hasattr(SubprocessResult, "__dataclass_fields__")

    def test_real_subprocess_runner_default_pty_mode_is_false(self):
        """DefaultSubprocessRunner must default pty_mode=False.

        pty_mode=True merges child stderr into PTY stdout, breaking all _run_subprocess
        callers that expect stderr to contain git/shell error messages. Claude CLI callers
        (run_headless_core in execution/headless.py, _llm_triage) already pass pty_mode=True
        explicitly. Note: run_managed_async itself already defaults pty_mode=False; only the
        DefaultSubprocessRunner wrapper overrides this with True — making it the sole target
        for this fix.
        """
        import inspect

        from autoskillit.execution.process import DefaultSubprocessRunner

        sig = inspect.signature(DefaultSubprocessRunner.__call__)
        default = sig.parameters["pty_mode"].default
        assert default is False, (
            f"pty_mode default must be False to prevent silent stderr loss in git commands. "
            f"Current default: {default!r}. Only callers that need PTY (Claude CLI) "
            f"should pass pty_mode=True explicitly."
        )


class TestTracingStopOnException:
    """Verify tracing_handle.stop() is called on BaseException in run_managed_async."""

    @pytest.mark.anyio
    @pytest.mark.skipif(sys.platform != "linux", reason="Linux-only tracing")
    async def test_tracing_stop_called_on_task_group_exception(self, monkeypatch, tmp_path):
        """tracing_handle.stop() is called in except BaseException even when task group raises."""
        import subprocess

        from autoskillit.execution.linux_tracing import LinuxTracingHandle
        from tests._helpers import make_tracing_config

        stop_called: list[bool] = []
        original_stop = LinuxTracingHandle.stop

        def patched_stop(self_handle: LinuxTracingHandle) -> list:
            stop_called.append(True)
            return original_stop(self_handle)

        monkeypatch.setattr(LinuxTracingHandle, "stop", patched_stop)

        # Use a real process with tracing enabled; cancel mid-run to trigger BaseException path
        proc = subprocess.Popen(["sleep", "2"])
        cfg = make_tracing_config(enabled=True, proc_interval=0.05, tmpfs_path=str(tmp_path))

        import anyio

        try:
            with anyio.move_on_after(0.2):
                await run_managed_async(
                    cmd=["sleep", "2"],
                    cwd=tmp_path,
                    timeout=10.0,
                    linux_tracing_config=cfg,
                )
        except Exception:
            pass
        finally:
            proc.kill()
            proc.wait()

        # stop() should have been called (via happy path or exception path)
        assert len(stop_called) >= 1


class TestOuterCancelRaceGuard:
    """timeout_scope None-guard prevents AttributeError when outer cancel fires
    before move_on_after() inside the task group can bind."""

    @pytest.mark.anyio
    async def test_run_managed_async_outer_cancel_no_attribute_error(self, tmp_path):
        """Outer move_on_after(0) fires before the task group body's scope can bind.

        Before the fix, timeout_scope was None and timeout_scope.cancelled_caught
        raised AttributeError. After the fix it must exit cleanly with a
        CancelledError (or just return if the outer scope swallows the cancel).
        """
        import anyio

        caught_exc: BaseException | None = None
        with anyio.move_on_after(0.001):
            try:
                await run_managed_async(
                    cmd=["sleep", "10"],
                    cwd=tmp_path,
                    timeout=30.0,
                )
            except BaseException as exc:
                caught_exc = exc

        # The outer scope fires before run_managed_async completes. Two valid outcomes:
        # 1. caught_exc is None: the outer scope swallowed the cancel (no exception escaped)
        # 2. caught_exc is not None: a CancelledError propagated, but never AttributeError
        # Either outcome proves the timeout_scope None-guard is in place.
        assert caught_exc is None or not isinstance(caught_exc, AttributeError), (
            f"timeout_scope None dereference — got AttributeError: {caught_exc}"
        )


class TestIdleStallWatchdog:
    """Integration test: idle_output_timeout kills a hanging process."""

    @pytest.mark.anyio
    async def test_run_managed_async_idle_stall_kills_hanging_process(self, tmp_path, monkeypatch):
        """Process writes burst then stalls — IDLE_STALL kills it promptly."""
        script = tmp_path / "burst_stall.py"
        script.write_text(
            textwrap.dedent("""\
                import sys, time, json
                for i in range(3):
                    sys.stdout.write(json.dumps({"type": "assistant", "i": i}) + "\\n")
                    sys.stdout.flush()
                time.sleep(9999)
            """)
        )

        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_api_connection",
            lambda pid: True,
        )

        start = time.monotonic()
        with anyio.fail_after(15.0):
            result = await run_managed_async(
                [sys.executable, str(script)],
                cwd=tmp_path,
                timeout=30,
                idle_output_timeout=2.0,
                stale_threshold=60,
            )

        elapsed = time.monotonic() - start
        assert result.termination == TerminationReason.IDLE_STALL
        assert elapsed < 12.0
