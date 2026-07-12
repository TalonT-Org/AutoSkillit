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
from typing import Any

import anyio
import psutil
import pytest

from autoskillit.core.types import KillReason, TerminationReason
from autoskillit.execution.linux_tracing import read_starttime_ticks
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
        sys.stdout.write(f"child_pid:{pid}\\n")
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

SpawnedIdentity = tuple[int, int, float]


def _capture_identity(pid: int, starttime_ticks: int | None = None) -> SpawnedIdentity:
    ticks = starttime_ticks if starttime_ticks is not None else read_starttime_ticks(pid)
    return (pid, ticks or 0, psutil.Process(pid).create_time())


def _identity_is_alive(identity: SpawnedIdentity) -> bool:
    pid, starttime_ticks, fallback_create_time = identity
    if not psutil.pid_exists(pid):
        return False
    if starttime_ticks > 0:
        return read_starttime_ticks(pid) == starttime_ticks
    try:
        return psutil.Process(pid).create_time() == fallback_create_time
    except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
        return False


async def _cleanup_identity(identity: SpawnedIdentity) -> None:
    """Terminate only the exact captured process identity."""
    if not _identity_is_alive(identity):
        return
    try:
        process = psutil.Process(identity[0])
        if identity[1] > 0:
            if read_starttime_ticks(identity[0]) != identity[1]:
                return
        elif process.create_time() != identity[2]:
            return
        process.terminate()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return
    try:
        await anyio.to_thread.run_sync(lambda: process.wait(timeout=2))
    except psutil.TimeoutExpired:
        if _identity_is_alive(identity):
            try:
                process.kill()
                await anyio.to_thread.run_sync(lambda: process.wait(timeout=2))
            except psutil.NoSuchProcess:
                pass


class _ParserConstructionFailure(RuntimeError):
    pass


class _ResultConstructionFailure(RuntimeError):
    pass


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
        assert result.cleanup_outcome is not None
        assert result.cleanup_outcome.succeeded
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
        assert result.cleanup_outcome is not None
        assert result.cleanup_outcome.succeeded
        for i in range(10):
            assert f"line {i}" in result.stdout

    def test_sync_root_exit_with_live_descendant_is_not_natural(self, tmp_path) -> None:
        script = tmp_path / "root_exit.py"
        script.write_text(PARENT_EXITS_CHILD_HOLDS_FD)

        result = run_managed_sync(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=10,
        )

        assert result.termination is TerminationReason.NATURAL_EXIT
        assert result.kill_reason is KillReason.INFRA_KILL
        assert result.cleanup_outcome is not None
        assert result.cleanup_outcome.succeeded


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

        import autoskillit.execution.process as proc_mod
        import autoskillit.execution.process._process_io as io_mod

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


