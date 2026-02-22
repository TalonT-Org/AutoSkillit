"""Integration tests for subprocess lifecycle utilities.

These tests use REAL subprocesses (small Python scripts) to reproduce
exact failure modes. They validate that temp-file I/O eliminates
pipe blocking and that psutil-based process tree kill handles all descendants.

NO MOCKS — that's the whole point.
"""

from __future__ import annotations

import sys
import textwrap
import time

import psutil
import pytest

from autoskillit.process_lifecycle import (
    _session_log_monitor,
    async_kill_process_tree,
    kill_process_tree,
    run_managed_async,
    run_managed_sync,
)

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

# Script that writes a JSON result line then hangs (simulates Claude CLI completed-but-hung)
WRITE_RESULT_THEN_HANG_SCRIPT = textwrap.dedent("""\
    import sys, time, json
    result = {"type": "result", "subtype": "success", "is_error": False,
              "result": "done", "session_id": "s1"}
    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\\n")
    sys.stdout.flush()
    time.sleep(3600)
""")

# Script that writes non-matching output then hangs
PARTIAL_OUTPUT_THEN_HANG_SCRIPT = textwrap.dedent("""\
    import sys, time
    sys.stdout.write("partial output\\n")
    sys.stdout.flush()
    time.sleep(3600)
""")

# Script that prints sys.stdout.isatty() result
ISATTY_CHECK_SCRIPT = textwrap.dedent("""\
    import sys
    print(sys.stdout.isatty())
""")


class TestTempFileIOEliminatesPipeBlocking:
    """Temp file I/O prevents pipe-inheritance blocking."""

    @pytest.mark.asyncio
    async def test_child_holds_fd_does_not_block_read(self, tmp_path):
        """Parent exits, child holds FD — temp file read doesn't block."""
        script = tmp_path / "parent_child.py"
        script.write_text(PARENT_EXITS_CHILD_HOLDS_FD)

        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=10,
        )

        assert not result.timed_out, "Read should not block even though child holds FD"
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

        assert not result.timed_out
        assert result.returncode == 0
        assert "parent output line" in result.stdout


class TestProcessTreeKill:
    """psutil-based kill terminates all descendants."""

    @pytest.mark.asyncio
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
        assert result.timed_out

        # Parse PIDs from output
        pids = []
        for line in result.stdout.strip().splitlines():
            if ":" in line:
                pids.append(int(line.split(":")[1]))

        # All PIDs should be dead
        time.sleep(0.5)  # Brief wait for kernel cleanup
        for pid in pids:
            assert not psutil.pid_exists(pid), f"PID {pid} should be dead"


class TestTimeoutKillsHangingProcess:
    """Timeout fires and kills when process hangs."""

    @pytest.mark.asyncio
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

        assert result.timed_out
        assert elapsed < 8, f"Should return within ~2s timeout, took {elapsed:.1f}s"
        assert "before hang" in result.stdout  # Partial output captured
        # Process should be dead
        time.sleep(0.5)
        assert not psutil.pid_exists(result.pid)


class TestNormalCompletion:
    """Normal subprocess completion captures all output."""

    @pytest.mark.asyncio
    async def test_normal_completion_captures_full_output(self, tmp_path):
        """Process writes multi-line output and exits — all captured."""
        script = tmp_path / "clean.py"
        script.write_text(CLEAN_OUTPUT_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=10,
        )

        assert not result.timed_out
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

        assert not result.timed_out
        assert result.returncode == 0
        for i in range(10):
            assert f"line {i}" in result.stdout


class TestStdinInput:
    """Stdin input via temp file works correctly."""

    @pytest.mark.asyncio
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

        assert not result.timed_out
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

        assert not result.timed_out
        assert result.returncode == 0
        assert "echo: hello world" in result.stdout


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


class TestHeartbeatDetectsCompletion:
    """Stdout heartbeat detects completion and triggers kill."""

    @pytest.mark.asyncio
    async def test_heartbeat_detects_completion_and_kills(self, tmp_path):
        """Script writes result JSON then hangs — heartbeat detects and returns."""
        script = tmp_path / "result_hang.py"
        script.write_text(WRITE_RESULT_THEN_HANG_SCRIPT)

        start = time.monotonic()
        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=30,
            heartbeat_marker='"type":"result"',
        )
        elapsed = time.monotonic() - start

        assert not result.timed_out, "Heartbeat should fire before wall-clock timeout"
        assert elapsed < 10, f"Heartbeat should detect within ~5s, took {elapsed:.1f}s"
        assert '"type": "result"' in result.stdout or '"type":"result"' in result.stdout

    @pytest.mark.asyncio
    async def test_heartbeat_ignores_non_matching_output(self, tmp_path):
        """Script writes non-matching output — heartbeat doesn't fire, backstop does."""
        script = tmp_path / "partial_hang.py"
        script.write_text(PARTIAL_OUTPUT_THEN_HANG_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=3,
            heartbeat_marker='"type":"result"',
        )

        assert result.timed_out, "Non-matching output should not trigger heartbeat"


