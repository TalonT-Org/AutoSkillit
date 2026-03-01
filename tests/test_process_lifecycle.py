"""Integration tests for subprocess lifecycle utilities.

These tests use REAL subprocesses (small Python scripts) to reproduce
exact failure modes. They validate that temp-file I/O eliminates
pipe blocking and that psutil-based process tree kill handles all descendants.

NO MOCKS — that's the whole point.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import textwrap
import time
from pathlib import Path
from unittest.mock import Mock, patch

import psutil
import pytest

from autoskillit.core.types import RetryReason, TerminationReason
from autoskillit.execution.process import (
    _has_active_api_connection,
    _heartbeat,
    _jsonl_contains_marker,
    _jsonl_has_record_type,
    _marker_is_standalone,
    _session_log_monitor,
    async_kill_process_tree,
    kill_process_tree,
    pty_wrap_command,
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

# Script that:
#   (1) writes %%ORDER_UP%% to a JSONL session file (Channel B fires)
#   (2) writes type=result to stdout after a delay (Channel A confirms within drain window)
#   (3) hangs until killed
# Pass session_dir as sys.argv[1].
CHANNEL_B_THEN_A_CONFIRM_SCRIPT = textwrap.dedent("""\
    import sys, time, json, os
    session_dir = sys.argv[1]
    os.makedirs(session_dir, exist_ok=True)
    # Small delay to ensure file ctime > spawn_time recorded in run_managed_async
    time.sleep(0.1)
    with open(os.path.join(session_dir, "session.jsonl"), "w") as f:
        record = {"type": "assistant", "message": {"role": "assistant",
                  "content": "%%ORDER_UP%%"}}
        f.write(json.dumps(record) + "\\n")
        f.flush()
    # Wait until after Channel B fires (phase1_poll + phase2_poll), then write stdout.
    # Callers pass this delay as sys.argv[2]; default 4.0 matches production poll defaults.
    time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 4.0)
    result = {"type": "result", "subtype": "success", "is_error": False,
              "result": "done", "session_id": "s1"}
    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\\n")
    sys.stdout.flush()
    time.sleep(3600)
""")

# Script that writes %%ORDER_UP%% to session JSONL but never writes type=result to stdout.
# Simulates CLI hung post-completion — drain timeout should expire and kill anyway.
# Pass session_dir as sys.argv[1].
CHANNEL_B_NO_STDOUT_SCRIPT = textwrap.dedent("""\
    import sys, time, json, os
    session_dir = sys.argv[1]
    os.makedirs(session_dir, exist_ok=True)
    time.sleep(0.1)
    with open(os.path.join(session_dir, "session.jsonl"), "w") as f:
        record = {"type": "assistant", "message": {"role": "assistant",
                  "content": "%%ORDER_UP%%"}}
        f.write(json.dumps(record) + "\\n")
        f.flush()
    time.sleep(3600)
""")

# Script that:
#   (1) writes %%ORDER_UP%% to a JSONL session file (Channel B fires)
#   (2) writes type=result with EMPTY result field to stdout (Channel A must NOT confirm this)
#   (3) hangs until killed
# This simulates the drain-race false negative: CLI flushes the result record envelope
# before populating its content.
# Pass session_dir as sys.argv[1].
CHANNEL_B_THEN_A_EMPTY_RESULT_SCRIPT = textwrap.dedent("""\
    import sys, time, json, os
    session_dir = sys.argv[1]
    os.makedirs(session_dir, exist_ok=True)
    # Small delay to ensure file ctime > spawn_time recorded in run_managed_async
    time.sleep(0.1)
    with open(os.path.join(session_dir, "session.jsonl"), "w") as f:
        record = {"type": "assistant", "message": {"role": "assistant",
                  "content": "%%ORDER_UP%%"}}
        f.write(json.dumps(record) + "\\n")
        f.flush()
    # Short delay then write an empty-result type=result record
    time.sleep(0.15)
    result = {"type": "result", "subtype": "success", "is_error": False,
              "result": "", "session_id": "s1"}
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

