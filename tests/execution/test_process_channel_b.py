"""Integration tests for session log monitor, heartbeat, and Channel B drain.

These tests use REAL subprocesses (small Python scripts) to reproduce
exact failure modes. They validate the Channel B (session JSONL monitor)
drain-wait logic and heartbeat-based completion detection.

NO MOCKS — that's the whole point.
"""

from __future__ import annotations

import sys
import textwrap
import time
from unittest.mock import patch

import anyio
import pytest

from autoskillit.core.types import ChannelConfirmation, SubprocessResult, TerminationReason
from autoskillit.execution.process import (
    _has_active_api_connection,
    _heartbeat,
    _session_log_monitor,
    run_managed_async,
)

# ---------------------------------------------------------------------------
# Helper scripts — small Python programs that reproduce specific scenarios
# ---------------------------------------------------------------------------

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

# Script that writes %%ORDER_UP%% to session JSONL then immediately exits rc=0
# with an empty type=result on stdout. Used with _phase1_poll=1.0 so the process
# exits before the first Phase 1 poll, exercising the post-exit drain window.
# Pass session_dir as sys.argv[1].
PROCESS_EXIT_THEN_CHANNEL_B_FIRES_SCRIPT = textwrap.dedent("""\
    import sys, json, os, time
    session_dir = sys.argv[1]
    os.makedirs(session_dir, exist_ok=True)
    # Small delay ensures file ctime > spawn_time recorded in run_managed_async
    time.sleep(0.1)
    with open(os.path.join(session_dir, "session.jsonl"), "w") as f:
        record = {"type": "assistant", "message": {"role": "assistant",
                  "content": "%%ORDER_UP%%"}}
        f.write(json.dumps(record) + "\\n")
        f.flush()
    payload = {"type": "result", "subtype": "success", "is_error": False,
               "result": "", "session_id": "test-drain"}
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\\n")
    sys.stdout.flush()
    sys.exit(0)
""")


