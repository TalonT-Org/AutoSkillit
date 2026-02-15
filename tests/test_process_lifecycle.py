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

from automation_mcp.process_lifecycle import (
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
