"""Tests for deadline-bound in-flight operation leases."""

from __future__ import annotations

import os
import time
from pathlib import Path

import anyio
import pytest

import autoskillit.core.plugins._operation_lease as lease_module
from autoskillit.core import (
    OPERATION_LEASE_FRESHNESS_SECONDS,
    OPERATION_LEASE_HEARTBEAT_SECONDS,
    InFlightOperations,
    OperationLeaseRecord,
    current_operation_lease,
    operation_lease,
    read_active_operation_leases,
)
from autoskillit.core.io.io import write_versioned_json

pytestmark = [pytest.mark.layer("core"), pytest.mark.medium]


@pytest.mark.anyio
async def test_operation_lease_round_trip_and_cleanup(tmp_path: Path) -> None:
    registry = InFlightOperations()
    deadline = time.time() + 60

    async with operation_lease(
        tmp_path,
        operation="run_skill",
        not_after_epoch=deadline,
        registry=registry,
    ) as handle:
        records = read_active_operation_leases(tmp_path, now_epoch=time.time())
        lease_path = handle.path
        assert len(records) == 1
        assert records[0] == handle.record
        assert records[0].operation == "run_skill"
        assert records[0].not_after_epoch == deadline
        assert registry.active_count == 1
        assert current_operation_lease() is handle
        assert lease_path is not None and lease_path.exists()

    assert read_active_operation_leases(tmp_path, now_epoch=time.time()) == ()
    assert lease_path is not None and not lease_path.exists()
    assert registry.active_count == 0
    assert current_operation_lease() is None


def test_reader_ignores_expired_and_stale_leases(tmp_path: Path) -> None:
    now = time.time()
    expired = OperationLeaseRecord("expired", "tool", now - 20, now - 1)
    stale = OperationLeaseRecord("stale", "tool", now - 20, now + 60)
    expired_path = tmp_path / "expired.lease.json"
    stale_path = tmp_path / "stale.lease.json"
    write_versioned_json(expired_path, expired.to_payload(), schema_version=1)
    write_versioned_json(stale_path, stale.to_payload(), schema_version=1)
    old_mtime = now - OPERATION_LEASE_FRESHNESS_SECONDS - 1
    os.utime(stale_path, (old_mtime, old_mtime))

    assert read_active_operation_leases(tmp_path, now_epoch=now) == ()


def test_operation_lease_record_rejects_invalid_epochs() -> None:
    for started_at, not_after in (
        (float("nan"), 10.0),
        (10.0, float("inf")),
        (10.0, 10.0),
    ):
        with pytest.raises(ValueError):
            OperationLeaseRecord("invalid", "tool", started_at, not_after)


def test_reader_sorts_active_leases_by_deadline(tmp_path: Path) -> None:
    now = time.time()
    later = OperationLeaseRecord("later", "tool", now - 1, now + 20)
    sooner = OperationLeaseRecord("sooner", "tool", now - 1, now + 10)
    write_versioned_json(tmp_path / "later.lease.json", later.to_payload(), schema_version=1)
    write_versioned_json(tmp_path / "sooner.lease.json", sooner.to_payload(), schema_version=1)

    assert read_active_operation_leases(tmp_path, now_epoch=now) == (sooner, later)


def test_reader_ignores_malformed_and_unknown_schema_files(tmp_path: Path) -> None:
    (tmp_path / "garbage.lease.json").write_bytes(b"not json")
    write_versioned_json(
        tmp_path / "unknown.lease.json",
        {"operation_id": "unknown"},
        schema_version=999,
    )

    assert read_active_operation_leases(tmp_path, now_epoch=time.time()) == ()


@pytest.mark.anyio
async def test_heartbeat_restores_freshness_while_body_is_active(tmp_path: Path) -> None:
    registry = InFlightOperations()

    async with operation_lease(
        tmp_path,
        operation="long_tool",
        not_after_epoch=time.time() + 60,
        registry=registry,
        heartbeat_interval=0.01,
    ) as handle:
        assert handle.path is not None
        old_mtime = time.time() - OPERATION_LEASE_FRESHNESS_SECONDS - 1
        os.utime(handle.path, (old_mtime, old_mtime))

        with anyio.fail_after(0.5):
            while not read_active_operation_leases(tmp_path, now_epoch=time.time()):
                await anyio.sleep(0.005)
        assert registry.active_count == 1

    assert read_active_operation_leases(tmp_path, now_epoch=time.time()) == ()
    assert registry.active_count == 0


@pytest.mark.anyio
async def test_heartbeat_never_recreates_a_missing_lease_file(tmp_path: Path) -> None:
    registry = InFlightOperations()

    async with operation_lease(
        tmp_path,
        operation="tool",
        not_after_epoch=time.time() + 60,
        registry=registry,
        heartbeat_interval=0.005,
    ) as handle:
        path = handle.path
        assert path is not None
        path.unlink()
        await anyio.sleep(0.02)
        assert not path.exists()
        assert read_active_operation_leases(tmp_path, now_epoch=time.time()) == ()

    assert registry.active_count == 0


