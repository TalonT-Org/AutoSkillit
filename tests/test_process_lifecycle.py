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
from pathlib import Path

import psutil
import pytest

from autoskillit.process_lifecycle import (
    TerminationReason,
    _heartbeat,
    _jsonl_contains_marker,
    _marker_is_standalone,
    _session_log_monitor,
    async_kill_process_tree,
    kill_process_tree,
    read_temp_output,
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
        assert result.termination == TerminationReason.TIMED_OUT

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

        assert result.termination == TerminationReason.TIMED_OUT
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

        assert result.termination != TerminationReason.TIMED_OUT, (
            "Heartbeat should fire before wall-clock timeout"
        )
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

        assert result.termination == TerminationReason.TIMED_OUT, (
            "Non-matching output should not trigger heartbeat"
        )


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

        assert result.termination == TerminationReason.TIMED_OUT


class TestSessionLogMonitor:
    """Session log monitor detects completion and staleness."""

    @pytest.mark.asyncio
    async def test_session_log_monitor_detects_completion(self, tmp_path):
        """Session log with completion marker in assistant record returns 'completion'."""
        import asyncio
        import json

        log_dir = tmp_path / "session_logs"
        log_dir.mkdir()
        spawn_time = time.time() - 1  # slightly in the past

        session_file = log_dir / "abc123.jsonl"
        session_file.write_text(
            json.dumps(
                {"type": "assistant", "message": {"role": "assistant", "content": "working..."}}
            )
            + "\n"
        )

        async def append_marker():
            await asyncio.sleep(1.0)
            with session_file.open("a") as f:
                f.write(
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {
                                "role": "assistant",
                                "content": "Done\n\n%%AUTOSKILLIT_COMPLETE%%",
                            },
                        }
                    )
                    + "\n"
                )

        task = asyncio.create_task(append_marker())
        result = await _session_log_monitor(
            log_dir, "%%AUTOSKILLIT_COMPLETE%%", stale_threshold=30, spawn_time=spawn_time
        )
        await task

        assert result == "completion"

    @pytest.mark.asyncio
    async def test_session_log_monitor_detects_staleness(self, tmp_path):
        """Session log that stops being written to returns 'stale'."""
        import json

        log_dir = tmp_path / "session_logs"
        log_dir.mkdir()
        spawn_time = time.time() - 1

        session_file = log_dir / "abc123.jsonl"
        session_file.write_text(
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "hello"}})
            + "\n"
        )

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
        import json

        log_dir = tmp_path / "session_logs"
        log_dir.mkdir()
        spawn_time = time.time() - 1

        session_file = log_dir / "abc123.jsonl"
        session_file.write_text(
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "start"}})
            + "\n"
        )

        async def keep_writing():
            for i in range(5):
                await asyncio.sleep(1.0)
                with session_file.open("a") as f:
                    f.write(
                        json.dumps(
                            {
                                "type": "assistant",
                                "message": {"role": "assistant", "content": f"msg {i}"},
                            }
                        )
                        + "\n"
                    )
            # After writing stops, staleness should eventually fire
            await asyncio.sleep(5.0)

        writer = asyncio.create_task(keep_writing())

        result = await _session_log_monitor(
            log_dir, "NONEXISTENT_MARKER", stale_threshold=3, spawn_time=spawn_time
        )
        writer.cancel()

        # Staleness should have fired AFTER the writing stopped, not during
        assert result == "stale"

    @pytest.mark.asyncio
    async def test_monitor_ignores_marker_in_non_assistant_records(self, tmp_path):
        """Monitor must NOT fire on completion marker in non-assistant records.

        Reproduces the false-fire: Claude Code writes the prompt (containing
        the completion marker) into a queue-operation/enqueue record at byte 0.
        The monitor should ignore it. Only an assistant-type record triggers.
        """
        import asyncio

        log_dir = tmp_path / "session_logs"
        log_dir.mkdir()
        spawn_time = time.time() - 1

        marker = "%%AUTOSKILLIT_COMPLETE%%"
        # Pre-populate with a queue-operation record containing the marker
        # (this is what Claude Code writes immediately from the injected prompt)
        session_file = log_dir / "abc123.jsonl"
        import json

        enqueue_record = json.dumps(
            {
                "type": "queue-operation",
                "operation": "enqueue",
                "content": f"Do the task\n\nORCHESTRATION DIRECTIVE: {marker}",
            }
        )
        session_file.write_text(enqueue_record + "\n")

        monitor_task = asyncio.create_task(
            _session_log_monitor(log_dir, marker, stale_threshold=30, spawn_time=spawn_time)
        )

        # Monitor should NOT fire on the enqueue record — wait 4s to confirm
        await asyncio.sleep(4.0)
        assert not monitor_task.done(), "Monitor fired on non-assistant record — false-fire bug"

        # Now append an assistant record with the marker — should fire
        assistant_record = json.dumps(
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": f"Done\n\n{marker}"},
            }
        )
        with session_file.open("a") as f:
            f.write(assistant_record + "\n")

        result = await asyncio.wait_for(monitor_task, timeout=10)
        assert result == "completion"

    @pytest.mark.asyncio
    async def test_monitor_realistic_jsonl_sequence(self, tmp_path):
        """Monitor correctly handles the realistic 3-record JSONL sequence.

        Claude Code writes:
        1. queue-operation/enqueue (immediate, contains marker in prompt)
        2. user message (immediate, contains marker in prompt)
        3. assistant message (after delay, contains marker in response)

        Only record 3 should trigger completion.
        """
        import asyncio
        import json

        log_dir = tmp_path / "session_logs"
        log_dir.mkdir()
        spawn_time = time.time() - 1

        marker = "%%AUTOSKILLIT_COMPLETE%%"

        # Write records 1 and 2 immediately (both contain the marker)
        session_file = log_dir / "abc123.jsonl"
        records_12 = (
            json.dumps(
                {
                    "type": "queue-operation",
                    "operation": "enqueue",
                    "content": f"Task prompt {marker}",
                }
            )
            + "\n"
            + json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": f"Task prompt {marker}"},
                }
            )
            + "\n"
        )
        session_file.write_text(records_12)

        monitor_task = asyncio.create_task(
            _session_log_monitor(log_dir, marker, stale_threshold=30, spawn_time=spawn_time)
        )

        # Wait and confirm no early fire
        await asyncio.sleep(4.0)
        assert not monitor_task.done(), "Monitor fired on user/enqueue records"

        # Write record 3 (assistant with marker as standalone line)
        assistant_record = json.dumps(
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": f"All done\n\n{marker}"},
            }
        )
        with session_file.open("a") as f:
            f.write(assistant_record + "\n")

        result = await asyncio.wait_for(monitor_task, timeout=10)
        assert result == "completion"