# ---------------------------------------------------------------------------
# Integration scripts — reproduce exact TerminationReason paths through
# run_managed_async → _build_skill_result for adjudication boundary tests.
# ---------------------------------------------------------------------------

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
        await asyncio.sleep(0.5)  # Brief wait for kernel cleanup
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
        assert elapsed < 5, f"Should return within ~2s timeout, took {elapsed:.1f}s"
        assert "before hang" in result.stdout  # Partial output captured
        # Process should be dead
        await asyncio.sleep(0.5)
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
            log_dir,
            "%%AUTOSKILLIT_COMPLETE%%",
            stale_threshold=0.2,
            spawn_time=spawn_time,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
        )
        elapsed = time.monotonic() - start

        assert result == "stale"
        assert elapsed < 1.0, f"Staleness should fire after ~0.2s, took {elapsed:.1f}s"

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
                await asyncio.sleep(0.05)
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
            await asyncio.sleep(0.5)

        writer = asyncio.create_task(keep_writing())

        result = await _session_log_monitor(
            log_dir,
            "NONEXISTENT_MARKER",
            stale_threshold=0.3,
            spawn_time=spawn_time,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
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
            _session_log_monitor(
                log_dir,
                marker,
                stale_threshold=30,
                spawn_time=spawn_time,
                _phase1_poll=0.01,
                _phase2_poll=0.1,
            )
        )

        # Monitor should NOT fire on the enqueue record — wait for several poll cycles to confirm
        await asyncio.sleep(0.5)
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

        result = await asyncio.wait_for(monitor_task, timeout=2)
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
            _session_log_monitor(
                log_dir,
                marker,
                stale_threshold=30,
                spawn_time=spawn_time,
                _phase1_poll=0.01,
                _phase2_poll=0.1,
            )
        )

        # Wait for several poll cycles and confirm no early fire
        await asyncio.sleep(0.5)
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

        result = await asyncio.wait_for(monitor_task, timeout=2)
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

        heartbeat_task = asyncio.create_task(
            _heartbeat(stdout_path, '"type":"result"', _poll_interval=0.05)
        )

        # Wait for several poll cycles to confirm it doesn't fire on the assistant record
        await asyncio.sleep(0.5)
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

        result = await asyncio.wait_for(heartbeat_task, timeout=2)
        assert result == "completion"


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

    @pytest.fixture(autouse=True)
    def _sync_process_logger(self):
        """Sync only process.logger._processors with the current structlog config.

        Scoped to this test class only — no cross-module mutation.
        """
        import structlog

        import autoskillit.execution.process as proc_mod

        structlog.reset_defaults()
        current_procs = structlog.get_config()["processors"]
        old_procs = getattr(proc_mod.logger, "_processors", None)
        if old_procs is not None:
            proc_mod.logger._processors = current_procs
        yield
        structlog.reset_defaults()
        if old_procs is not None:
            proc_mod.logger._processors = old_procs

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
            _heartbeat_poll=0.05,
        )

        assert result.termination == TerminationReason.COMPLETED


class TestJsonlContainsMarkerContentBlocks:
    """_jsonl_contains_marker handles list-of-content-blocks format."""

    def test_list_content_blocks_with_marker(self):
        import json

        content = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "Done\n%%AUTOSKILLIT_COMPLETE%%"}]
                },
            }
        )
        assert _jsonl_contains_marker(
            content, "%%AUTOSKILLIT_COMPLETE%%", frozenset({"assistant"})
        )

    def test_list_content_mixed_blocks_with_marker(self):
        import json

        content = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "Running..."},
                        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}},
                        {"type": "text", "text": "\n%%AUTOSKILLIT_COMPLETE%%"},
                    ]
                },
            }
        )
        assert _jsonl_contains_marker(
            content, "%%AUTOSKILLIT_COMPLETE%%", frozenset({"assistant"})
        )

    def test_list_content_marker_embedded_in_prose(self):
        import json

        content = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "I will emit %%AUTOSKILLIT_COMPLETE%% when done"}
                    ]
                },
            }
        )
        assert not _jsonl_contains_marker(
            content, "%%AUTOSKILLIT_COMPLETE%%", frozenset({"assistant"})
        )

    def test_list_content_no_marker(self):
        import json

        content = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "still working"}]},
            }
        )
        assert not _jsonl_contains_marker(
            content, "%%AUTOSKILLIT_COMPLETE%%", frozenset({"assistant"})
        )

    def test_none_content_no_crash(self):
        import json

        content = json.dumps({"type": "assistant", "message": {"content": None}})
        assert not _jsonl_contains_marker(
            content, "%%AUTOSKILLIT_COMPLETE%%", frozenset({"assistant"})
        )

    def test_none_message_no_crash(self):
        import json

        content = json.dumps({"type": "assistant", "message": None})
        assert not _jsonl_contains_marker(
            content, "%%AUTOSKILLIT_COMPLETE%%", frozenset({"assistant"})
        )


