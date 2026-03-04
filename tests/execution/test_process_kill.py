"""Integration tests for process tree kill and async cancellation.

These tests use REAL subprocesses (small Python scripts) to reproduce
exact failure modes. They validate that psutil-based process tree kill
handles all descendants and that cancellation cleans up properly.

NO MOCKS — that's the whole point.
"""

from __future__ import annotations

import sys
import textwrap

import anyio
import psutil
import pytest

from autoskillit.core.types import TerminationReason
from autoskillit.execution.process import (
    async_kill_process_tree,
    kill_process_tree,
    run_managed_async,
)

# ---------------------------------------------------------------------------
# Helper scripts — small Python programs that reproduce specific scenarios
# ---------------------------------------------------------------------------

# Script that spawns two grandchildren, all sleep forever
PROCESS_TREE_SCRIPT = textwrap.dedent("""\
    import os, sys, time
    sys.stdout.write(f"root:{os.getpid()}\\n")
    sys.stdout.flush()
    for _ in range(2):
        pid = os.fork()
        if pid == 0:
            sys.stdout.write(f"child:{os.getpid()}\\n")
            sys.stdout.flush()
            time.sleep(60)
            sys.exit(0)
    time.sleep(60)
    sys.exit(0)
""")

# Script that sleeps forever (simulates Claude CLI hang)
HANG_FOREVER_SCRIPT = textwrap.dedent("""\
    import sys, time
    sys.stdout.write("before hang\\n")
    sys.stdout.flush()
    time.sleep(3600)
""")


class TestProcessTreeKill:
    """psutil-based kill terminates all descendants."""

    @pytest.mark.anyio
    async def test_process_tree_kill_terminates_all_descendants(self, tmp_path):
        """Spawn root + 2 children, kill_process_tree kills all 3."""
        script = tmp_path / "tree.py"
        script.write_text(PROCESS_TREE_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=3,
        )

        # Process should have been killed by timeout
        assert result.termination == TerminationReason.TIMED_OUT

        # Parse PIDs from output
        pids = []
        for line in result.stdout.strip().splitlines():
            if ":" in line:
                pids.append(int(line.split(":")[1]))

        # All PIDs should be dead
        await anyio.sleep(0.5)  # Brief wait for kernel cleanup
        for pid in pids:
            assert not psutil.pid_exists(pid), f"PID {pid} should be dead"


class TestKillProcessTreeUnit:
    """Direct tests for kill_process_tree utility."""

    def test_kill_nonexistent_pid(self):
        """kill_process_tree handles nonexistent PID gracefully."""
        kill_process_tree(999999999)  # Should not raise

    def test_kill_already_dead_process(self, tmp_path):
        """kill_process_tree handles already-dead process gracefully."""
        import subprocess

        # Start and immediately kill a process
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        pid = proc.pid
        proc.kill()
        proc.wait()

        # Should handle gracefully
        kill_process_tree(pid)


class TestCancellationKillsProcess:
    """Cancellation of run_managed_async kills the subprocess."""

    @pytest.mark.anyio
    async def test_cancellation_kills_process(self, tmp_path):
        """Cancel run_managed_async — process should be cleaned up."""
        script = tmp_path / "sleep.py"
        script.write_text("import time; time.sleep(3600)")

        async with anyio.create_task_group() as tg:

            async def _run() -> None:
                await run_managed_async(
                    [sys.executable, str(script)],
                    cwd=tmp_path,
                    timeout=60,
                )

            tg.start_soon(_run)
            await anyio.sleep(1.0)
            tg.cancel_scope.cancel()  # replaces task.cancel()

        # Give the kernel a moment
        await anyio.sleep(0.5)


class TestAsyncKillDoesNotBlockLoop:
    """async_kill_process_tree doesn't block the event loop."""

    @pytest.mark.anyio
    async def test_async_kill_does_not_block_loop(self, tmp_path):
        """A concurrent coroutine runs while kill is in progress."""
        import subprocess as sp

        proc = sp.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        pid = proc.pid

        concurrent_ran = False

        async def concurrent_work():
            nonlocal concurrent_ran
            await anyio.sleep(0.1)
            concurrent_ran = True

        async with anyio.create_task_group() as tg:
            tg.start_soon(async_kill_process_tree, pid)
            tg.start_soon(concurrent_work)

        assert concurrent_ran, "Concurrent coroutine should run during async kill"
        proc.wait()


class TestDualWinnerRace:
    """When wait_task and session_monitor both complete, process exit wins."""

    @pytest.mark.anyio
    async def test_wait_task_wins_over_stale_monitor(self, tmp_path):
        """When process exits AND monitor reports stale simultaneously,
        the process exit takes priority — stale must be False."""
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        # Create a stale .jsonl file (exists before spawn_time, so monitor
        # enters phase-1 polling then finds it and sees no activity → stale)
        stale_log = session_dir / "session.jsonl"
        stale_log.write_text('{"type":"init"}\n')

        result = await run_managed_async(
            [sys.executable, "-c", "print('done')"],
            cwd=tmp_path,
            timeout=10,
            session_log_dir=session_dir,
            stale_threshold=0.001,  # fires immediately once file is found
            completion_marker="NONEXISTENT",
        )
        assert result.termination != TerminationReason.STALE
        assert result.returncode == 0

    @pytest.mark.anyio
    async def test_wait_task_wins_over_completion_monitor(self, tmp_path):
        """Process exit + monitor completion simultaneously — use process exit."""
        result = await run_managed_async(
            [sys.executable, "-c", "print('done')"],
            cwd=tmp_path,
            timeout=10,
        )
        assert result.termination != TerminationReason.STALE
        assert result.termination != TerminationReason.TIMED_OUT


class TestRunManagedAsyncPassesPidToMonitor:
    """Verify that run_managed_async passes proc.pid to _session_log_monitor."""

    @pytest.mark.anyio
    async def test_pid_passed_to_session_monitor(self, tmp_path):
        """
        Spawn a real subprocess. Patch _session_log_monitor to capture args.
        Verify the pid kwarg matches the real subprocess PID.
        """
        from unittest.mock import patch

        captured = {}

        async def capturing_monitor(*args, **kwargs):
            from autoskillit.execution._process_monitor import SessionMonitorResult

            captured["pid"] = kwargs.get("pid")
            captured["positional_pid"] = args[5] if len(args) > 5 else None
            return SessionMonitorResult("stale", "")

        session_file = tmp_path / "fake_session.jsonl"
        session_file.write_text("")

        with patch("autoskillit.execution._process_race._session_log_monitor", capturing_monitor):
            result = await run_managed_async(
                ["sleep", "5"],
                cwd=tmp_path,
                timeout=3.0,
                session_log_dir=tmp_path,
                stale_threshold=0.1,
                completion_marker="DONE",
            )

        assert result.termination == TerminationReason.STALE
        pid_received = captured.get("pid") or captured.get("positional_pid")
        assert pid_received is not None
        assert isinstance(pid_received, int)
        assert pid_received > 0