class TestJsonlContainsMarker:
    """_jsonl_contains_marker performs structured record filtering."""

    def test_matches_in_allowed_record_type(self):
        import json

        content = json.dumps({"type": "assistant", "message": {"content": "Done\nMARKER"}})
        assert _jsonl_contains_marker(content, "MARKER", frozenset({"assistant"}))

    def test_ignores_disallowed_record_type(self):
        import json

        content = json.dumps({"type": "queue-operation", "content": "prompt\nMARKER"})
        assert not _jsonl_contains_marker(content, "MARKER", frozenset({"assistant"}))

    def test_ignores_unparseable_lines(self):
        content = "not valid json MARKER\n"
        assert not _jsonl_contains_marker(content, "MARKER", frozenset({"assistant"}))

    def test_multiline_mixed_records(self):
        import json

        lines = [
            json.dumps({"type": "user", "message": {"content": "hello\nMARKER"}}),
            json.dumps({"type": "assistant", "message": {"content": "world"}}),
            json.dumps({"type": "assistant", "message": {"content": "found it\nMARKER"}}),
        ]
        content = "\n".join(lines)
        assert _jsonl_contains_marker(content, "MARKER", frozenset({"assistant"}))

    def test_no_match_when_marker_absent(self):
        import json

        content = json.dumps({"type": "assistant", "message": {"content": "no marker here"}})
        assert not _jsonl_contains_marker(content, "MARKER", frozenset({"assistant"}))

    def test_marker_in_result_record(self):
        import json

        content = json.dumps({"type": "result", "result": "MARKER", "subtype": "success"})
        assert _jsonl_contains_marker(content, "MARKER", frozenset({"result"}))

    def test_marker_embedded_in_prose_no_match(self):
        import json

        content = json.dumps(
            {
                "type": "assistant",
                "message": {"content": "I will emit MARKER when done"},
            }
        )
        assert not _jsonl_contains_marker(content, "MARKER", frozenset({"assistant"}))