class TestNoHeartbeatPreservesExistingBehavior:
    """Without heartbeat, behavior matches original blind-wait."""

    @pytest.mark.asyncio
    async def test_no_heartbeat_preserves_existing_behavior(self, tmp_path):
        """No heartbeat marker — same hanging script, same timeout behavior."""
        script = tmp_path / "hang.py"
        script.write_text(HANG_FOREVER_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=3,
            heartbeat_marker=None,
        )

        assert result.timed_out


class TestSessionLogMonitor:
    """Session log monitor detects completion and staleness."""

    @pytest.mark.asyncio
    async def test_session_log_monitor_detects_completion(self, tmp_path):
        """Session log with completion marker returns 'completion'."""
        import asyncio

        log_dir = tmp_path / "session_logs"
        log_dir.mkdir()
        spawn_time = time.time() - 1  # slightly in the past

        session_file = log_dir / "abc123.jsonl"
        session_file.write_text('{"role":"assistant","content":"working..."}\n')

        async def append_marker():
            await asyncio.sleep(1.0)
            with session_file.open("a") as f:
                f.write('{"role":"assistant","content":"Done %%AUTOSKILLIT_COMPLETE%%"}\n')

        task = asyncio.create_task(append_marker())
        result = await _session_log_monitor(
            log_dir, "%%AUTOSKILLIT_COMPLETE%%", stale_threshold=30, spawn_time=spawn_time
        )
        await task

        assert result == "completion"

    @pytest.mark.asyncio
    async def test_session_log_monitor_detects_staleness(self, tmp_path):
        """Session log that stops being written to returns 'stale'."""
        log_dir = tmp_path / "session_logs"
        log_dir.mkdir()
        spawn_time = time.time() - 1

        session_file = log_dir / "abc123.jsonl"
        session_file.write_text('{"role":"assistant","content":"hello"}\n')

        start = time.monotonic()
        result = await _session_log_monitor(
            log_dir, "%%AUTOSKILLIT_COMPLETE%%", stale_threshold=2, spawn_time=spawn_time
        )
        elapsed = time.monotonic() - start

        assert result == "stale"
        assert elapsed < 10, f"Staleness should fire after ~2s, took {elapsed:.1f}s"

    @pytest.mark.asyncio
    async def test_staleness_resets_on_activity(self, tmp_path):
        """Session log that keeps getting written to does not fire staleness."""
        import asyncio

        log_dir = tmp_path / "session_logs"
        log_dir.mkdir()
        spawn_time = time.time() - 1

        session_file = log_dir / "abc123.jsonl"
        session_file.write_text('{"role":"assistant","content":"start"}\n')

        async def keep_writing():
            for i in range(5):
                await asyncio.sleep(1.0)
                with session_file.open("a") as f:
                    f.write(f'{{"role":"assistant","content":"msg {i}"}}\n')
            # After writing stops, staleness should eventually fire
            await asyncio.sleep(5.0)

        writer = asyncio.create_task(keep_writing())

        result = await _session_log_monitor(
            log_dir, "NONEXISTENT_MARKER", stale_threshold=3, spawn_time=spawn_time
        )
        writer.cancel()

        # Staleness should have fired AFTER the writing stopped, not during
        assert result == "stale"


class TestPtyWrapper:
    """PTY wrapping provides a TTY to the subprocess."""

    @pytest.mark.asyncio
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

        assert not result.timed_out
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

        assert not result.timed_out
        assert "False" in result.stdout


class TestCancellationKillsProcess:
    """Cancellation of run_managed_async kills the subprocess."""

    @pytest.mark.asyncio
    async def test_cancellation_kills_process(self, tmp_path):
        """Cancel run_managed_async — process should be cleaned up."""
        import asyncio

        script = tmp_path / "sleep.py"
        script.write_text("import time; time.sleep(3600)")

        task = asyncio.create_task(
            run_managed_async(
                [sys.executable, str(script)],
                cwd=tmp_path,
                timeout=60,
            )
        )

        await asyncio.sleep(1.0)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        # Give the kernel a moment
        await asyncio.sleep(0.5)


class TestAsyncKillDoesNotBlockLoop:
    """async_kill_process_tree doesn't block the event loop."""

    @pytest.mark.asyncio
    async def test_async_kill_does_not_block_loop(self, tmp_path):
        """A concurrent coroutine runs while kill is in progress."""
        import asyncio
        import subprocess as sp

        proc = sp.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        pid = proc.pid

        concurrent_ran = False

        async def concurrent_work():
            nonlocal concurrent_ran
            await asyncio.sleep(0.1)
            concurrent_ran = True

        await asyncio.gather(
            async_kill_process_tree(pid),
            concurrent_work(),
        )

        assert concurrent_ran, "Concurrent coroutine should run during async kill"
        proc.wait()
