"""Unit tests for _session_log_monitor, _heartbeat, and _has_active_api_connection."""

from __future__ import annotations

import sys
import time
from unittest.mock import MagicMock, patch

import anyio
import psutil
import pytest

from autoskillit.core.types import ChannelBStatus, TerminationReason
from autoskillit.execution.process import (
    RaceAccumulator,
    _has_active_api_connection,
    _has_active_child_processes,
    _heartbeat,
    _session_log_monitor,
    _watch_session_log,
    run_managed_async,
)

# ---------------------------------------------------------------------------
# Helper scripts — small Python programs that reproduce specific scenarios
# ---------------------------------------------------------------------------

# Script that writes a JSON result line then hangs (simulates Claude CLI completed-but-hung)
WRITE_RESULT_THEN_HANG_SCRIPT = (
    "import sys, time, json\n"
    'result = {"type": "result", "subtype": "success", "is_error": False,\n'
    '          "result": "done", "session_id": "s1"}\n'
    'sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\\n")\n'
    "sys.stdout.flush()\n"
    "time.sleep(3600)\n"
)

# Script that writes non-matching output then hangs
PARTIAL_OUTPUT_THEN_HANG_SCRIPT = (
    "import sys, time\n"
    'sys.stdout.write("partial output\\n")\n'
    "sys.stdout.flush()\n"
    "time.sleep(3600)\n"
)


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

        assert monitor_result[0].status == "completion"
        assert monitor_result[0].session_id == "abc123"

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

        assert result.status == "stale"
        assert result.session_id == "abc123"
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
        assert monitor_result[0].status == "stale"

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

        assert monitor_result[0].status == "completion"

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

        assert monitor_result[0].status == "completion"


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
        return patch(
            "autoskillit.execution._process_monitor.psutil.Process", return_value=mock_parent
        )

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
            "autoskillit.execution._process_monitor.psutil.Process",
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
        with patch(
            "autoskillit.execution._process_monitor.psutil.Process", return_value=mock_parent
        ):
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
        with patch(
            "autoskillit.execution._process_monitor.psutil.Process", return_value=mock_parent
        ):
            assert _has_active_api_connection(12345) is True


class TestHasActiveChildProcesses:
    """Unit tests for _has_active_child_processes."""

    def _make_child(self, cpu: float | type[Exception]) -> MagicMock:
        """Build a mock psutil child process."""
        child = MagicMock()
        if isinstance(cpu, type) and issubclass(cpu, Exception):
            child.cpu_percent.side_effect = cpu(pid=999)
        else:
            child.cpu_percent.return_value = cpu
        return child

    def _patch_children(self, children, monkeypatch, parent_raises=None):
        mock_proc = MagicMock()
        if parent_raises:
            monkeypatch.setattr(
                "autoskillit.execution._process_monitor.psutil.Process",
                MagicMock(side_effect=parent_raises(pid=1234)),
            )
            return
        mock_proc.children.return_value = children
        monkeypatch.setattr(
            "autoskillit.execution._process_monitor.psutil.Process",
            MagicMock(return_value=mock_proc),
        )

    def test_returns_true_when_child_exceeds_threshold(self, monkeypatch):
        self._patch_children([self._make_child(15.0)], monkeypatch)
        assert _has_active_child_processes(1234) is True

    def test_returns_false_when_all_children_below_threshold(self, monkeypatch):
        children = [self._make_child(0.0), self._make_child(5.0), self._make_child(9.9)]
        self._patch_children(children, monkeypatch)
        assert _has_active_child_processes(1234) is False

    def test_returns_false_when_no_children(self, monkeypatch):
        self._patch_children([], monkeypatch)
        assert _has_active_child_processes(1234) is False

    def test_returns_false_on_parent_nosuchprocess(self, monkeypatch):
        self._patch_children([], monkeypatch, parent_raises=psutil.NoSuchProcess)
        assert _has_active_child_processes(1234) is False

    def test_skips_dead_child_gracefully(self, monkeypatch):
        children = [self._make_child(psutil.NoSuchProcess), self._make_child(5.0)]
        self._patch_children(children, monkeypatch)
        assert _has_active_child_processes(1234) is False

    def test_skips_zombie_child_then_finds_active(self, monkeypatch):
        children = [self._make_child(psutil.ZombieProcess), self._make_child(20.0)]
        self._patch_children(children, monkeypatch)
        assert _has_active_child_processes(1234) is True

    def test_skips_access_denied_gracefully(self, monkeypatch):
        self._patch_children([self._make_child(psutil.AccessDenied)], monkeypatch)
        assert _has_active_child_processes(1234) is False