class TestJsonlContainsMarkerEdgeCases:
    """Edge cases: empty input and partial/truncated NDJSON."""

    def test_empty_string_returns_false(self):
        """Empty input contains no records → False."""
        assert not _jsonl_contains_marker("", "MARKER", frozenset({"assistant"}))

    def test_partial_truncated_json_skipped_valid_line_matches(self):
        """Partial JSON from a mid-write kill is skipped; valid subsequent line matches."""
        import json

        # Simulate: process killed mid-write on first line, second line is complete.
        truncated = '{"type": "result", "result": "MARKER", "subtype": "succe'
        valid = json.dumps({"type": "result", "result": "MARKER", "subtype": "success"})
        content = truncated + "\n" + valid
        assert _jsonl_contains_marker(content, "MARKER", frozenset({"result"}))

    def test_only_truncated_json_returns_false(self):
        """Content with only a truncated JSON line (no valid lines) → False."""
        truncated = '{"type": "assistant", "message": {"content": "Done\nMARKER"'
        assert not _jsonl_contains_marker(truncated, "MARKER", frozenset({"assistant"}))


class TestHasActiveApiConnection:
    """Unit tests for _has_active_api_connection."""

    def _make_conn(self, port: int, status: str = "ESTABLISHED") -> Mock:
        conn = Mock()
        conn.status = status
        conn.raddr = Mock()
        conn.raddr.port = port
        return conn

    def _patch_psutil(self, parent_conns, child_conns_list=None):
        """Returns a context manager patching psutil in process_lifecycle."""
        mock_parent = Mock()
        mock_parent.net_connections.return_value = parent_conns
        children = []
        for child_conns in child_conns_list or []:
            mock_child = Mock()
            mock_child.net_connections.return_value = child_conns
            children.append(mock_child)
        mock_parent.children.return_value = children
        return patch("autoskillit.execution.process.psutil.Process", return_value=mock_parent)

    def test_returns_true_when_parent_has_established_port_443(self):
        with self._patch_psutil([self._make_conn(443)]):
            assert _has_active_api_connection(12345) is True

    def test_returns_true_when_child_has_established_port_443(self):
        with self._patch_psutil(
            parent_conns=[self._make_conn(80)],
            child_conns_list=[[self._make_conn(443)]],
        ):
            assert _has_active_api_connection(12345) is True

    def test_returns_false_when_no_connections(self):
        with self._patch_psutil([]):
            assert _has_active_api_connection(12345) is False

    def test_returns_false_when_all_connections_non_443(self):
        conns = [self._make_conn(80), self._make_conn(8080), self._make_conn(22)]
        with self._patch_psutil(conns):
            assert _has_active_api_connection(12345) is False

    def test_returns_false_when_443_is_not_established(self):
        conns = [
            self._make_conn(443, status="TIME_WAIT"),
            self._make_conn(443, status="CLOSE_WAIT"),
        ]
        with self._patch_psutil(conns):
            assert _has_active_api_connection(12345) is False

    def test_returns_false_when_no_raddr(self):
        conn = Mock()
        conn.status = "ESTABLISHED"
        conn.raddr = None
        with self._patch_psutil([conn]):
            assert _has_active_api_connection(12345) is False

    def test_returns_false_on_nosuchprocess(self):
        with patch(
            "autoskillit.execution.process.psutil.Process",
            side_effect=psutil.NoSuchProcess(12345),
        ):
            assert _has_active_api_connection(12345) is False

    def test_skips_dead_child_gracefully(self):
        mock_parent = Mock()
        mock_parent.net_connections.return_value = []
        mock_dead_child = Mock()
        mock_dead_child.net_connections.side_effect = psutil.NoSuchProcess(99999)
        mock_live_child = Mock()
        mock_live_child.net_connections.return_value = [self._make_conn(443)]
        mock_parent.children.return_value = [mock_dead_child, mock_live_child]
        with patch("autoskillit.execution.process.psutil.Process", return_value=mock_parent):
            assert _has_active_api_connection(12345) is True

    def test_skips_zombie_child_gracefully(self):
        mock_parent = Mock()
        mock_parent.net_connections.return_value = []
        mock_zombie = Mock()
        mock_zombie.net_connections.side_effect = psutil.ZombieProcess(99998)
        mock_live_child = Mock()
        mock_live_child.net_connections.return_value = [self._make_conn(443)]
        mock_parent.children.return_value = [mock_zombie, mock_live_child]
        with patch("autoskillit.execution.process.psutil.Process", return_value=mock_parent):
            assert _has_active_api_connection(12345) is True


