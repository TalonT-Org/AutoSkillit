"""Lease evidence must reach each process liveness consumer before early exits."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import anyio
import pytest
import structlog.testing

import autoskillit.core.plugins._operation_lease as lease_module
import autoskillit.execution.process._process_monitor as process_monitor
import autoskillit.execution.process._race_watchers as race_watchers
from autoskillit.core import (
    ChannelBStatus,
    InFlightOperations,
    operation_lease,
)
from autoskillit.execution.process._process_monitor import (
    SessionMonitorResult,
    _active_liveness_signals,
    _discover_session_log,
    _session_log_monitor,
)
from autoskillit.execution.process._process_race import RaceAccumulator
from autoskillit.execution.process._race_watchers import (
    _watch_child_activity,
    _watch_stdout_idle,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


class _StopPolling(Exception):
    pass


class _LeaseClock:
    def __init__(
        self,
        epoch: float,
        *,
        channel: Path | None = None,
        steps: list[float] | None = None,
        stop_after: int | None = None,
        on_tick=None,
    ) -> None:
        self.epoch = epoch
        self.elapsed = 0.0
        self.channel = channel
        self.steps = list(steps or [])
        self.stop_after = stop_after
        self.on_tick = on_tick
        self.ticks = 0

    def current_time(self) -> float:
        return self.elapsed

    async def sleep(self, interval: float) -> None:
        self.ticks += 1
        delta = self.steps.pop(0) if self.steps else interval
        self.elapsed += delta
        self.epoch += delta
        if self.channel is not None:
            for path in self.channel.glob("*.lease.json"):
                os.utime(path, (self.epoch, self.epoch))
        if self.on_tick is not None:
            self.on_tick(self)
        if self.stop_after is not None and self.ticks >= self.stop_after:
            raise _StopPolling


async def _blocked_heartbeat(_handle, _interval: float) -> None:
    await asyncio.Event().wait()


def _install_clock(
    monkeypatch: pytest.MonkeyPatch,
    clock: _LeaseClock,
    *modules: object,
) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: clock.elapsed)
    monkeypatch.setattr(time, "time", lambda: clock.epoch)
    fake_anyio = SimpleNamespace(sleep=clock.sleep, current_time=clock.current_time)
    for module in modules:
        monkeypatch.setattr(module, "anyio", fake_anyio)


@pytest.mark.anyio
async def test_stdout_lease_tolerance_refreshes_each_poll_until_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lease_module, "_heartbeat", _blocked_heartbeat)
    registry = InFlightOperations()
    async with operation_lease(
        tmp_path,
        operation="run_skill",
        not_after_epoch=time.time() + 7200,
        registry=registry,
        heartbeat_interval=10000,
    ) as handle:
        assert handle.path is not None
        clock = _LeaseClock(
            handle.record.started_at_epoch,
            channel=tmp_path,
            steps=[1, 7198, 1, 1799],
        )
        _install_clock(monkeypatch, clock, race_watchers)
        stdout = MagicMock()
        stdout.stat.return_value = SimpleNamespace(st_size=1)
        acc = RaceAccumulator()
        trigger = anyio.Event()

        with structlog.testing.capture_logs() as logs:
            await _watch_stdout_idle(
                stdout,
                1800,
                acc,
                trigger,
                300,
                max_suppression_seconds=1800,
                operation_lease_dir=tmp_path,
            )

    assert clock.epoch < handle.record.not_after_epoch + 1800 + 300
    assert clock.epoch >= handle.record.not_after_epoch
    assert acc.idle_stall is True
    assert any(entry.get("event") == "stdout_idle_deferred_to_operation" for entry in logs)


@pytest.mark.anyio
async def test_stdout_growth_then_stall_reports_actual_silence_and_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _LeaseClock(time.time())
    _install_clock(monkeypatch, clock, race_watchers)
    stdout = MagicMock()
    stdout.stat.side_effect = [
        SimpleNamespace(st_size=10),
        SimpleNamespace(st_size=20),
        SimpleNamespace(st_size=20),
        SimpleNamespace(st_size=20),
    ]
    acc = RaceAccumulator()
    trigger = anyio.Event()

    with structlog.testing.capture_logs() as logs:
        await _watch_stdout_idle(stdout, 2.0, acc, trigger, 1.0)

    fire = next(entry for entry in logs if entry.get("event") == "stdout_idle_stall_firing")
    assert acc.idle_stall is True
    assert fire["idle_threshold"] == 2.0
    assert fire["silence_seconds"] >= 2.0


@pytest.mark.anyio
async def test_stdout_lease_precedes_pending_task_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lease_module, "_heartbeat", _blocked_heartbeat)
    async with operation_lease(
        tmp_path,
        operation="run_skill",
        not_after_epoch=time.time() + 100,
        registry=InFlightOperations(),
        heartbeat_interval=10000,
    ):
        clock = _LeaseClock(time.time(), channel=tmp_path, stop_after=6)
        _install_clock(monkeypatch, clock, race_watchers)
        stdout = MagicMock()
        stdout.stat.return_value = SimpleNamespace(st_size=1)
        acc = RaceAccumulator()
        trigger = anyio.Event()

        with pytest.raises(_StopPolling):
            await _watch_stdout_idle(
                stdout,
                0.5,
                acc,
                trigger,
                1.0,
                max_suppression_seconds=0.1,
                has_pending_tasks=lambda: True,
                operation_lease_dir=tmp_path,
            )

        assert acc.idle_stall is False
        assert stdout.stat.call_count == 5


@pytest.mark.anyio
async def test_stdout_file_loss_during_lease_keeps_last_evidence_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lease_module, "_heartbeat", _blocked_heartbeat)
    async with operation_lease(
        tmp_path,
        operation="run_skill",
        not_after_epoch=time.time() + 6,
        registry=InFlightOperations(),
        heartbeat_interval=10000,
    ) as handle:
        clock = _LeaseClock(handle.record.started_at_epoch, channel=tmp_path)
        _install_clock(monkeypatch, clock, race_watchers)
        stdout = MagicMock()
        stdout.stat.side_effect = [
            SimpleNamespace(st_size=1),
            OSError("missing"),
            OSError("missing"),
            OSError("missing"),
            OSError("missing"),
            SimpleNamespace(st_size=1),
            SimpleNamespace(st_size=1),
            SimpleNamespace(st_size=1),
            SimpleNamespace(st_size=1),
        ]
        acc = RaceAccumulator()
        trigger = anyio.Event()

        await _watch_stdout_idle(
            stdout,
            3.0,
            acc,
            trigger,
            1.0,
            operation_lease_dir=tmp_path,
        )

    assert acc.idle_stall is True
    assert clock.elapsed == 8


@pytest.mark.anyio
async def test_session_log_discovery_waits_for_lease_then_uses_timeout_from_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lease_module, "_heartbeat", _blocked_heartbeat)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    async with operation_lease(
        tmp_path,
        operation="run_skill",
        not_after_epoch=time.time() + 100,
        registry=InFlightOperations(),
        heartbeat_interval=10000,
    ) as handle:
        assert handle.path is not None

        def remove_after_three_ticks(clock: _LeaseClock) -> None:
            if clock.ticks == 3:
                handle.path.unlink()

        clock = _LeaseClock(
            handle.record.started_at_epoch,
            channel=tmp_path,
            on_tick=remove_after_three_ticks,
        )
        _install_clock(monkeypatch, clock, process_monitor)
        result = await _discover_session_log(
            log_dir,
            spawn_time=clock.epoch - 1,
            phase1_poll=1,
            phase1_timeout=2,
            expected_session_id=None,
            resume_cursor=None,
            operation_lease_dir=tmp_path,
        )

    assert isinstance(result, SessionMonitorResult)
    assert result.status is ChannelBStatus.STALE
    assert clock.elapsed >= 4


@pytest.mark.anyio
async def test_session_log_lease_defers_api_suppression_but_existing_cap_still_fires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lease_module, "_heartbeat", _blocked_heartbeat)
    monkeypatch.setattr(process_monitor, "_has_active_api_connection", lambda _pid: True)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    session_file = log_dir / "child.jsonl"
    session_file.write_text(json.dumps({"type": "assistant", "message": "working"}) + "\n")
    async with operation_lease(
        tmp_path,
        operation="run_skill",
        not_after_epoch=time.time() + 100,
        registry=InFlightOperations(),
        heartbeat_interval=10000,
    ) as handle:
        assert handle.path is not None

        def remove_after_three_ticks(clock: _LeaseClock) -> None:
            if clock.ticks == 3:
                handle.path.unlink()

        clock = _LeaseClock(
            handle.record.started_at_epoch,
            channel=tmp_path,
            on_tick=remove_after_three_ticks,
        )
        _install_clock(monkeypatch, clock, process_monitor)
        with structlog.testing.capture_logs() as logs:
            result = await _session_log_monitor(
                log_dir,
                "%%DONE%%",
                stale_threshold=1,
                spawn_time=clock.epoch - 1,
                pid=123,
                _phase1_poll=1,
                _phase2_poll=1,
                max_suppression_seconds=2,
                operation_lease_dir=tmp_path,
            )

    assert result.status is ChannelBStatus.STALE
    operation_events = [
        index
        for index, entry in enumerate(logs)
        if entry.get("event") == "stale_deferred_to_operation"
    ]
    api_events = [
        index
        for index, entry in enumerate(logs)
        if "ESTABLISHED port-443 connection" in str(entry.get("event", ""))
    ]
    assert operation_events
    assert api_events and min(api_events) > min(operation_events)


@pytest.mark.anyio
async def test_session_log_file_loss_during_lease_keeps_last_evidence_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lease_module, "_heartbeat", _blocked_heartbeat)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    session_file = log_dir / "child.jsonl"
    session_file.write_text(json.dumps({"type": "assistant", "message": "working"}) + "\n")
    selected: list[Path] = []
    stat_calls = 0
    original_stat = Path.stat

    def temporary_stat_failure(path: Path, *args, **kwargs):
        nonlocal stat_calls
        if selected and path == selected[0]:
            stat_calls += 1
            if 3 <= stat_calls <= 6:
                raise OSError("temporary session log loss")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", temporary_stat_failure)
    async with operation_lease(
        tmp_path,
        operation="run_skill",
        not_after_epoch=time.time() + 6,
        registry=InFlightOperations(),
        heartbeat_interval=10000,
    ) as handle:
        clock = _LeaseClock(handle.record.started_at_epoch, channel=tmp_path)
        _install_clock(monkeypatch, clock, process_monitor)
        result = await _session_log_monitor(
            log_dir,
            "%%DONE%%",
            stale_threshold=3,
            spawn_time=clock.epoch - 1,
            _phase1_poll=1,
            _phase2_poll=1,
            operation_lease_dir=tmp_path,
            on_session_file_selected=lambda path, _cursor: selected.append(path),
        )

    assert result.status is ChannelBStatus.STALE
    assert clock.elapsed >= 8


@pytest.mark.anyio
async def test_session_log_completion_is_processed_while_lease_is_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lease_module, "_heartbeat", _blocked_heartbeat)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    session_file = log_dir / "child.jsonl"
    session_file.write_text(json.dumps({"type": "assistant", "message": "working"}) + "\n")
    async with operation_lease(
        tmp_path,
        operation="run_skill",
        not_after_epoch=time.time() + 100,
        registry=InFlightOperations(),
        heartbeat_interval=10000,
    ):

        def append_completion(clock: _LeaseClock) -> None:
            if clock.ticks == 2:
                with session_file.open("a") as stream:
                    stream.write(
                        json.dumps({"type": "assistant", "message": {"content": "%%DONE%%"}})
                        + "\n"
                    )

        clock = _LeaseClock(
            time.time(),
            channel=tmp_path,
            on_tick=append_completion,
        )
        _install_clock(monkeypatch, clock, process_monitor)
        result = await _session_log_monitor(
            log_dir,
            "%%DONE%%",
            stale_threshold=1,
            spawn_time=clock.epoch - 1,
            _phase1_poll=1,
            _phase2_poll=1,
            operation_lease_dir=tmp_path,
        )

    assert result.status is ChannelBStatus.COMPLETION


@pytest.mark.anyio
async def test_deadline_extension_uses_lease_but_keeps_absolute_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lease_module, "_heartbeat", _blocked_heartbeat)
    async with operation_lease(
        tmp_path,
        operation="run_skill",
        not_after_epoch=time.time() + 100,
        registry=InFlightOperations(),
        heartbeat_interval=10000,
    ):
        clock = _LeaseClock(time.time(), channel=tmp_path, stop_after=4)
        _install_clock(monkeypatch, clock, race_watchers)
        scope = SimpleNamespace(deadline=5.0)
        trigger = anyio.Event()
        with pytest.raises(_StopPolling):
            await _watch_child_activity(
                123,
                [scope],
                max_extension_seconds=10,
                trigger=trigger,
                _poll_interval=4,
                operation_lease_dir=tmp_path,
                has_pending_tasks=lambda: False,
            )

        assert 5.0 < scope.deadline <= 15.0


@pytest.mark.anyio
async def test_drain_liveness_signals_include_active_operation_lease(tmp_path: Path) -> None:
    async with operation_lease(
        tmp_path,
        operation="run_skill",
        not_after_epoch=time.time() + 60,
        registry=InFlightOperations(),
    ):
        assert "operation_lease" in _active_liveness_signals(
            None,
            None,
            None,
            operation_lease_dir=tmp_path,
        )
