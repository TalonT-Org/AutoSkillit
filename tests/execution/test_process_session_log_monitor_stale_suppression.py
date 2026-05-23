"""TCP/CPU stale suppression gate and bounded suppression tests for _session_log_monitor."""

from __future__ import annotations

import time
from unittest.mock import patch

import anyio
import pytest

from autoskillit.core.types import ChannelBStatus
from autoskillit.execution.process import _session_log_monitor

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


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
            "autoskillit.execution.process._process_monitor._has_active_api_connection",
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
        assert result.status == ChannelBStatus.STALE
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
        assert result.status == ChannelBStatus.STALE

    @pytest.mark.anyio
    async def test_fires_stale_when_pid_is_none_regardless_of_tcp(self, tmp_path):
        """pid=None bypasses TCP check entirely — existing behavior preserved."""
        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10

        with patch(
            "autoskillit.execution.process._process_monitor._has_active_api_connection"
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
        assert result.status == ChannelBStatus.STALE
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
            "autoskillit.execution.process._process_monitor._has_active_api_connection",
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
        _io = capsys.readouterr()
        captured = _io.out + _io.err
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
            "autoskillit.execution.process._process_monitor._has_active_api_connection",
            fake_api_conn,
        )
        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_child_processes",
            fake_child_cpu,
        )
        with anyio.fail_after(5.0):
            result = await _session_log_monitor(
                tmp_path,
                "DONE",
                stale_threshold=0.05,
                spawn_time=spawn_time,
                pid=9999,
                _phase1_poll=0.01,
                _phase2_poll=0.05,
            )
        assert result.status == ChannelBStatus.STALE
        assert call_count["cpu"] == 2  # suppressed once, then fired


class TestStaleSuppressionBounded:
    """Bounded suppression: max_suppression_seconds caps stale deferral."""

    @pytest.mark.anyio
    async def test_stale_suppression_bounded_by_max_duration(self, tmp_path, monkeypatch):
        """Stale fires after max_suppression_seconds despite ESTABLISHED connection."""
        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10

        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_api_connection",
            lambda pid: True,
        )

        with anyio.fail_after(8.0):
            result = await _session_log_monitor(
                tmp_path,
                "DONE",
                stale_threshold=0.05,
                spawn_time=spawn_time,
                pid=9999,
                _phase1_poll=0.01,
                _phase2_poll=0.05,
                max_suppression_seconds=1.0,
            )
        assert result.status == ChannelBStatus.STALE

    @pytest.mark.anyio
    async def test_stale_suppression_resets_on_genuine_activity(self, tmp_path, monkeypatch):
        """Suppression counter resets when JSONL file grows."""
        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10

        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_api_connection",
            lambda pid: True,
        )

        async def write_activity() -> None:
            import json as _json

            for i in range(6):
                await anyio.sleep(0.5)
                with session_file.open("a") as f:
                    record = {"type": "assistant", "message": {"content": f"msg-{i}"}}
                    f.write(_json.dumps(record) + "\n")

        with anyio.fail_after(10.0):
            async with anyio.create_task_group() as tg:
                tg.start_soon(write_activity)
                result = await _session_log_monitor(
                    tmp_path,
                    "DONE",
                    stale_threshold=0.05,
                    spawn_time=spawn_time,
                    pid=9999,
                    _phase1_poll=0.01,
                    _phase2_poll=0.05,
                    max_suppression_seconds=2.0,
                )
                tg.cancel_scope.cancel()

        assert result.status == ChannelBStatus.STALE

    @pytest.mark.anyio
    async def test_stale_suppression_logs_warning_on_bounded_kill(
        self, tmp_path, monkeypatch, capsys
    ):
        """Warning log emitted when bounded suppression fires."""
        import structlog.testing

        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10

        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_api_connection",
            lambda pid: True,
        )

        with anyio.fail_after(8.0):
            with structlog.testing.capture_logs() as logs:
                result = await _session_log_monitor(
                    tmp_path,
                    "DONE",
                    stale_threshold=0.05,
                    spawn_time=spawn_time,
                    pid=9999,
                    _phase1_poll=0.01,
                    _phase2_poll=0.05,
                    max_suppression_seconds=1.0,
                )
        assert result.status == ChannelBStatus.STALE
        _io = capsys.readouterr()
        captured = _io.out + _io.err
        bounded_in_logs = any("Suppression bounded" in str(log.get("event", "")) for log in logs)
        bounded_in_stdout = "Suppression bounded" in captured
        assert bounded_in_logs or bounded_in_stdout

    @pytest.mark.anyio
    async def test_shared_suppression_timer_prevents_chaining(self, tmp_path, monkeypatch):
        """Switching suppression gates (API → marker) does not chain independent timers."""
        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10

        call_count = {"n": 0}

        def _api_conn(pid):
            call_count["n"] += 1
            return call_count["n"] <= 2

        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_api_connection",
            _api_conn,
        )
        monkeypatch.setattr(
            "autoskillit.execution.process._process_monitor._has_active_child_processes",
            lambda pid: False,
        )

        (tmp_path / "dispatch-in-progress-some-uuid.marker").write_text("{}")

        suppression_start = time.monotonic()
        with anyio.fail_after(8.0):
            result = await _session_log_monitor(
                tmp_path,
                "DONE",
                stale_threshold=0.05,
                spawn_time=spawn_time,
                pid=9999,
                _phase1_poll=0.01,
                _phase2_poll=0.05,
                max_suppression_seconds=0.3,
                marker_dir=tmp_path,
                caller_session_id=None,
            )
        elapsed = time.monotonic() - suppression_start

        assert result.status == ChannelBStatus.STALE
        assert elapsed <= 0.45, f"elapsed {elapsed:.2f}s exceeds 0.45s — timer may have chained"
        assert elapsed >= 0.15, (
            f"elapsed {elapsed:.2f}s below 0.15s — suppression may not have fired"
        )