class TestSessionLogMonitor:
    """Session log monitor detects completion and staleness."""

    @pytest.mark.anyio
    async def test_session_log_monitor_detects_completion(self, tmp_path):
        """Session log with completion marker in assistant record returns 'completion'."""
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
            await anyio.sleep(1.0)
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

        monitor_result: list[str] = []

        async def _run_monitor() -> None:
            monitor_result.append(
                await _session_log_monitor(
                    log_dir, "%%AUTOSKILLIT_COMPLETE%%", stale_threshold=30, spawn_time=spawn_time
                )
            )

        async with anyio.create_task_group() as tg:
            tg.start_soon(append_marker)
            tg.start_soon(_run_monitor)

        assert monitor_result[0] == "completion"

    @pytest.mark.anyio
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

    @pytest.mark.anyio
    async def test_staleness_resets_on_activity(self, tmp_path):
        """Session log that keeps getting written to does not fire staleness."""
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
                await anyio.sleep(0.05)
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
            await anyio.sleep(0.5)

        monitor_result: list[str] = []

        async with anyio.create_task_group() as tg:

            async def _run_monitor() -> None:
                monitor_result.append(
                    await _session_log_monitor(
                        log_dir,
                        "NONEXISTENT_MARKER",
                        stale_threshold=0.3,
                        spawn_time=spawn_time,
                        _phase1_poll=0.01,
                        _phase2_poll=0.05,
                    )
                )
                tg.cancel_scope.cancel()  # cancel writer once monitor fires

            tg.start_soon(keep_writing)
            tg.start_soon(_run_monitor)

        # Staleness should have fired AFTER the writing stopped, not during
        assert monitor_result[0] == "stale"

    @pytest.mark.anyio
    async def test_monitor_ignores_marker_in_non_assistant_records(self, tmp_path):
        """Monitor must NOT fire on completion marker in non-assistant records.

        Reproduces the false-fire: Claude Code writes the prompt (containing
        the completion marker) into a queue-operation/enqueue record at byte 0.
        The monitor should ignore it. Only an assistant-type record triggers.
        """
        import json

        log_dir = tmp_path / "session_logs"
        log_dir.mkdir()
        spawn_time = time.time() - 1

        marker = "%%AUTOSKILLIT_COMPLETE%%"
        # Pre-populate with a queue-operation record containing the marker
        # (this is what Claude Code writes immediately from the injected prompt)
        session_file = log_dir / "abc123.jsonl"

        enqueue_record = json.dumps(
            {
                "type": "queue-operation",
                "operation": "enqueue",
                "content": f"Do the task\n\nORCHESTRATION DIRECTIVE: {marker}",
            }
        )
        session_file.write_text(enqueue_record + "\n")

        poll_count = 0
        polls_done = anyio.Event()

        def on_poll() -> None:
            nonlocal poll_count
            poll_count += 1
            if poll_count >= 5:
                polls_done.set()

        monitor_result: list[str] = []

        async with anyio.create_task_group() as tg:

            async def _run_monitor() -> None:
                monitor_result.append(
                    await _session_log_monitor(
                        log_dir,
                        marker,
                        stale_threshold=30,
                        spawn_time=spawn_time,
                        _phase1_poll=0.01,
                        _phase2_poll=0.05,
                        _on_poll=on_poll,
                    )
                )

            tg.start_soon(_run_monitor)
            with anyio.fail_after(10.0):
                await polls_done.wait()
            assert not monitor_result, "Monitor fired on non-assistant record — false-fire bug"

            # Now append an assistant record with the marker — should fire
            assistant_record = json.dumps(
                {
                    "type": "assistant",
                    "message": {"role": "assistant", "content": f"Done\n\n{marker}"},
                }
            )
            with session_file.open("a") as f:
                f.write(assistant_record + "\n")
            # task group awaits _run_monitor to detect assistant record and complete

        assert monitor_result[0] == "completion"

    @pytest.mark.anyio
    async def test_monitor_realistic_jsonl_sequence(self, tmp_path):
        """Monitor correctly handles the realistic 3-record JSONL sequence.

        Claude Code writes:
        1. queue-operation/enqueue (immediate, contains marker in prompt)
        2. user message (immediate, contains marker in prompt)
        3. assistant message (after delay, contains marker in response)

        Only record 3 should trigger completion.
        """
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

        poll_count = 0
        polls_done = anyio.Event()

        def on_poll() -> None:
            nonlocal poll_count
            poll_count += 1
            if poll_count >= 5:
                polls_done.set()

        monitor_result: list[str] = []

        async with anyio.create_task_group() as tg:

            async def _run_monitor() -> None:
                monitor_result.append(
                    await _session_log_monitor(
                        log_dir,
                        marker,
                        stale_threshold=30,
                        spawn_time=spawn_time,
                        _phase1_poll=0.01,
                        _phase2_poll=0.05,
                        _on_poll=on_poll,
                    )
                )

            tg.start_soon(_run_monitor)
            with anyio.fail_after(10.0):
                await polls_done.wait()
            assert not monitor_result, "Monitor fired on user/enqueue records"

            # Write record 3 (assistant with marker as standalone line)
            assistant_record = json.dumps(
                {
                    "type": "assistant",
                    "message": {"role": "assistant", "content": f"All done\n\n{marker}"},
                }
            )
            with session_file.open("a") as f:
                f.write(assistant_record + "\n")
            # task group awaits _run_monitor to detect assistant record and complete

        assert monitor_result[0] == "completion"


class TestHeartbeatDetectsCompletion:
    """Stdout heartbeat detects completion and triggers kill."""

    @pytest.mark.anyio
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

    @pytest.mark.anyio
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

    @pytest.mark.anyio
    async def test_no_heartbeat_preserves_existing_behavior(self, tmp_path):
        """No heartbeat marker — same hanging script, same timeout behavior."""
        import textwrap

        hang_script = textwrap.dedent("""\
            import sys, time
            sys.stdout.write("before hang\\n")
            sys.stdout.flush()
            time.sleep(3600)
        """)
        script = tmp_path / "hang.py"
        script.write_text(hang_script)

        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=3,
            heartbeat_marker="",
        )

        assert result.termination == TerminationReason.TIMED_OUT