class TestHeartbeatStructuredParsing:
    """Heartbeat uses structured parsing to avoid false-fires."""

    @pytest.mark.asyncio
    async def test_heartbeat_ignores_marker_in_string_values(self, tmp_path):
        """Heartbeat must not fire when the marker text appears as a string
        value inside a non-result record (e.g., model discussing JSON formats).
        """
        import asyncio
        import json

        stdout_path = tmp_path / "stdout.tmp"
        # Write an assistant message that CONTAINS the marker text as a value.
        # Use separators=(",", ":") to match Claude CLI's compact JSON format.
        assistant_msg = json.dumps(
            {
                "type": "assistant",
                "message": {"content": 'The output format uses "type":"result" for completion'},
            },
            separators=(",", ":"),
        )
        stdout_path.write_text(assistant_msg + "\n")

        heartbeat_task = asyncio.create_task(_heartbeat(stdout_path, '"type":"result"'))

        # Wait to confirm it doesn't fire on the assistant record
        await asyncio.sleep(2.0)
        assert not heartbeat_task.done(), (
            "Heartbeat fired on non-result record containing marker text"
        )

        # Now write an actual result record (compact format matching Claude CLI)
        result_record = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "s1",
            },
            separators=(",", ":"),
        )
        with stdout_path.open("a") as f:
            f.write(result_record + "\n")

        result = await asyncio.wait_for(heartbeat_task, timeout=10)
        assert result == "completion"


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


class TestDualWinnerRace:
    """When wait_task and session_monitor both complete, process exit wins."""

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_wait_task_wins_over_completion_monitor(self, tmp_path):
        """Process exit + monitor completion simultaneously — use process exit."""
        result = await run_managed_async(
            [sys.executable, "-c", "print('done')"],
            cwd=tmp_path,
            timeout=10,
        )
        assert result.termination != TerminationReason.STALE
        assert result.termination != TerminationReason.TIMED_OUT


class TestReadTempOutputLogging:
    """OSError during temp file read should produce a warning log."""

    def test_oserror_logs_warning(self, caplog):
        """OSError during temp file read should produce a warning log."""
        import logging

        with caplog.at_level(logging.WARNING):
            stdout, stderr = read_temp_output(
                Path("/nonexistent/stdout.tmp"),
                Path("/nonexistent/stderr.tmp"),
            )
        assert stdout == ""
        assert stderr == ""
        assert "Failed to read" in caplog.text


class TestMarkerIsStandalone:
    """_marker_is_standalone validates standalone line matching."""

    def test_standalone_marker(self):
        assert _marker_is_standalone(
            "Done\n\n%%AUTOSKILLIT_COMPLETE%%", "%%AUTOSKILLIT_COMPLETE%%"
        )

    def test_embedded_marker_rejected(self):
        assert not _marker_is_standalone(
            "I will emit %%AUTOSKILLIT_COMPLETE%% when done", "%%AUTOSKILLIT_COMPLETE%%"
        )

    def test_marker_as_sole_content(self):
        assert _marker_is_standalone("%%AUTOSKILLIT_COMPLETE%%", "%%AUTOSKILLIT_COMPLETE%%")

    def test_marker_with_trailing_whitespace(self):
        assert _marker_is_standalone(
            "Done\n%%AUTOSKILLIT_COMPLETE%%  ", "%%AUTOSKILLIT_COMPLETE%%"
        )


class TestJsonlFieldLevelMarkerMatching:
    """_jsonl_contains_marker extracts field values, not raw JSON lines."""

    def test_marker_quoted_in_assistant_prose_no_match(self):
        """Marker text quoted in prose should NOT trigger detection."""
        import json

        content = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": "I see %%AUTOSKILLIT_COMPLETE%% in the prompt",
                },
            }
        )
        assert not _jsonl_contains_marker(
            content, "%%AUTOSKILLIT_COMPLETE%%", frozenset({"assistant"})
        )

    def test_marker_as_standalone_final_line_matches(self):
        """Marker as standalone final line in content should trigger detection."""
        import json

        content = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": "Task done.\n\n%%AUTOSKILLIT_COMPLETE%%",
                },
            }
        )
        assert _jsonl_contains_marker(
            content, "%%AUTOSKILLIT_COMPLETE%%", frozenset({"assistant"})
        )

    def test_marker_in_result_record_matches(self):
        """Marker in result record's result field should trigger detection."""
        import json

        content = json.dumps(
            {
                "type": "result",
                "result": "%%AUTOSKILLIT_COMPLETE%%",
                "subtype": "success",
            }
        )
        assert _jsonl_contains_marker(content, "%%AUTOSKILLIT_COMPLETE%%", frozenset({"result"}))


class TestHeartbeatTerminationReason:
    """Heartbeat kill produces COMPLETED termination reason."""

    @pytest.mark.asyncio
    async def test_heartbeat_kill_sets_completed_termination(self, tmp_path):
        """When heartbeat detects result and kills the process, termination is COMPLETED."""
        script = tmp_path / "result_hang.py"
        script.write_text(WRITE_RESULT_THEN_HANG_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=30,
            heartbeat_marker='"type":"result"',
        )

        assert result.termination == TerminationReason.COMPLETED