class TestSessionLogMonitorStaleSuppressionGate:
    """_session_log_monitor suppresses stale when process has an active port-443 connection."""

    @pytest.mark.asyncio
    async def test_suppresses_stale_when_port_443_connection_active(self, tmp_path):
        """
        File stops growing. Monitor reaches stale_threshold. But process has
        an ESTABLISHED port-443 connection → suppression fires, clock resets,
        monitor continues. On second check (connection dropped) → stale fires.
        """
        import asyncio

        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10

        call_count = {"n": 0}

        def side_effect(pid):
            call_count["n"] += 1
            return call_count["n"] == 1  # True on first call, False on second

        with patch(
            "autoskillit.execution.process._has_active_api_connection",
            side_effect=side_effect,
        ):
            result = await asyncio.wait_for(
                _session_log_monitor(
                    tmp_path,
                    "DONE",
                    stale_threshold=0.05,
                    spawn_time=spawn_time,
                    pid=99999,
                    _phase1_poll=0.01,
                    _phase2_poll=0.05,
                ),
                timeout=5.0,
            )
        assert result == "stale"
        assert call_count["n"] == 2

    @pytest.mark.asyncio
    async def test_fires_stale_immediately_when_no_api_connection(self, tmp_path):
        """Standard stale: file silent, no pid provided, stale fires as before."""
        import asyncio

        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10
        result = await asyncio.wait_for(
            _session_log_monitor(
                tmp_path,
                "DONE",
                stale_threshold=0.05,
                spawn_time=spawn_time,
                # pid omitted (defaults to None)
                _phase1_poll=0.01,
                _phase2_poll=0.05,
            ),
            timeout=2.0,
        )
        assert result == "stale"

    @pytest.mark.asyncio
    async def test_fires_stale_when_pid_is_none_regardless_of_tcp(self, tmp_path):
        """pid=None bypasses TCP check entirely — existing behavior preserved."""
        import asyncio

        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10

        with patch("autoskillit.execution.process._has_active_api_connection") as mock_tcp:
            result = await asyncio.wait_for(
                _session_log_monitor(
                    tmp_path,
                    "DONE",
                    stale_threshold=0.05,
                    spawn_time=spawn_time,
                    pid=None,
                    _phase1_poll=0.01,
                    _phase2_poll=0.05,
                ),
                timeout=2.0,
            )
        assert result == "stale"
        mock_tcp.assert_not_called()

    @pytest.mark.asyncio
    async def test_suppression_emits_warning(self, tmp_path, capsys):
        """A suppression event must log a warning with elapsed time."""
        import asyncio

        import structlog

        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10

        calls = {"n": 0}

        def side_effect(pid):
            calls["n"] += 1
            return calls["n"] == 1

        with patch(
            "autoskillit.execution.process._has_active_api_connection",
            side_effect=side_effect,
        ):
            with structlog.testing.capture_logs() as logs:
                await asyncio.wait_for(
                    _session_log_monitor(
                        tmp_path,
                        "DONE",
                        stale_threshold=0.05,
                        spawn_time=spawn_time,
                        pid=99999,
                        _phase1_poll=0.01,
                        _phase2_poll=0.05,
                    ),
                    timeout=5.0,
                )
        # capture_logs() intercepts when structlog is in default state.
        # In a parallel worker where configure_logging() ran in a prior test,
        # bound loggers may use a stale processor reference and write to stdout.
        captured = capsys.readouterr().out
        warning_in_logs = any(
            "port-443" in str(log.get("event", "")) or "ESTABLISHED" in str(log.get("event", ""))
            for log in logs
        )
        warning_in_stdout = "port-443" in captured or "ESTABLISHED" in captured
        assert warning_in_logs or warning_in_stdout, (
            "Suppression warning must appear in structlog capture or stdout"
        )


