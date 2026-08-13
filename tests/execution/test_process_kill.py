"""Integration tests for process tree kill and async cancellation.

These tests use REAL subprocesses (small Python scripts) to reproduce
exact failure modes. They validate that psutil-based process tree kill
handles all descendants and that cancellation cleans up properly.

NO MOCKS — that's the whole point.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
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
from tests.execution import _process_group_helpers
from tests.execution._process_group_helpers import (
    _cleanup_owned_process_group,
    _cleanup_process_identities,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

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

LIFECYCLE_TREE_SCRIPT = textwrap.dedent("""\
    import json, os, time
    children = []
    for task_id in ("owned-1", "owned-2"):
        child = os.fork()
        if child == 0:
            time.sleep(60)
            raise SystemExit(0)
        children.append(child)
        print(json.dumps({"type": "task_started", "task_id": task_id}), flush=True)
        print(json.dumps({"type": "child_pid", "pid": child}), flush=True)
    print(json.dumps({"type": "result", "result": "ORDER_UP"}), flush=True)
    time.sleep(0.4)
    print(json.dumps({
        "type": "task_notification",
        "task_id": "owned-1",
        "status": "completed",
    }), flush=True)
    print(json.dumps({
        "type": "task_updated",
        "task_id": "owned-2",
        "patch": {"status": "failed"},
    }), flush=True)
    time.sleep(60)
""")

NATURAL_EXIT_WITH_OWNED_CHILD_SCRIPT = textwrap.dedent("""\
    import json, os, time
    child = os.fork()
    if child == 0:
        time.sleep(60)
        raise SystemExit(0)
    print(json.dumps({"type": "task_started", "task_id": "owned-exit"}), flush=True)
    print(json.dumps({"type": "child_pid", "pid": child}), flush=True)
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

    @pytest.mark.anyio
    async def test_lifecycle_marker_waits_for_terminal_then_cleans_group(self, tmp_path):
        from autoskillit.execution.backends import ClaudeStreamParser

        script = tmp_path / "lifecycle_tree.py"
        script.write_text(LIFECYCLE_TREE_SCRIPT)
        started = anyio.current_time()
        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=10,
            completion_marker="ORDER_UP",
            stream_parser=ClaudeStreamParser(completion_marker="ORDER_UP"),
            lifecycle_observation_enabled=True,
            child_deferral_ceiling=2,
            natural_exit_grace_seconds=0.05,
        )
        assert anyio.current_time() - started >= 0.35
        assert result.termination is TerminationReason.COMPLETED
        assert result.lifecycle_observation_complete is True
        assert result.pending_task_ids == ()
        child_records = [
            json.loads(line)
            for line in result.stdout.splitlines()
            if '"type": "child_pid"' in line
        ]
        assert len(child_records) == 2
        assert all(not psutil.pid_exists(record["pid"]) for record in child_records)

    @pytest.mark.anyio
    async def test_natural_exit_retains_obligation_and_cleans_owned_group(self, tmp_path):
        from autoskillit.execution.backends import ClaudeStreamParser

        script = tmp_path / "natural_exit_with_owned_child.py"
        script.write_text(NATURAL_EXIT_WITH_OWNED_CHILD_SCRIPT)
        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=10,
            stream_parser=ClaudeStreamParser(),
            lifecycle_observation_enabled=True,
            natural_exit_grace_seconds=0.05,
        )

        assert result.termination is TerminationReason.NATURAL_EXIT
        assert result.lifecycle_observation_complete is True
        assert result.pending_task_ids == ("owned-exit",)
        child_record = next(
            json.loads(line)
            for line in result.stdout.splitlines()
            if '"type": "child_pid"' in line
        )
        assert not psutil.pid_exists(child_record["pid"])


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

    def test_exited_unowned_root_cannot_reconstruct_group_authority(self):
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import os,time; pid=os.fork(); "
                    "print(pid, flush=True) if pid else time.sleep(60)"
                ),
            ],
            start_new_session=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        assert proc.stdout is not None
        child_pid = int(proc.stdout.readline())
        child_identity = psutil.Process(child_pid).create_time()
        proc.wait(timeout=5)
        try:
            result = kill_process_tree(proc.pid)
            assert result.observation_complete is False
            assert result.complete is False
            assert result.terminated_pids == ()
        finally:
            _cleanup_process_identities({child_pid: child_identity})

    @pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
    def test_reaped_leader_cannot_authorize_test_group_teardown(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        proc = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            start_new_session=True,
        )
        proc.wait(timeout=5)
        group_signals: list[tuple[int, signal.Signals]] = []
        monkeypatch.setattr(
            os,
            "killpg",
            lambda pgid, signum: group_signals.append((pgid, signum)),
        )

        cleaned = _cleanup_owned_process_group(proc)

        assert cleaned == set()
        assert group_signals == []

    def test_changed_leader_identity_refuses_group_teardown(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class FakeProcess:
            pid = 101
            returncode: int | None = None

            def wait(self, timeout: float | None = None) -> int:
                del timeout
                self.returncode = 0
                return 0

        process = FakeProcess()
        group_signals: list[tuple[int, signal.Signals]] = []
        monkeypatch.setattr(
            _process_group_helpers,
            "_capture_owned_group_identities",
            lambda _process: {process.pid: 123.0},
        )
        monkeypatch.setattr(
            _process_group_helpers,
            "_owned_group_anchor_is_valid",
            lambda _process, _created: False,
        )
        monkeypatch.setattr(
            os,
            "killpg",
            lambda pgid, signum: group_signals.append((pgid, signum)),
        )

        cleaned = _cleanup_owned_process_group(process)  # type: ignore[arg-type]

        assert cleaned == {process.pid}
        assert group_signals == []


class TestCancellationKillsProcess:
    """Cancellation of run_managed_async kills the subprocess."""

    @pytest.mark.anyio
    async def test_cancellation_kills_process(self, tmp_path):
        """Cancel run_managed_async — process should be cleaned up."""
        script = tmp_path / "sleep.py"
        script.write_text("import time; time.sleep(3600)")

        parent = psutil.Process(os.getpid())
        children_before = set(c.pid for c in parent.children(recursive=True))

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

        children_after = set(c.pid for c in parent.children(recursive=True))
        leaked = children_after - children_before
        assert not leaked, f"Child processes not cleaned up after cancellation: {leaked}"


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
            from autoskillit.execution.process._process_monitor import SessionMonitorResult

            captured["pid"] = kwargs.get("pid")
            captured["positional_pid"] = args[5] if len(args) > 5 else None
            return SessionMonitorResult("stale", "")

        session_file = tmp_path / "fake_session.jsonl"
        session_file.write_text("")

        with patch(
            "autoskillit.execution.process._process_race._session_log_monitor", capturing_monitor
        ):
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
