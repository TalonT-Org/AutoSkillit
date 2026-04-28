"""Unit tests for _heartbeat, _has_active_api_connection, _has_active_child_processes,
and orphaned tool result detection."""

from __future__ import annotations

import sys
import time
from unittest.mock import MagicMock, patch

import anyio
import psutil
import pytest

from autoskillit.core.types import ChannelBStatus, TerminationReason
from autoskillit.execution.process import (
    _has_active_api_connection,
    _has_active_child_processes,
    _heartbeat,
    _session_log_monitor,
    run_managed_async,
)
from tests.execution.conftest import WRITE_RESULT_THEN_HANG_SCRIPT

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


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
        partial_output_then_hang_script = (
            "import sys, time\n"
            'sys.stdout.write("partial output\\n")\n'
            "sys.stdout.flush()\n"
            "time.sleep(3600)\n"
        )
        script = tmp_path / "partial_hang.py"
        script.write_text(partial_output_then_hang_script)

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
    """Unit tests for _has_active_child_processes.

    The function caches psutil.Process objects so cpu_percent(interval=0)
    returns meaningful deltas on the second call.  First call primes the
    baseline (always returns False); second call with the same child PIDs
    uses the cached objects.
    """

    @pytest.fixture(autouse=True)
    def _clear_cache(self, monkeypatch):
        """Reset the module-level Process cache between tests."""
        from autoskillit.execution import _process_monitor

        monkeypatch.setattr(_process_monitor, "_child_process_cache", {})

    def _make_child(self, cpu: float | type[Exception], *, pid: int = 999) -> MagicMock:
        """Build a mock psutil child process."""
        child = MagicMock()
        child.pid = pid
        if isinstance(cpu, type) and issubclass(cpu, Exception):
            child.cpu_percent.side_effect = cpu(pid=pid)
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
        child = self._make_child(15.0, pid=100)
        self._patch_children([child], monkeypatch)
        # First call primes baseline; second call returns meaningful delta.
        _has_active_child_processes(1234)
        # Cache now holds the child object; second call uses cached.cpu_percent.
        from autoskillit.execution._process_monitor import _child_process_cache

        _child_process_cache[100] = child
        assert _has_active_child_processes(1234) is True

    def test_returns_false_when_all_children_below_threshold(self, monkeypatch):
        children = [
            self._make_child(0.0, pid=100),
            self._make_child(5.0, pid=101),
            self._make_child(9.9, pid=102),
        ]
        self._patch_children(children, monkeypatch)
        # Prime baseline
        _has_active_child_processes(1234)
        from autoskillit.execution._process_monitor import _child_process_cache

        for c in children:
            _child_process_cache[c.pid] = c
        assert _has_active_child_processes(1234) is False

    def test_returns_false_when_no_children(self, monkeypatch):
        self._patch_children([], monkeypatch)
        assert _has_active_child_processes(1234) is False

    def test_returns_false_on_parent_nosuchprocess(self, monkeypatch):
        self._patch_children([], monkeypatch, parent_raises=psutil.NoSuchProcess)
        assert _has_active_child_processes(1234) is False

    def test_skips_dead_child_gracefully(self, monkeypatch):
        children = [
            self._make_child(psutil.NoSuchProcess, pid=100),
            self._make_child(5.0, pid=101),
        ]
        self._patch_children(children, monkeypatch)
        _has_active_child_processes(1234)
        from autoskillit.execution._process_monitor import _child_process_cache

        for c in children:
            _child_process_cache[c.pid] = c
        assert _has_active_child_processes(1234) is False

    def test_skips_zombie_child_then_finds_active(self, monkeypatch):
        children = [
            self._make_child(psutil.ZombieProcess, pid=100),
            self._make_child(20.0, pid=101),
        ]
        self._patch_children(children, monkeypatch)
        # Prime baseline
        _has_active_child_processes(1234)
        from autoskillit.execution._process_monitor import _child_process_cache

        for c in children:
            _child_process_cache[c.pid] = c
        assert _has_active_child_processes(1234) is True

    def test_skips_access_denied_gracefully(self, monkeypatch):
        self._patch_children(
            [self._make_child(psutil.AccessDenied, pid=100)],
            monkeypatch,
        )
        _has_active_child_processes(1234)
        from autoskillit.execution._process_monitor import _child_process_cache

        _child_process_cache[100] = self._make_child(psutil.AccessDenied, pid=100)
        assert _has_active_child_processes(1234) is False


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


class TestOrphanedToolResultDetection:
    """Channel B detects orphaned tool results when last JSONL record is type=user."""

    @pytest.mark.anyio
    async def test_orphaned_tool_result_detected_when_last_record_is_user_type(self, tmp_path):
        """STALE result has orphaned_tool_result=True when last JSONL record is type=user."""
        import json

        log_dir = tmp_path / "session_logs"
        log_dir.mkdir()
        spawn_time = time.time() - 1

        session_file = log_dir / "sess_orphaned.jsonl"
        session_file.write_text(
            json.dumps({"type": "assistant", "message": {"content": "working..."}})
            + "\n"
            + json.dumps({"type": "user", "message": {"content": [{"type": "tool_result"}]}})
            + "\n"
        )

        result = await _session_log_monitor(
            log_dir,
            "%%AUTOSKILLIT_COMPLETE%%",
            stale_threshold=0.15,
            spawn_time=spawn_time,
            _phase1_poll=0.01,
            _phase2_poll=0.04,
        )

        assert result.status == ChannelBStatus.STALE
        assert result.orphaned_tool_result is True

    @pytest.mark.anyio
    async def test_no_false_positive_when_last_record_is_assistant(self, tmp_path):
        """STALE result has orphaned_tool_result=False when last JSONL record is type=assistant."""
        import json

        log_dir = tmp_path / "session_logs"
        log_dir.mkdir()
        spawn_time = time.time() - 1

        session_file = log_dir / "sess_assistant.jsonl"
        session_file.write_text(
            json.dumps({"type": "user", "message": {"content": "do something"}})
            + "\n"
            + json.dumps({"type": "assistant", "message": {"content": "thinking..."}})
            + "\n"
        )

        result = await _session_log_monitor(
            log_dir,
            "%%AUTOSKILLIT_COMPLETE%%",
            stale_threshold=0.15,
            spawn_time=spawn_time,
            _phase1_poll=0.01,
            _phase2_poll=0.04,
        )

        assert result.status == ChannelBStatus.STALE
        assert result.orphaned_tool_result is False

    @pytest.mark.anyio
    async def test_orphaned_false_on_completion(self, tmp_path):
        """COMPLETION result always has orphaned_tool_result=False."""
        import json

        log_dir = tmp_path / "session_logs"
        log_dir.mkdir()
        spawn_time = time.time() - 1

        session_file = log_dir / "sess_complete.jsonl"
        session_file.write_text(
            json.dumps({"type": "user", "message": {"content": "do something"}})
            + "\n"
            + json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": "done\n%%AUTOSKILLIT_COMPLETE%%"},
                }
            )
            + "\n"
        )

        result = await _session_log_monitor(
            log_dir,
            "%%AUTOSKILLIT_COMPLETE%%",
            stale_threshold=5.0,
            spawn_time=spawn_time,
            _phase1_poll=0.01,
            _phase2_poll=0.04,
        )

        assert result.status == ChannelBStatus.COMPLETION
        assert result.orphaned_tool_result is False