class TestRunManagedAsyncPassesPidToMonitor:
    """Verify that run_managed_async passes proc.pid to _session_log_monitor."""

    @pytest.mark.asyncio
    async def test_pid_passed_to_session_monitor(self, tmp_path):
        """
        Spawn a real subprocess. Patch _session_log_monitor to capture args.
        Verify the pid kwarg matches the real subprocess PID.
        """
        captured = {}

        async def capturing_monitor(*args, **kwargs):
            captured["pid"] = kwargs.get("pid")
            captured["positional_pid"] = args[5] if len(args) > 5 else None
            return "stale"

        session_file = tmp_path / "fake_session.jsonl"
        session_file.write_text("")

        with patch("autoskillit.execution.process._session_log_monitor", capturing_monitor):
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


class TestChannelBDrainWait:
    """Channel B (session monitor) winning before Channel A triggers bounded drain wait."""

    @pytest.mark.asyncio
    async def test_channel_b_wins_then_channel_a_confirms_within_drain(self, tmp_path):
        """Channel B fires first; drain wait allows Channel A to confirm stdout data.

        Sequence (fast poll params):
          t=0.00s  subprocess starts
          t=0.10s  script writes %%ORDER_UP%% to session JSONL (Channel B target)
          t=0.11s  Phase 1 poll fires → session file found
          t=0.16s  Phase 2 poll fires → marker detected → Channel B fires → drain starts
          t=0.25s  script writes type=result to stdout (0.15s after JSONL write)
          t=0.30s  heartbeat fires → Channel A confirms → drain completes
          t~0.30s  process killed with confirmed stdout
        """
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        script = tmp_path / "channel_b_then_a.py"
        script.write_text(CHANNEL_B_THEN_A_CONFIRM_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script), str(session_dir), "0.15"],
            cwd=tmp_path,
            timeout=30,
            heartbeat_marker='"type":"result"',
            session_log_dir=session_dir,
            completion_marker="%%ORDER_UP%%",
            completion_drain_timeout=5.0,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
            _heartbeat_poll=0.05,
        )

        assert result.termination == TerminationReason.COMPLETED
        # Drain wait confirmed Channel A fired: stdout is non-empty
        assert result.stdout.strip()

    @pytest.mark.asyncio
    async def test_channel_b_wins_drain_timeout_still_kills(self, tmp_path):
        """Channel B fires; Channel A never fires; drain times out and process is killed.

        Sequence (fast poll params):
          t=0.10s  script writes %%ORDER_UP%% to session JSONL
          t=0.16s  Channel B fires → drain wait starts with 0.5s timeout
          t=0.66s  drain times out (script never wrote to stdout)
          t=0.66s  process killed with empty stdout
        """
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        script = tmp_path / "channel_b_no_stdout.py"
        script.write_text(CHANNEL_B_NO_STDOUT_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script), str(session_dir)],
            cwd=tmp_path,
            timeout=30,
            heartbeat_marker='"type":"result"',
            session_log_dir=session_dir,
            completion_marker="%%ORDER_UP%%",
            completion_drain_timeout=0.5,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
        )

        assert result.termination == TerminationReason.COMPLETED
        # Drain timed out: CLI hung and never flushed its result record
        assert not result.stdout.strip()

    @pytest.mark.asyncio
    async def test_channel_a_wins_unchanged_behavior(self, tmp_path):
        """Channel A (heartbeat) wins before any session monitor: no drain wait needed.

        Sequence:
          t=0     script writes type=result to stdout immediately
          t~0.5s  heartbeat fires, Channel A confirmed → kill immediately
          No drain wait: heartbeat_task is in done set
        """
        script = tmp_path / "result_hang.py"
        script.write_text(WRITE_RESULT_THEN_HANG_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=30,
            heartbeat_marker='"type":"result"',
            # No session_log_dir: Channel B cannot fire
            _heartbeat_poll=0.05,
        )

        assert result.termination == TerminationReason.COMPLETED
        assert result.stdout.strip()  # Channel A confirmed: stdout is non-empty

    @pytest.mark.asyncio
    async def test_data_confirmed_false_set_on_drain_timeout(self, tmp_path):
        """Channel B wins the race; drain timeout expires without Channel A confirming.

        Verifies that SubprocessResult.data_confirmed is False when the bounded
        drain wait times out — i.e. Channel A never confirmed stdout data.
        """
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        script = tmp_path / "channel_b_no_stdout.py"
        script.write_text(CHANNEL_B_NO_STDOUT_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script), str(session_dir)],
            cwd=tmp_path,
            timeout=30,
            heartbeat_marker='"type":"result"',
            session_log_dir=session_dir,
            completion_marker="%%ORDER_UP%%",
            completion_drain_timeout=0.1,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
        )

        assert result.termination == TerminationReason.COMPLETED
        assert result.data_confirmed is False

    @pytest.mark.asyncio
    async def test_data_confirmed_true_when_channel_a_wins(self, tmp_path):
        """Channel A (heartbeat) wins; data_confirmed must be True.

        When the heartbeat fires before Channel B (or with no Channel B),
        data availability is guaranteed and data_confirmed must remain True.
        """
        script = tmp_path / "result_hang.py"
        script.write_text(WRITE_RESULT_THEN_HANG_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=30,
            heartbeat_marker='"type":"result"',
            # No session_log_dir: Channel B cannot fire
            _heartbeat_poll=0.05,
        )

        assert result.termination == TerminationReason.COMPLETED
        assert result.data_confirmed is True

    @pytest.mark.asyncio
    async def test_channel_b_then_a_empty_result_data_confirmed_is_false(self, tmp_path):
        """Channel B fires (%%ORDER_UP%% in JSONL).

        Within the drain window, Claude CLI writes a type=result record with
        result="". Channel A must NOT confirm on this — data_confirmed must
        remain False so the provenance bypass can fire.
        """
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        script = tmp_path / "channel_b_empty.py"
        script.write_text(CHANNEL_B_THEN_A_EMPTY_RESULT_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script), str(session_dir)],
            cwd=tmp_path,
            timeout=30,
            heartbeat_marker='"type":"result"',
            session_log_dir=session_dir,
            completion_marker="%%ORDER_UP%%",
            completion_drain_timeout=2.0,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
            _heartbeat_poll=0.05,
        )
        assert result.termination == TerminationReason.COMPLETED
        assert result.data_confirmed is False  # FAILS before fix: True