class TestSessionLogMonitorStaleSuppressionGate:
    """_session_log_monitor suppresses stale when process has an active port-443 connection."""

    @pytest.mark.anyio
    async def test_suppresses_stale_when_port_443_connection_active(self, tmp_path):
        """
        File stops growing. Monitor reaches stale_threshold. But process has
        an ESTABLISHED port-443 connection -> suppression fires, clock resets,
        monitor continues. On second check (connection dropped) -> stale fires.
        """
        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10

        call_count = {"n": 0}

        def side_effect(pid):
            call_count["n"] += 1
            return call_count["n"] == 1  # True on first call, False on second

        with patch(
            "autoskillit.execution._process_monitor._has_active_api_connection",
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
        assert result.status == "stale"
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
        assert result.status == "stale"

    @pytest.mark.anyio
    async def test_fires_stale_when_pid_is_none_regardless_of_tcp(self, tmp_path):
        """pid=None bypasses TCP check entirely — existing behavior preserved."""
        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10

        with patch(
            "autoskillit.execution._process_monitor._has_active_api_connection"
        ) as mock_tcp:
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
        assert result.status == "stale"
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
            "autoskillit.execution._process_monitor._has_active_api_connection",
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

    @pytest.mark.anyio
    async def test_suppresses_stale_when_child_cpu_active_no_api_connection(
        self, tmp_path, monkeypatch
    ):
        """Child CPU activity suppresses stale kill even when no port-443 connection."""
        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10  # wall time — compared against st_ctime in phase 1
        call_count: dict[str, int] = {"cpu": 0}

        def fake_api_conn(pid):
            return False  # No port-443 connection

        def fake_child_cpu(pid):
            call_count["cpu"] += 1
            return call_count["cpu"] == 1  # True first, False second

        monkeypatch.setattr(
            "autoskillit.execution._process_monitor._has_active_api_connection",
            fake_api_conn,
        )
        monkeypatch.setattr(
            "autoskillit.execution._process_monitor._has_active_child_processes",
            fake_child_cpu,
        )
        result = await _session_log_monitor(
            tmp_path, "DONE", stale_threshold=0.05, spawn_time=spawn_time, pid=9999,
            _phase1_poll=0.01, _phase2_poll=0.05,
        )
        assert result.status == ChannelBStatus.STALE
        assert call_count["cpu"] == 2  # suppressed once, then fired


class TestHeartbeatMarkerAwareness:
    """_heartbeat respects completion_marker when configured."""

    @pytest.mark.anyio
    async def test_heartbeat_does_not_fire_without_marker_when_configured(self, tmp_path):
        """Channel A must NOT fire on a result missing the marker when marker is configured."""
        import json

        stdout_path = tmp_path / "stdout.tmp"
        result_record = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Partial output",
                "session_id": "s1",
            },
            separators=(",", ":"),
        )
        stdout_path.write_text(result_record + "\n")

        with pytest.raises(TimeoutError):
            with anyio.fail_after(0.3):
                await _heartbeat(
                    stdout_path,
                    completion_marker="%%ORDER_UP%%",
                    _poll_interval=0.05,
                )

    @pytest.mark.anyio
    async def test_heartbeat_fires_with_marker_when_configured(self, tmp_path):
        """Channel A fires when the result contains the marker as a standalone line."""
        import json

        stdout_path = tmp_path / "stdout.tmp"
        result_record = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.\n%%ORDER_UP%%",
                "session_id": "s1",
            },
            separators=(",", ":"),
        )
        stdout_path.write_text(result_record + "\n")

        with anyio.fail_after(5.0):
            result = await _heartbeat(
                stdout_path,
                completion_marker="%%ORDER_UP%%",
                _poll_interval=0.05,
            )
        assert result == "completion"


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