@pytest.mark.anyio
async def test_narrow_is_min_only_and_reader_honors_expiry(tmp_path: Path) -> None:
    registry = InFlightOperations()
    now = time.time()
    original_deadline = now + 60
    narrowed_deadline = now + 30

    async with operation_lease(
        tmp_path,
        operation="run_skill",
        not_after_epoch=original_deadline,
        registry=registry,
    ) as handle:
        handle.narrow(now + 90)
        assert handle.record.not_after_epoch == original_deadline
        handle.narrow(narrowed_deadline)
        assert handle.record.not_after_epoch == narrowed_deadline
        assert read_active_operation_leases(tmp_path, now_epoch=now)[0].not_after_epoch == (
            narrowed_deadline
        )

        handle.narrow(now + 120)
        assert handle.record.not_after_epoch == narrowed_deadline
        assert read_active_operation_leases(tmp_path, now_epoch=original_deadline + 1) == ()

    assert registry.active_count == 0


@pytest.mark.parametrize("deadline", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.anyio
async def test_non_finite_narrow_leaves_record_and_file_unchanged(
    tmp_path: Path, deadline: float
) -> None:
    registry = InFlightOperations()

    async with operation_lease(
        tmp_path,
        operation="tool",
        not_after_epoch=time.time() + 60,
        registry=registry,
    ) as handle:
        assert handle.path is not None
        original_record = handle.record
        original_bytes = handle.path.read_bytes()
        with pytest.raises(ValueError):
            handle.narrow(deadline)
        assert handle.record is original_record
        assert handle.path.read_bytes() == original_bytes
        assert len(read_active_operation_leases(tmp_path, now_epoch=time.time())) == 1

    assert registry.active_count == 0


@pytest.mark.anyio
async def test_narrowing_to_expired_deadline_retires_file_and_fails(tmp_path: Path) -> None:
    registry = InFlightOperations()

    async with operation_lease(
        tmp_path,
        operation="tool",
        not_after_epoch=time.time() + 60,
        registry=registry,
    ) as handle:
        path = handle.path
        assert path is not None
        with pytest.raises(ValueError):
            handle.narrow(handle.record.started_at_epoch)
        assert not path.exists()
        assert read_active_operation_leases(tmp_path, now_epoch=time.time()) == ()

    assert registry.active_count == 0


@pytest.mark.anyio
async def test_no_channel_keeps_in_process_count_and_contextvar(tmp_path: Path) -> None:
    registry = InFlightOperations()

    async with operation_lease(
        None,
        operation="tool",
        not_after_epoch=time.time() + 60,
        registry=registry,
    ) as handle:
        assert handle.path is None
        assert tuple(tmp_path.iterdir()) == ()
        assert registry.active_count == 1
        assert current_operation_lease() is handle

    assert registry.active_count == 0
    assert current_operation_lease() is None


@pytest.mark.anyio
async def test_concurrent_leases_are_task_local_and_counted(tmp_path: Path) -> None:
    registry = InFlightOperations()

    async with operation_lease(
        None,
        operation="parent",
        not_after_epoch=time.time() + 60,
        registry=registry,
    ) as parent:
        entered_a = anyio.Event()
        entered_b = anyio.Event()
        exit_a = anyio.Event()
        exit_b = anyio.Event()
        exited_a = anyio.Event()
        handles: list[object] = []

        async def run_child(entered: anyio.Event, exit_event: anyio.Event, exited: anyio.Event):
            async with operation_lease(
                None,
                operation="child",
                not_after_epoch=time.time() + 60,
                registry=registry,
            ) as child:
                handles.append(current_operation_lease())
                assert current_operation_lease() is child
                entered.set()
                await exit_event.wait()
            exited.set()

        with anyio.fail_after(1):
            async with anyio.create_task_group() as group:
                group.start_soon(run_child, entered_a, exit_a, exited_a)
                group.start_soon(run_child, entered_b, exit_b, anyio.Event())
                await entered_a.wait()
                await entered_b.wait()
                assert registry.active_count == 3
                assert current_operation_lease() is parent
                exit_a.set()
                await exited_a.wait()
                assert registry.active_count == 2
                assert current_operation_lease() is parent
                exit_b.set()

        assert len(handles) == 2
        assert registry.active_count == 1

    assert registry.active_count == 0


@pytest.mark.anyio
async def test_body_exception_cleans_file_and_registry(tmp_path: Path) -> None:
    registry = InFlightOperations()

    with pytest.raises(RuntimeError, match="body failure"):
        async with operation_lease(
            tmp_path,
            operation="tool",
            not_after_epoch=time.time() + 60,
            registry=registry,
        ):
            raise RuntimeError("body failure")

    assert tuple(tmp_path.iterdir()) == ()
    assert registry.active_count == 0
    assert current_operation_lease() is None


@pytest.mark.anyio
async def test_cancellation_cleans_file_and_registry(tmp_path: Path) -> None:
    registry = InFlightOperations()

    with anyio.move_on_after(0.02):
        async with operation_lease(
            tmp_path,
            operation="tool",
            not_after_epoch=time.time() + 60,
            registry=registry,
            heartbeat_interval=0.001,
        ):
            await anyio.sleep_forever()

    assert tuple(tmp_path.iterdir()) == ()
    assert registry.active_count == 0
    assert current_operation_lease() is None


@pytest.mark.parametrize("deadline", [float("nan"), 0.0])
@pytest.mark.anyio
async def test_invalid_or_expired_deadline_fails_before_admission(
    tmp_path: Path, deadline: float
) -> None:
    registry = InFlightOperations()

    with pytest.raises(ValueError):
        async with operation_lease(
            tmp_path,
            operation="tool",
            not_after_epoch=deadline,
            registry=registry,
        ):
            pytest.fail("invalid lease unexpectedly entered")

    assert registry.active_count == 0
    assert tuple(tmp_path.iterdir()) == ()


@pytest.mark.anyio
async def test_narrow_write_failure_retires_lease_and_balances_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = InFlightOperations()
    original_writer = lease_module.write_versioned_json

    async with operation_lease(
        tmp_path,
        operation="tool",
        not_after_epoch=time.time() + 60,
        registry=registry,
    ) as handle:
        path = handle.path
        assert path is not None
        narrowed_deadline = handle.record.not_after_epoch - 1

        def fail_write(*args: object, **kwargs: object) -> None:
            raise OSError("rewrite failed")

        monkeypatch.setattr(lease_module, "write_versioned_json", fail_write)
        with pytest.raises(OSError, match="rewrite failed"):
            handle.narrow(narrowed_deadline)
        assert handle.record.not_after_epoch == narrowed_deadline
        assert handle.path is None
        assert not path.exists()

    monkeypatch.setattr(lease_module, "write_versioned_json", original_writer)
    assert registry.active_count == 0


@pytest.mark.anyio
async def test_write_failure_yields_fileless_lease_and_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = InFlightOperations()
    events: list[str] = []

    def fail_write(*args: object, **kwargs: object) -> None:
        raise PermissionError("read-only channel")

    monkeypatch.setattr(lease_module, "write_versioned_json", fail_write)
    monkeypatch.setattr(lease_module.logger, "warning", lambda event, **_: events.append(event))

    async with operation_lease(
        tmp_path,
        operation="tool",
        not_after_epoch=time.time() + 60,
        registry=registry,
    ) as handle:
        assert handle.path is None
        assert registry.active_count == 1
        assert tuple(tmp_path.iterdir()) == ()

    assert registry.active_count == 0
    assert "operation_lease_write_failed" in events


@pytest.mark.anyio
async def test_secondary_cleanup_failures_preserve_body_error_and_balance_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = InFlightOperations()
    events: list[str] = []
    original_unlink = Path.unlink

    def fail_lease_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path.name.endswith(".lease.json"):
            raise OSError("unlink failed")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_lease_unlink)
    monkeypatch.setattr(lease_module.logger, "warning", lambda event, **_: events.append(event))

    with pytest.raises(RuntimeError, match="primary body error"):
        async with operation_lease(
            tmp_path,
            operation="tool",
            not_after_epoch=time.time() + 60,
            registry=registry,
            heartbeat_interval=0.005,
        ):
            raise RuntimeError("primary body error")

    lease_file = next(tmp_path.glob("*.lease.json"))
    mtime = lease_file.stat().st_mtime_ns
    await anyio.sleep(0.02)
    assert lease_file.stat().st_mtime_ns == mtime
    assert registry.active_count == 0
    assert current_operation_lease() is None
    assert "operation_lease_unlink_failed" in events


@pytest.mark.anyio
async def test_unexpected_heartbeat_failure_does_not_replace_body_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = InFlightOperations()
    started = anyio.Event()
    events: list[str] = []

    async def fail_heartbeat(_handle: object, _interval: float) -> None:
        started.set()
        raise RuntimeError("heartbeat failed")

    monkeypatch.setattr(lease_module, "_heartbeat", fail_heartbeat)
    monkeypatch.setattr(lease_module.logger, "warning", lambda event, **_: events.append(event))

    with pytest.raises(RuntimeError, match="primary body error"):
        async with operation_lease(
            tmp_path,
            operation="tool",
            not_after_epoch=time.time() + 60,
            registry=registry,
        ):
            await started.wait()
            raise RuntimeError("primary body error")

    assert registry.active_count == 0
    assert current_operation_lease() is None
    assert "operation_lease_heartbeat_failed" in events


def test_lease_freshness_allows_three_heartbeat_intervals() -> None:
    assert OPERATION_LEASE_FRESHNESS_SECONDS >= 3 * OPERATION_LEASE_HEARTBEAT_SECONDS
