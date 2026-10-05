"""Lease and child-activity stale suppression tests for _session_log_monitor."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import anyio
import psutil
import pytest

import autoskillit.execution.process._process_monitor as _patch_process__process_monitor
from autoskillit.core.types import ChannelBStatus
from autoskillit.execution.process import _session_log_monitor

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


class TestSessionLogMonitorStaleSuppressionGate:
    """Staleness is deferred only by operation leases or active child work."""

    @pytest.mark.anyio
    async def test_network_connection_without_lease_does_not_suppress_stale(
        self, tmp_path, monkeypatch
    ):
        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10
        channel = tmp_path / "leases"
        channel.mkdir()

        class ConnectedProcess:
            def __init__(self, pid: int) -> None:
                self.pid = pid

            def children(self, recursive: bool = False) -> list[object]:
                return []

            def connections(self, kind: str | None = None) -> list[SimpleNamespace]:
                return [
                    SimpleNamespace(
                        status=psutil.CONN_ESTABLISHED,
                        raddr=SimpleNamespace(port=443),
                    )
                ]

            net_connections = connections

        monkeypatch.setattr(_patch_process__process_monitor.psutil, "Process", ConnectedProcess)
        monkeypatch.setattr(
            _patch_process__process_monitor, "_has_active_child_processes", lambda _pid: False
        )
        started = time.monotonic()
        with anyio.fail_after(2.0):
            result = await _session_log_monitor(
                tmp_path,
                "DONE",
                stale_threshold=0.05,
                spawn_time=spawn_time,
                pid=99999,
                _phase1_poll=0.01,
                _phase2_poll=0.05,
                max_suppression_seconds=30.0,
                operation_lease_dir=channel,
            )
        elapsed = time.monotonic() - started
        assert result.status == ChannelBStatus.STALE
        assert elapsed < 0.5

    @pytest.mark.anyio
    async def test_fires_stale_without_an_operation_lease(self, tmp_path):
        """A silent log with no process evidence fires stale."""
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
    async def test_suppresses_stale_when_child_cpu_active(self, tmp_path, monkeypatch):
        """Child CPU activity continues to suppress stale kill."""
        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10  # wall time — compared against st_ctime in phase 1
        call_count: dict[str, int] = {"cpu": 0}

        def fake_child_cpu(pid):
            call_count["cpu"] += 1
            return call_count["cpu"] == 1  # True first, False second

        monkeypatch.setattr(
            _patch_process__process_monitor,
            "_has_active_child_processes",
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

    @pytest.mark.anyio
    async def test_real_operation_lease_defers_stale_until_lease_ends(self, tmp_path):
        import structlog.testing

        from autoskillit.core import InFlightOperations, operation_lease

        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        channel = tmp_path / "leases"
        channel.mkdir()
        ticks = {"count": 0}

        async with operation_lease(
            channel,
            operation="run_skill",
            not_after_epoch=time.time() + 60,
            registry=InFlightOperations(),
            heartbeat_interval=10000,
        ) as handle:

            def end_lease_after_three_polls() -> None:
                ticks["count"] += 1
                if ticks["count"] == 3 and handle.path is not None:
                    handle.path.unlink()

            with structlog.testing.capture_logs() as logs:
                with anyio.fail_after(2.0):
                    result = await _session_log_monitor(
                        tmp_path,
                        "DONE",
                        stale_threshold=0.05,
                        spawn_time=time.time() - 10,
                        _phase1_poll=0.01,
                        _phase2_poll=0.01,
                        _on_poll=end_lease_after_three_polls,
                        max_suppression_seconds=30.0,
                        operation_lease_dir=channel,
                    )

        assert result.status is ChannelBStatus.STALE
        assert any(log.get("event") == "stale_deferred_to_operation" for log in logs)


class TestStaleSuppressionBounded:
    """Bounded suppression: max_suppression_seconds caps stale deferral."""

    @pytest.mark.anyio
    async def test_stale_suppression_bounded_by_max_duration(self, tmp_path, monkeypatch):
        """The existing cap still bounds deferral for active child processes."""
        session_file = tmp_path / "session.jsonl"
        session_file.write_text("")
        spawn_time = time.time() - 10

        monkeypatch.setattr(
            _patch_process__process_monitor, "_has_active_child_processes", lambda _pid: True
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
            _patch_process__process_monitor, "_has_active_child_processes", lambda _pid: True
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


class TestExecutionMarkerLifecycle:
    @pytest.mark.anyio
    async def test_execution_marker_lifecycle(self, tmp_path: Path) -> None:
        from autoskillit.core._execution_marker import execution_marker

        marker_dir = tmp_path / "markers"
        marker_dir.mkdir()

        async with execution_marker(marker_dir, "session-123", "run-skill") as path:
            assert path is not None
            matches = list(marker_dir.glob("run-skill-in-progress-session-123-*.marker"))
            assert len(matches) == 1

        remaining = list(marker_dir.glob("*-in-progress-*.marker"))
        assert remaining == [], f"marker not cleaned up: {remaining}"