class TestSessionLogMonitorSessionId:
    """_session_log_monitor returns SessionMonitorResult with session ID from filename."""

    @pytest.mark.anyio
    async def test_session_log_monitor_returns_session_id_from_filename(self, tmp_path):
        """_session_log_monitor returns the JSONL filename stem as session_id."""
        import json

        session_uuid = "d9adcc78-3098-4c3e-8720-ddcf3da35fff"
        jsonl_file = tmp_path / f"{session_uuid}.jsonl"
        jsonl_file.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": "done\n\nCOMPLETION_MARKER"},
                }
            )
            + "\n"
        )

        result = await _session_log_monitor(
            tmp_path,
            "COMPLETION_MARKER",
            stale_threshold=10.0,
            spawn_time=time.time() - 1,
        )

        assert result.status == "completion"
        assert result.session_id == session_uuid

    @pytest.mark.anyio
    async def test_session_log_monitor_returns_session_id_on_stale(self, tmp_path):
        """Even stale sessions capture the session ID from the discovered file."""
        import json

        session_uuid = "abc12345-dead-beef-cafe-123456789abc"
        jsonl_file = tmp_path / f"{session_uuid}.jsonl"
        jsonl_file.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": "no marker"},
                }
            )
            + "\n"
        )

        result = await _session_log_monitor(
            tmp_path,
            "MARKER",
            stale_threshold=0.1,
            spawn_time=time.time() - 1,
            _phase1_poll=0.01,
            _phase2_poll=0.05,
        )

        assert result.status == "stale"
        assert result.session_id == session_uuid

    @pytest.mark.anyio
    async def test_session_log_monitor_empty_session_id_when_no_file_found(self, tmp_path):
        """When no JSONL file is discovered (phase1 timeout), session_id is empty."""
        result = await _session_log_monitor(
            tmp_path,
            "MARKER",
            stale_threshold=10.0,
            spawn_time=time.time() - 1,
            _phase1_timeout=0.1,
        )

        assert result.status == "stale"
        assert result.session_id == ""

    @pytest.mark.anyio
    async def test_session_log_monitor_status_is_channel_b_status_enum(self, tmp_path):
        """SessionMonitorResult.status is a ChannelBStatus enum member."""
        import json

        from autoskillit.core.types import ChannelBStatus

        session_uuid = "enum-check-session"
        jsonl_file = tmp_path / f"{session_uuid}.jsonl"
        jsonl_file.write_text(
            json.dumps({"type": "assistant", "message": {"content": "done\n\nMARKER"}}) + "\n"
        )
        result = await _session_log_monitor(
            tmp_path,
            "MARKER",
            stale_threshold=10.0,
            spawn_time=time.time() - 1,
        )
        assert isinstance(result.status, ChannelBStatus)


class TestWatchSessionLogSessionId:
    """_watch_session_log deposits session ID from monitor into accumulator."""

    @pytest.mark.anyio
    async def test_watch_session_log_deposits_session_id(self, tmp_path):
        """_watch_session_log writes channel_b_session_id to the accumulator."""
        import json

        acc = RaceAccumulator()
        trigger = anyio.Event()
        channel_b_ready = anyio.Event()

        session_uuid = "test-uuid-from-channel-b"
        jsonl_file = tmp_path / f"{session_uuid}.jsonl"
        jsonl_file.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": "done MARKER"},
                }
            )
            + "\n"
        )

        async with anyio.create_task_group() as tg:
            tg.start_soon(
                _watch_session_log,
                tmp_path,
                "MARKER",
                10.0,
                time.time() - 1,
                frozenset({"assistant"}),
                12345,
                5.0,
                acc,
                trigger,
                channel_b_ready,
                0.1,
                0.1,
                30.0,
            )
            await channel_b_ready.wait()
            tg.cancel_scope.cancel()

        assert acc.channel_b_session_id == session_uuid


class TestSessionIdBasedSelection:
    """Phase 1 identity-based JSONL file selection."""

    @pytest.mark.anyio
    async def test_session_id_selects_correct_file_over_newer(self, tmp_path):
        """When expected_session_id is provided, selects matching file regardless of ctime."""
        import json

        session_a = "session-aaa-target"
        session_b = "session-bbb-newer"

        # Create session A first
        file_a = tmp_path / f"{session_a}.jsonl"
        file_a.write_text(
            json.dumps({"type": "assistant", "message": {"content": "done\n\nMARKER"}}) + "\n"
        )

        # Create session B slightly later (newer by ctime)
        await anyio.sleep(0.05)
        file_b = tmp_path / f"{session_b}.jsonl"
        file_b.write_text(
            json.dumps({"type": "assistant", "message": {"content": "done\n\nMARKER"}}) + "\n"
        )

        result = await _session_log_monitor(
            tmp_path,
            "MARKER",
            stale_threshold=10.0,
            spawn_time=time.time() - 2,
            expected_session_id=session_a,
        )
        assert result.status == "completion"
        assert result.session_id == session_a

    @pytest.mark.anyio
    async def test_session_id_falls_back_to_recency_when_no_match(self, tmp_path):
        """When expected_session_id doesn't match any file, falls back to newest."""
        import json

        session_b = "session-bbb-only"
        file_b = tmp_path / f"{session_b}.jsonl"
        file_b.write_text(
            json.dumps({"type": "assistant", "message": {"content": "done\n\nMARKER"}}) + "\n"
        )

        result = await _session_log_monitor(
            tmp_path,
            "MARKER",
            stale_threshold=10.0,
            spawn_time=time.time() - 2,
            expected_session_id="nonexistent-session-id",
        )
        assert result.status == "completion"
        assert result.session_id == session_b