class TestPostSpawnFailureFinalization:
    @pytest.mark.anyio
    async def test_parser_factory_failure_preserves_error_and_finalizes_once(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import autoskillit.execution.process as process_module

        captured: list[SpawnedIdentity] = []
        cleanup_executions: list[int] = []
        factory_calls = 0
        injected_error = _ParserConstructionFailure("parser construction failed")
        original_post_init = process_module._OwnedProcessFinalizer.__post_init__
        original_run_once = process_module._OwnedProcessFinalizer._run_once

        def capture_spawned_identity(finalizer: Any) -> None:
            original_post_init(finalizer)
            captured.append(_capture_identity(finalizer.owned_root_pid))

        async def observe_cleanup_execution(finalizer: Any) -> Any:
            cleanup_executions.append(finalizer.owned_root_pid)
            return await original_run_once(finalizer)

        def failing_parser_factory() -> Any:
            nonlocal factory_calls
            factory_calls += 1
            raise injected_error

        monkeypatch.setattr(
            process_module._OwnedProcessFinalizer,
            "__post_init__",
            capture_spawned_identity,
        )
        monkeypatch.setattr(
            process_module._OwnedProcessFinalizer,
            "_run_once",
            observe_cleanup_execution,
        )

        try:
            with pytest.raises(_ParserConstructionFailure) as exc_info:
                await run_managed_async(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    cwd=tmp_path,
                    timeout=5,
                    completion_marker="DONE",
                    stream_parser_factory=failing_parser_factory,
                    parent_candidate_normalizer=lambda _record, _offset: None,
                    cleanup_budget_seconds=3,
                )

            assert exc_info.value is injected_error
            assert factory_calls == 1
            assert len(captured) == 1
            assert captured[0][0] > 0
            assert captured[0][1] > 0 or captured[0][2] > 0
            assert cleanup_executions == [captured[0][0]]
            assert not _identity_is_alive(captured[0])
        finally:
            for identity in captured:
                await _cleanup_identity(identity)

    @pytest.mark.anyio
    async def test_result_construction_failure_preserves_error_and_finalizes_once(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import autoskillit.execution.process as process_module

        captured: list[SpawnedIdentity] = []
        cleanup_executions: list[int] = []
        result_constructions = 0
        injected_error = _ResultConstructionFailure("result construction failed")
        original_run_once = process_module._OwnedProcessFinalizer._run_once

        def capture_spawned_identity(pid: int, starttime_ticks: int) -> None:
            captured.append(_capture_identity(pid, starttime_ticks))

        async def observe_cleanup_execution(finalizer: Any) -> Any:
            cleanup_executions.append(finalizer.owned_root_pid)
            return await original_run_once(finalizer)

        def failing_result_construction(*_args: Any, **_kwargs: Any) -> Any:
            nonlocal result_constructions
            result_constructions += 1
            raise injected_error

        monkeypatch.setattr(
            process_module._OwnedProcessFinalizer,
            "_run_once",
            observe_cleanup_execution,
        )
        monkeypatch.setattr(
            process_module,
            "SubprocessResult",
            failing_result_construction,
        )

        try:
            with pytest.raises(_ResultConstructionFailure) as exc_info:
                await run_managed_async(
                    [sys.executable, "-c", "import time; time.sleep(0.2)"],
                    cwd=tmp_path,
                    timeout=5,
                    on_pid_resolved=capture_spawned_identity,
                    cleanup_budget_seconds=3,
                )

            assert exc_info.value is injected_error
            assert result_constructions == 1
            assert len(captured) == 1
            assert captured[0][0] > 0
            assert captured[0][1] > 0 or captured[0][2] > 0
            assert cleanup_executions == [captured[0][0]]
            assert not _identity_is_alive(captured[0])
        finally:
            for identity in captured:
                await _cleanup_identity(identity)


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

    @pytest.mark.anyio
    @pytest.mark.skipif(sys.platform != "linux", reason="Linux-only tracing")
    async def test_stop_failure_preserves_application_error_and_runs_finalizer(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import autoskillit.execution.process as process_module
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.execution.linux_tracing import LinuxTracingHandle
        from tests._helpers import make_tracing_config

        finalizer_called = anyio.Event()
        original_finalizer_run = process_module._OwnedProcessFinalizer.run

        async def observed_finalizer(finalizer) -> Any:
            finalizer_called.set()
            return await original_finalizer_run(finalizer)

        async def failing_pump(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("application-failed")

        def failing_stop(_handle: LinuxTracingHandle) -> list[Any]:
            raise RuntimeError("tracing-stop-failed")

        monkeypatch.setattr(process_module, "run_channel_a_pump", failing_pump)
        monkeypatch.setattr(process_module._OwnedProcessFinalizer, "run", observed_finalizer)
        monkeypatch.setattr(LinuxTracingHandle, "stop", failing_stop)
        backend = ClaudeCodeBackend()
        cfg = make_tracing_config(enabled=True, proc_interval=0.05, tmpfs_path=str(tmp_path))

        with pytest.raises(RuntimeError, match="application-failed"):
            await run_managed_async(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=tmp_path,
                timeout=10,
                completion_marker="DONE",
                stream_parser_factory=backend.stream_parser_factory("DONE"),
                parent_candidate_normalizer=backend.parent_candidate_normalizer("DONE"),
                linux_tracing_config=cfg,
            )

        assert finalizer_called.is_set()


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


class TestLifecycleProducerSupervisor:
    @pytest.mark.anyio
    async def test_producer_exception_is_raised_only_after_actor_done(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import autoskillit.execution.process as process_module
        from autoskillit.execution.backends.claude import ClaudeCodeBackend

        actor_finished = anyio.Event()
        original_actor = process_module.run_lifecycle_actor

        async def observed_actor(*args: Any, **kwargs: Any) -> None:
            try:
                await original_actor(*args, **kwargs)
            finally:
                actor_finished.set()

        async def failing_pump(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("producer-failed")

        monkeypatch.setattr(process_module, "run_lifecycle_actor", observed_actor)
        monkeypatch.setattr(process_module, "run_channel_a_pump", failing_pump)
        backend = ClaudeCodeBackend()

        with pytest.raises(RuntimeError, match="producer-failed"):
            await run_managed_async(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=tmp_path,
                timeout=10,
                completion_marker="DONE",
                completion_drain_timeout=0.05,
                stream_parser_factory=backend.stream_parser_factory("DONE"),
                parent_candidate_normalizer=backend.parent_candidate_normalizer("DONE"),
            )

        assert actor_finished.is_set()

    @pytest.mark.anyio
    async def test_stuck_actor_uses_bounded_emergency_scope(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import autoskillit.execution.process as process_module
        from autoskillit.execution.backends.claude import ClaudeCodeBackend

        async def stuck_actor(*_args: Any, **_kwargs: Any) -> None:
            await anyio.sleep_forever()

        monkeypatch.setattr(process_module, "run_lifecycle_actor", stuck_actor)
        backend = ClaudeCodeBackend()
        started = time.monotonic()

        with pytest.raises(RuntimeError, match="lifecycle_actor_drain_incomplete"):
            await run_managed_async(
                [sys.executable, "-c", "print('done')"],
                cwd=tmp_path,
                timeout=0.05,
                completion_marker="DONE",
                completion_drain_timeout=0.05,
                stream_parser_factory=backend.stream_parser_factory("DONE"),
                parent_candidate_normalizer=backend.parent_candidate_normalizer("DONE"),
            )

        assert time.monotonic() - started < 2.0