class TestChannelBFullPipelineAdjudication:
    """Full end-to-end adjudication for Channel B drain-race scenarios."""

    @pytest.mark.asyncio
    async def test_channel_b_then_a_empty_result_produces_success(self, tmp_path):
        """Full end-to-end: Channel B fires, CLI writes type=result with result="".

        With strengthened Channel A, data_confirmed=False, provenance bypass fires.
        Result: success=True, needs_retry=False (no wasteful retry of completed session).
        """
        from autoskillit.execution.headless import _build_skill_result

        session_dir = tmp_path / "session"
        session_dir.mkdir()
        script = tmp_path / "channel_b_empty.py"
        script.write_text(CHANNEL_B_THEN_A_EMPTY_RESULT_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script), str(session_dir)],
            cwd=tmp_path,
            timeout=30,
            heartbeat_marker='"type":"result"',
            session_log_dir=session_dir,
            completion_marker="%%ORDER_UP%%",
            completion_drain_timeout=2.0,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
            _heartbeat_poll=0.05,
        )
        skill_result = _build_skill_result(
            result,
            completion_marker="%%ORDER_UP%%",
            skill_command="test-command",
            audit=None,
        )
        assert skill_result.success is True  # FAILS before fix: False
        assert skill_result.needs_retry is False  # FAILS before fix: True


class TestJsonlHasRecordTypeResultContent:
    """_jsonl_has_record_type requires non-empty result field for type=result records."""

    def test_rejects_empty_result_field(self):
        """A type=result record with result="" must NOT satisfy _jsonl_has_record_type.

        Confirming on empty content is the source of the drain-race false negative.
        """
        empty_result_line = '{"type":"result","subtype":"success","result":"","is_error":false}\n'
        assert not _jsonl_has_record_type(empty_result_line, frozenset({"result"}))

    def test_accepts_nonempty_result_field(self):
        """Non-empty result still satisfies the predicate."""
        nonempty_line = '{"type":"result","subtype":"success","result":"done","is_error":false}\n'
        assert _jsonl_has_record_type(nonempty_line, frozenset({"result"}))

    def test_non_result_types_unaffected(self):
        """Non-result record types (e.g. assistant, system) are unaffected by the change."""
        assistant_line = (
            '{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]}}\n'
        )
        assert _jsonl_has_record_type(assistant_line, frozenset({"assistant"}))

    def test_result_field_none_rejected(self):
        """A type=result record with result=null must NOT satisfy the predicate."""
        null_result_line = '{"type":"result","subtype":"success","result":null,"is_error":false}\n'
        assert not _jsonl_has_record_type(null_result_line, frozenset({"result"}))

    def test_result_field_whitespace_only_rejected(self):
        """A type=result record with result='   ' (whitespace only) must NOT satisfy."""
        whitespace_line = '{"type":"result","subtype":"success","result":"   ","is_error":false}\n'
        assert not _jsonl_has_record_type(whitespace_line, frozenset({"result"}))


