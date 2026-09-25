"""Focused tests for the interactive cook lifetime policy."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import structlog

from autoskillit.config import ProcessTetherConfig
from autoskillit.core import TerminationReason
from autoskillit.execution.process._process_tether import TetherRecord, write_tether
from tests.cli._cook_launch_helpers import lifetime_policy

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


class _Clock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _lifetime_module():
    from autoskillit.cli.session import _session_process

    return _session_process


def _policy(
    *, soft: float = 3.0, extension: float = 20.0, systemd_scope_enabled: bool = False
) -> ProcessTetherConfig:
    return lifetime_policy(
        cook_ceiling_seconds=soft,
        cook_max_extension_seconds=extension,
        systemd_scope_enabled=systemd_scope_enabled,
    )


def _start_lifetime(
    policy: ProcessTetherConfig,
    *,
    clock: _Clock | None = None,
    wall: _Clock | None = None,
    activity_probe: Callable[[int, int | None], frozenset[str]] | None = None,
    tether_path: Path | None = None,
    notice_path: Path | None = None,
):
    lifetime = _lifetime_module().InteractiveLifetime(
        policy,
        clock=clock or _Clock(),
        wall=wall or _Clock(10_000.0),
        activity_probe=activity_probe or (lambda _pid, _fd: frozenset()),
    )
    lifetime.start(
        pid=os.getpid(), tether_path=tether_path, terminal_fd=None, notice_path=notice_path
    )
    return lifetime


def _write_test_tether(tmp_path: Path, not_after: float) -> Path:
    record = TetherRecord(
        child_pid=os.getpid(),
        child_pgid=os.getpgrp(),
        child_starttime_ticks=1,
        boot_id="test-boot",
        spawner_pid=os.getpid(),
        spawner_starttime_ticks=1,
        spawned_at_ns=1,
        not_after=not_after,
        origin="cook",
    )
    return write_tether(record, tmp_path / "tethers")


def _tether_data(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _events(logs: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [record for record in logs if record.get("event") == event]


@pytest.mark.parametrize(
    ("soft", "extension"),
    [
        (float("nan"), 1.0),
        (float("inf"), 1.0),
        (0.0, 1.0),
        (-1.0, 1.0),
        (1.0, float("nan")),
        (1.0, float("inf")),
        (1.0, -1.0),
    ],
)
def test_invalid_policy_fails_closed(soft: float, extension: float) -> None:
    with pytest.raises(ValueError):
        _lifetime_module().InteractiveLifetime(_policy(soft=soft, extension=extension))


def test_enforcer_values_derive_from_policy() -> None:
    lifetime_module = _lifetime_module()
    soft = 123.5
    extension = 456.75
    lifetime = _lifetime_module().InteractiveLifetime(
        _policy(soft=soft, extension=extension, systemd_scope_enabled=True)
    )
    hard_cap = soft + extension

    assert lifetime.scope_runtime_max_seconds == (
        hard_cap + lifetime_module.OWNER_PRECEDENCE_MARGIN_SECONDS
    )
    assert lifetime.tether_ceiling_seconds == min(
        lifetime_module.TETHER_LEASE_SECONDS,
        hard_cap + lifetime_module.OWNER_PRECEDENCE_MARGIN_SECONDS,
    )
    assert lifetime.systemd_scope_enabled is True


def test_active_session_extends_past_soft_ceiling() -> None:
    clock = _Clock()
    lifetime = _start_lifetime(
        _policy(soft=2.0, extension=8.0),
        clock=clock,
        activity_probe=lambda _pid, _fd: frozenset({"terminal_io"}),
    )
    clock.advance(2.1)

    with structlog.testing.capture_logs() as logs:
        assert lifetime.poll() is None

    extended = _events(logs, "cook_lifetime_extended")
    assert len(extended) == 1
    assert extended[0]["signals"] == ["terminal_io"]


def test_idle_session_ends_at_soft_ceiling_as_idle_stall(monkeypatch: pytest.MonkeyPatch) -> None:
    lifetime_module = _lifetime_module()
    monkeypatch.setattr(lifetime_module, "_IDLE_WINDOW_SECONDS", 5.0)
    monkeypatch.setattr(lifetime_module, "_LIVENESS_PROBE_INTERVAL_SECONDS", 1.0)
    clock = _Clock()
    calls = 0

    def probe(_pid: int, _fd: int | None) -> frozenset[str]:
        nonlocal calls
        calls += 1
        return frozenset()

    lifetime = _start_lifetime(
        _policy(soft=6.0, extension=10.0), clock=clock, activity_probe=probe
    )
    clock.advance(6.1)
    with structlog.testing.capture_logs() as logs:
        assert lifetime.poll() is None
        assert calls == 1
        clock.advance(1.0)
        assert lifetime.poll() is TerminationReason.IDLE_STALL

    expired = _events(logs, "cook_lifetime_expired")
    assert len(expired) == 1
    assert expired[0]["reason"] == "idle_stall"
    assert expired[0]["signals"] == []


def test_first_probe_after_soft_never_ends_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    lifetime_module = _lifetime_module()
    monkeypatch.setattr(lifetime_module, "_IDLE_WINDOW_SECONDS", 1.0)
    clock = _Clock()
    lifetime = _start_lifetime(
        _policy(soft=2.0, extension=10.0),
        clock=clock,
        activity_probe=lambda _pid, _fd: frozenset(),
    )
    clock.advance(3.0)

    assert lifetime.poll() is None


def test_terminal_io_does_not_restart_idle_window_but_other_activity_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifetime_module = _lifetime_module()
    monkeypatch.setattr(lifetime_module, "_IDLE_WINDOW_SECONDS", 5.0)
    monkeypatch.setattr(lifetime_module, "_LIVENESS_PROBE_INTERVAL_SECONDS", 1.0)

    terminal_signals = iter([frozenset({"terminal_io"}), frozenset({"terminal_io"}), frozenset()])
    terminal_clock = _Clock()
    terminal_lifetime = _start_lifetime(
        _policy(soft=5.0, extension=20.0),
        clock=terminal_clock,
        activity_probe=lambda _pid, _fd: next(terminal_signals, frozenset()),
    )
    terminal_clock.advance(5.1)
    assert terminal_lifetime.poll() is None
    terminal_clock.advance(1.0)
    assert terminal_lifetime.poll() is None
    terminal_clock.advance(1.0)
    assert terminal_lifetime.poll() is TerminationReason.IDLE_STALL

    active_signals = iter(
        [
            frozenset({"terminal_io"}),
            frozenset({"api_connection"}),
            frozenset(),
            frozenset(),
        ]
    )
    active_clock = _Clock()
    active_lifetime = _start_lifetime(
        _policy(soft=5.0, extension=20.0),
        clock=active_clock,
        activity_probe=lambda _pid, _fd: next(active_signals, frozenset()),
    )
    active_clock.advance(5.1)
    assert active_lifetime.poll() is None
    active_clock.advance(1.0)
    assert active_lifetime.poll() is None
    active_clock.advance(4.0)
    assert active_lifetime.poll() is None
    active_clock.advance(1.0)
    assert active_lifetime.poll() is TerminationReason.IDLE_STALL


def test_active_session_ends_at_hard_cap_as_timed_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_lifetime_module(), "_LIVENESS_PROBE_INTERVAL_SECONDS", 1.0)
    clock = _Clock()
    lifetime = _start_lifetime(
        _policy(soft=2.0, extension=3.0),
        clock=clock,
        activity_probe=lambda _pid, _fd: frozenset({"api_connection"}),
    )
    clock.advance(2.1)
    assert lifetime.poll() is None
    clock.advance(2.8)
    assert lifetime.poll() is None
    clock.advance(0.1)

    assert lifetime.poll() is TerminationReason.TIMED_OUT


def test_zero_extension_is_hard_stop_at_soft_ceiling() -> None:
    clock = _Clock()
    lifetime = _start_lifetime(
        _policy(soft=2.0, extension=0.0),
        clock=clock,
        activity_probe=lambda _pid, _fd: frozenset({"api_connection"}),
    )
    clock.advance(1.9)
    assert lifetime.poll() is None
    clock.advance(0.1)

    assert lifetime.poll() is TerminationReason.TIMED_OUT


def test_probe_rate_limited_and_never_called_before_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    lifetime_module = _lifetime_module()
    monkeypatch.setattr(lifetime_module, "_LIVENESS_PROBE_INTERVAL_SECONDS", 3.0)
    clock = _Clock()
    calls = 0

    def probe(_pid: int, _fd: int | None) -> frozenset[str]:
        nonlocal calls
        calls += 1
        return frozenset({"api_connection"})

    lifetime = _start_lifetime(
        _policy(soft=2.0, extension=20.0), clock=clock, activity_probe=probe
    )
    for _ in range(20):
        assert lifetime.poll() is None
    assert calls == 0

    clock.advance(2.1)
    assert lifetime.poll() is None
    assert lifetime.poll() is None
    assert calls == 1
    clock.advance(2.9)
    assert lifetime.poll() is None
    assert calls == 1
    clock.advance(0.1)
    assert lifetime.poll() is None
    assert calls == 2


def test_decision_is_sticky_and_logged_once() -> None:
    clock = _Clock()
    lifetime = _start_lifetime(_policy(soft=2.0, extension=0.0), clock=clock)
    clock.advance(2.0)

    with structlog.testing.capture_logs() as logs:
        first = lifetime.poll()
        clock.advance(10.0)
        second = lifetime.poll()
        third = lifetime.poll()

    assert first is TerminationReason.TIMED_OUT
    assert second is first
    assert third is first
    assert len(_events(logs, "cook_lifetime_expired")) == 1


@pytest.mark.skipif(sys.platform != "linux", reason="write_tether persists records on Linux only")
def test_lease_renewed_ahead_of_expiry(tmp_path: Path) -> None:
    lifetime_module = _lifetime_module()
    clock = _Clock()
    wall = _Clock(50_000.0)
    lease = lifetime_module.TETHER_LEASE_SECONDS
    renew = lifetime_module.TETHER_LEASE_RENEW_SECONDS
    path = _write_test_tether(tmp_path, wall.value + lease)
    before = _tether_data(path)
    lifetime = _start_lifetime(
        _policy(soft=20_000.0, extension=20_000.0),
        clock=clock,
        wall=wall,
        activity_probe=lambda _pid, _fd: frozenset({"api_connection"}),
        tether_path=path,
    )

    wall.advance(renew - 1.0)
    assert lifetime.poll() is None
    assert pytest.approx(lease - (renew - 1.0)) == (_tether_data(path)["not_after"] - wall.value)
    wall.advance(1.0)
    assert lifetime.poll() is None
    after = _tether_data(path)

    assert after["not_after"] == pytest.approx(wall.value + lease)
    assert {key: value for key, value in after.items() if key != "not_after"} == {
        key: value for key, value in before.items() if key != "not_after"
    }
    assert after["not_after"] - wall.value >= lease - renew

    wall.advance(renew - 1.0)
    assert lifetime.poll() is None
    assert _tether_data(path)["not_after"] - wall.value >= lease - renew


@pytest.mark.skipif(sys.platform != "linux", reason="write_tether persists records on Linux only")
def test_lease_capped_at_hard_cap_plus_margin(tmp_path: Path) -> None:
    lifetime_module = _lifetime_module()
    clock = _Clock()
    wall = _Clock(20_000.0)
    lease = lifetime_module.TETHER_LEASE_SECONDS
    renew = lifetime_module.TETHER_LEASE_RENEW_SECONDS
    path = _write_test_tether(tmp_path, wall.value + lease)
    lifetime = _start_lifetime(
        _policy(soft=1.0, extension=10.0),
        clock=clock,
        wall=wall,
        activity_probe=lambda _pid, _fd: frozenset({"api_connection"}),
        tether_path=path,
    )
    clock.advance(10.75)
    wall.advance(renew)

    assert lifetime.poll() is None
    hard_remaining = 11.0 - clock.value
    record = _tether_data(path)
    assert record["not_after"] == pytest.approx(
        wall.value + min(lease, hard_remaining + lifetime_module.OWNER_PRECEDENCE_MARGIN_SECONDS)
    )


@pytest.mark.skipif(sys.platform != "linux", reason="write_tether persists records on Linux only")
def test_wall_jump_renews_immediately_without_firing_deadlines(tmp_path: Path) -> None:
    lifetime_module = _lifetime_module()
    clock = _Clock()
    wall = _Clock(30_000.0)
    lease = lifetime_module.TETHER_LEASE_SECONDS
    path = _write_test_tether(tmp_path, wall.value + lease)
    lifetime = _start_lifetime(
        _policy(soft=40_000.0, extension=20_000.0),
        clock=clock,
        wall=wall,
        tether_path=path,
    )
    wall.advance(8 * 60 * 60)

    assert lifetime.poll() is None
    assert lifetime.decision is None
    assert lifetime.elapsed_seconds == 0.0
    assert _tether_data(path)["not_after"] == pytest.approx(wall.value + lease)


@pytest.mark.skipif(sys.platform != "linux", reason="write_tether persists records on Linux only")
def test_renew_failure_logged_and_retried_sooner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lifetime_module = _lifetime_module()
    clock = _Clock()
    wall = _Clock(40_000.0)
    renew = lifetime_module.TETHER_LEASE_RENEW_SECONDS
    retry = lifetime_module._LEASE_RETRY_SECONDS
    path = _write_test_tether(tmp_path, wall.value + lifetime_module.TETHER_LEASE_SECONDS)
    calls = 0

    def fail_renewal(_path: Path, _not_after: float) -> bool:
        nonlocal calls
        calls += 1
        return False

    monkeypatch.setattr(lifetime_module, "renew_tether", fail_renewal)
    lifetime = _start_lifetime(
        _policy(soft=20_000.0, extension=20_000.0),
        clock=clock,
        wall=wall,
        tether_path=path,
    )
    wall.advance(renew)

    with structlog.testing.capture_logs() as logs:
        assert lifetime.poll() is None
        wall.advance(retry - 1.0)
        assert lifetime.poll() is None
        assert calls == 1
        wall.advance(1.0)
        assert lifetime.poll() is None

    assert calls == 2
    assert len(_events(logs, "cook_tether_lease_renew_failed")) == 1


def test_warnings_written_at_t_minus_30_and_5_minutes_once_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lifetime_module = _lifetime_module()
    clock = _Clock()
    wall = _Clock(60_000.0)
    notice_path = tmp_path / "notice.json"
    original_write_versioned_json = lifetime_module.write_versioned_json
    writes: list[Path] = []

    def recording_write_versioned_json(
        path: Path,
        payload: dict[str, Any],
        schema_version: int,
        *,
        strict_durability: bool = False,
    ) -> None:
        writes.append(path)
        original_write_versioned_json(
            path,
            payload,
            schema_version,
            strict_durability=strict_durability,
        )

    monkeypatch.setattr(lifetime_module, "write_versioned_json", recording_write_versioned_json)
    lifetime = _start_lifetime(
        _policy(soft=3600.0, extension=0.0),
        clock=clock,
        wall=wall,
        activity_probe=lambda _pid, _fd: frozenset({"api_connection"}),
        notice_path=notice_path,
    )

    clock.advance(1799.0)
    wall.advance(1799.0)
    assert lifetime.poll() is None
    assert not notice_path.exists()

    clock.advance(1.0)
    wall.advance(1.0)
    assert lifetime.poll() is None
    first_content = notice_path.read_text(encoding="utf-8")
    first = json.loads(first_content)
    assert {"level", "message", "deadline_epoch"} <= first.keys()
    assert first["message"]
    assert first["deadline_epoch"] == pytest.approx(63_600.0)
    assert len(writes) == 1

    clock.advance(1.0)
    wall.advance(1.0)
    assert lifetime.poll() is None
    assert notice_path.read_text(encoding="utf-8") == first_content
    assert len(writes) == 1

    clock.advance(1499.0)
    wall.advance(1499.0)
    assert lifetime.poll() is None
    second = json.loads(notice_path.read_text(encoding="utf-8"))
    assert second["level"] != first["level"]
    assert second["message"]
    assert second["deadline_epoch"] == pytest.approx(63_600.0)
    assert len(writes) == 2

    clock.advance(1.0)
    wall.advance(1.0)
    assert lifetime.poll() is None
    assert len(writes) == 2