class TestHeartbeatStructuredParsing:
    """Heartbeat uses structured parsing to avoid false-fires."""

    @pytest.mark.anyio
    async def test_heartbeat_ignores_marker_in_string_values(self, tmp_path):
        """Heartbeat must not fire when the marker text appears as a string
        value inside a non-result record (e.g., model discussing JSON formats).
        """
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

        poll_count = 0
        polls_done = anyio.Event()

        def on_poll() -> None:
            nonlocal poll_count
            poll_count += 1
            if poll_count >= 5:
                polls_done.set()

        heartbeat_result: list[str] = []

        async with anyio.create_task_group() as tg:

            async def _run_heartbeat() -> None:
                heartbeat_result.append(
                    await _heartbeat(
                        stdout_path, '"type":"result"', _poll_interval=0.05, _on_poll=on_poll
                    )
                )

            tg.start_soon(_run_heartbeat)
            with anyio.fail_after(10.0):
                await polls_done.wait()
            assert not heartbeat_result, (
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
            # task group awaits _run_heartbeat to detect result record and complete

        assert heartbeat_result[0] == "completion"


class TestHeartbeatTerminationReason:
    """Heartbeat kill produces COMPLETED termination reason."""

    @pytest.mark.anyio
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


class TestHasActiveApiConnection:
    """Unit tests for _has_active_api_connection."""

    def _make_conn(self, port: int, status: str = "ESTABLISHED"):
        from unittest.mock import Mock

        conn = Mock()
        conn.status = status
        conn.raddr = Mock()
        conn.raddr.port = port
        return conn

    def _patch_psutil(self, parent_conns, child_conns_list=None):
        """Returns a context manager patching psutil in process_lifecycle."""
        from unittest.mock import Mock

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
        from unittest.mock import Mock

        conn = Mock()
        conn.status = "ESTABLISHED"
        conn.raddr = None
        with self._patch_psutil([conn]):
            assert _has_active_api_connection(12345) is False

    def test_returns_false_on_nosuchprocess(self):
        import psutil as _psutil

        with patch(
            "autoskillit.execution.process.psutil.Process",
            side_effect=_psutil.NoSuchProcess(12345),
        ):
            assert _has_active_api_connection(12345) is False

    def test_skips_dead_child_gracefully(self):
        from unittest.mock import Mock

        import psutil as _psutil

        mock_parent = Mock()
        mock_parent.net_connections.return_value = []
        mock_dead_child = Mock()
        mock_dead_child.net_connections.side_effect = _psutil.NoSuchProcess(99999)
        mock_live_child = Mock()
        mock_live_child.net_connections.return_value = [self._make_conn(443)]
        mock_parent.children.return_value = [mock_dead_child, mock_live_child]
        with patch("autoskillit.execution.process.psutil.Process", return_value=mock_parent):
            assert _has_active_api_connection(12345) is True

    def test_skips_zombie_child_gracefully(self):
        from unittest.mock import Mock

        import psutil as _psutil

        mock_parent = Mock()
        mock_parent.net_connections.return_value = []
        mock_zombie = Mock()
        mock_zombie.net_connections.side_effect = _psutil.ZombieProcess(99998)
        mock_live_child = Mock()
        mock_live_child.net_connections.return_value = [self._make_conn(443)]
        mock_parent.children.return_value = [mock_zombie, mock_live_child]
        with patch("autoskillit.execution.process.psutil.Process", return_value=mock_parent):
            assert _has_active_api_connection(12345) is True


class TestSessionLogMonitorStaleSuppressionGate:
    """_session_log_monitor suppresses stale when process has an active port-443 connection."""

    @pytest.mark.anyio
    async def test_suppresses_stale_when_port_443_connection_active(self, tmp_path):
        """
        File stops growing. Monitor reaches stale_threshold. But process has
        an ESTABLISHED port-443 connection → suppression fires, clock resets,
        monitor continues. On second check (connection dropped) → stale fires.
        """
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
            with anyio.fail_after(5.0):
                result = await _session_log_monitor(
                    tmp_path,
                    "DONE",
                    stale_threshold=0.05,
                    spawn_time=spawn_time,
                    pid=99999,
                    _phase1_poll=0.01,
                    _phase2_poll=0.05,
                )
        assert result == "stale"
        assert call_count["n"] == 2

    @pytest.mark.anyio
    async def test_fires_stale_immediately_when_no_api_connection(self, tmp_path):
        """Standard stale: file silent, no pid provided, stale fires as before."""
        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10
        with anyio.fail_after(2.0):
            result = await _session_log_monitor(
                tmp_path,
                "DONE",
                stale_threshold=0.05,
                spawn_time=spawn_time,
                # pid omitted (defaults to None)
                _phase1_poll=0.01,
                _phase2_poll=0.05,
            )
        assert result == "stale"

    @pytest.mark.anyio
    async def test_fires_stale_when_pid_is_none_regardless_of_tcp(self, tmp_path):
        """pid=None bypasses TCP check entirely — existing behavior preserved."""
        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10

        with patch("autoskillit.execution.process._has_active_api_connection") as mock_tcp:
            with anyio.fail_after(2.0):
                result = await _session_log_monitor(
                    tmp_path,
                    "DONE",
                    stale_threshold=0.05,
                    spawn_time=spawn_time,
                    pid=None,
                    _phase1_poll=0.01,
                    _phase2_poll=0.05,
                )
        assert result == "stale"
        mock_tcp.assert_not_called()

    @pytest.mark.anyio
    async def test_suppression_emits_warning(self, tmp_path, capsys):
        """A suppression event must log a warning with elapsed time."""
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
                with anyio.fail_after(5.0):
                    await _session_log_monitor(
                        tmp_path,
                        "DONE",
                        stale_threshold=0.05,
                        spawn_time=spawn_time,
                        pid=99999,
                        _phase1_poll=0.01,
                        _phase2_poll=0.05,
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


class TestChannelBDrainWait:
    """Channel B (session monitor) winning before Channel A triggers bounded drain wait."""

    @pytest.mark.anyio
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

    @pytest.mark.anyio
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

    @pytest.mark.anyio
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

    @pytest.mark.anyio
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
        assert result.channel_confirmation == ChannelConfirmation.CHANNEL_B

    @pytest.mark.anyio
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
        assert result.channel_confirmation == ChannelConfirmation.CHANNEL_A

    @pytest.mark.anyio
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
        assert (
            result.channel_confirmation == ChannelConfirmation.CHANNEL_B
        )  # FAILS before fix: True


class TestChannelBFullPipelineAdjudication:
    """Full end-to-end adjudication for Channel B drain-race scenarios."""

    @pytest.mark.anyio
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


class TestHeartbeatScanPosition:
    """_heartbeat uses byte-safe scan position — regression test for multi-byte content."""

    @pytest.mark.anyio
    async def test_heartbeat_detects_record_after_multibyte_content(self, tmp_path):
        """Heartbeat correctly scans past multi-byte UTF-8 content to detect a result record.

        Regression test: ensures the byte-offset refactor (scan_pos tracks bytes, not chars)
        does not break detection when prior content contains multi-byte characters.
        """
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

        with anyio.fail_after(10):
            result = await _heartbeat(stdout_path, '"type":"result"')
        assert result == "completion"


class TestChannelBDrainRacePipelineAdjudication:
    """Integration: COMPLETED (Channel B drain timeout) flows through _build_skill_result.

    Uses the existing CHANNEL_B_NO_STDOUT_SCRIPT: session monitor fires, drain expires,
    process is killed with empty stdout. _build_skill_result must apply the Channel B
    provenance bypass (data_confirmed=False → success=True without calling _compute_success).
    """

    @pytest.mark.anyio
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
        assert result.channel_confirmation == ChannelConfirmation.CHANNEL_B

        skill_result = _build_skill_result(
            result,
            completion_marker="%%ORDER_UP%%",
            skill_command="resolve-failures",
            audit=None,
        )

        assert skill_result.success is True
        assert skill_result.needs_retry is False


class TestNaturalExitWithChannelConfirmation:
    """NATURAL_EXIT + channel signals flow correctly through _build_skill_result.

    Test 1C: Validates the downstream adjudication path for the combination
    produced by the signal-accumulation fix when wait_task and session_monitor
    both complete in the same event loop tick.
    """

    def test_natural_exit_channel_b_empty_stdout_is_success(self):
        """NATURAL_EXIT + CHANNEL_B + empty stdout → success=True, no retry.

        _compute_success: CHANNEL_B provenance bypass fires → True.
        _compute_retry: NATURAL_EXIT + CHANNEL_B channel guard fires → (False, NONE).
        """
        from autoskillit.execution.headless import _build_skill_result

        result = SubprocessResult(
            returncode=0,
            stdout="",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=0,
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
        )
        skill_result = _build_skill_result(
            result, completion_marker="", skill_command="test", audit=None
        )
        assert skill_result.success is True
        assert skill_result.needs_retry is False


class TestPostExitDrainWindow:
    """Symmetric drain window: process exits first, Channel B gets a bounded window to deposit."""

    @pytest.mark.anyio
    async def test_drain_window_allows_channel_b_to_deposit(self, tmp_path):
        """Process exits before Phase 1 polls; drain window lets Channel B detect marker.

        Uses _phase1_poll=1.0 to guarantee the process exits (~100ms) before the
        first Phase 1 poll fires. The drain window (completion_drain_timeout=5.0)
        gives the session monitor enough time to complete its poll and detect the
        marker in the JSONL file, producing CHANNEL_B confirmation.
        """
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        script = tmp_path / "process_exit_then_channel_b.py"
        script.write_text(PROCESS_EXIT_THEN_CHANNEL_B_FIRES_SCRIPT)

        result = await run_managed_async(
            [sys.executable, str(script), str(session_dir)],
            cwd=tmp_path,
            timeout=30,
            session_log_dir=session_dir,
            completion_marker="%%ORDER_UP%%",
            completion_drain_timeout=5.0,
            _phase1_poll=1.0,
            _phase2_poll=0.05,
            _heartbeat_poll=0.05,
        )

        assert result.channel_confirmation == ChannelConfirmation.CHANNEL_B

    @pytest.mark.anyio
    async def test_drain_window_times_out_when_no_session_jsonl(self, tmp_path):
        """Process exits with no session JSONL; drain window times out, UNMONITORED preserved.

        The drain window expires after completion_drain_timeout seconds without
        Channel B depositing. Existing behavior (UNMONITORED) is unchanged.
        """
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        # Script that writes empty result to stdout and exits — no JSONL written
        script = tmp_path / "empty_exit.py"
        script.write_text(
            textwrap.dedent("""\
            import sys, json
            payload = {"type": "result", "subtype": "success", "is_error": False,
                       "result": "", "session_id": "test-stop-delay"}
            sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\\n")
            sys.stdout.flush()
            sys.exit(0)
        """)
        )

        result = await run_managed_async(
            [sys.executable, str(script)],
            cwd=tmp_path,
            timeout=10,
            session_log_dir=session_dir,
            completion_marker="%%ORDER_UP%%",
            completion_drain_timeout=0.2,
            _phase1_poll=0.05,
            _phase2_poll=0.05,
            _heartbeat_poll=0.05,
        )

        assert result.channel_confirmation == ChannelConfirmation.UNMONITORED