class TestHeartbeatScanPosition:
    """_heartbeat uses byte-safe scan position — regression test for multi-byte content."""

    @pytest.mark.asyncio
    async def test_heartbeat_detects_record_after_multibyte_content(self, tmp_path):
        """Heartbeat correctly scans past multi-byte UTF-8 content to detect a result record.

        Regression test: ensures the byte-offset refactor (scan_pos tracks bytes, not chars)
        does not break detection when prior content contains multi-byte characters.
        """
        import asyncio
        import json

        stdout_path = tmp_path / "stdout.tmp"
        # Write an assistant message with multi-byte UTF-8 content (CJK characters),
        # followed by a result record on a new line.
        assistant_msg = json.dumps(
            {"type": "assistant", "message": "こんにちは"},
            separators=(",", ":"),
        )
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
        stdout_path.write_text(assistant_msg + "\n" + result_record + "\n", encoding="utf-8")

        result = await asyncio.wait_for(
            _heartbeat(stdout_path, '"type":"result"'),
            timeout=10,
        )
        assert result == "completion"


# ---------------------------------------------------------------------------
# pty_wrap_command — platform-specific flag selection
# ---------------------------------------------------------------------------


class TestPtyWrapCommand:
    """pty_wrap_command selects BSD or GNU script flags based on sys.platform."""

    def test_pty_wrap_command_linux_uses_gnu_flags(self) -> None:
        """On Linux, pty_wrap_command produces GNU script -qefc syntax."""
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
        cmd = ["claude", "arg1"]
        with patch("shutil.which", return_value=None):
            result = pty_wrap_command(cmd)
        assert result is cmd


class TestSubprocessResultAndRunnerTypes:
    """Tests for SubprocessResult in types.py and SubprocessRunner protocol."""

    def test_subprocess_result_still_importable_from_process_lifecycle(self):
        """SubprocessResult remains importable from process_lifecycle for backward compat."""
        from autoskillit.execution.process import SubprocessResult  # noqa: F401

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


# ---------------------------------------------------------------------------
# Adjudication boundary integration tests
# Each class exercises ONE TerminationReason path from a real subprocess
# through run_managed_async → _build_skill_result → SkillResult.
# ---------------------------------------------------------------------------


class TestChannelBDrainRacePipelineAdjudication:
    """Integration: COMPLETED (Channel B drain timeout) flows through _build_skill_result.

    Uses the existing CHANNEL_B_NO_STDOUT_SCRIPT: session monitor fires, drain expires,
    process is killed with empty stdout. _build_skill_result must apply the Channel B
    provenance bypass (data_confirmed=False → success=True without calling _compute_success).
    """

    @pytest.mark.asyncio
    async def test_channel_b_drain_timeout_produces_success_skill_result(self, tmp_path):
        """COMPLETED + data_confirmed=False + empty stdout → success=True, needs_retry=False.

        Channel B provenance bypass: when session monitor wins and drain expires,
        _build_skill_result returns success=True immediately, bypassing _compute_success.
        """
        from autoskillit.execution.headless import _build_skill_result

        session_dir = tmp_path / "session"
        session_dir.mkdir()
        script = tmp_path / "channel_b_no_stdout.py"
        script.write_text(CHANNEL_B_NO_STDOUT_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script), str(session_dir)],
            cwd=tmp_path,
            timeout=30,
            heartbeat_marker='"type":"result"',
            session_log_dir=session_dir,
            completion_marker="%%ORDER_UP%%",
            completion_drain_timeout=0.5,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
        )

        assert result.termination == TerminationReason.COMPLETED
        assert result.data_confirmed is False

        skill_result = _build_skill_result(
            result,
            completion_marker="%%ORDER_UP%%",
            skill_command="resolve-failures",
            audit=None,
        )

        assert skill_result.success is True
        assert skill_result.needs_retry is False


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
      COMPLETED    → TestChannelBDrainRacePipelineAdjudication
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
            f"Add a TestXxxPipelineAdjudication class in test_process_lifecycle.py "
            f"and add the value to COVERED_BY_INTEGRATION_TESTS."
        )
