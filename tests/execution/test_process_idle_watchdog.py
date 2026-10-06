"""Tests for the stdout idle watchdog coroutine (_watch_stdout_idle)."""

from __future__ import annotations

import functools
import sys
import textwrap
import time
from unittest.mock import MagicMock

import anyio
import pytest

import autoskillit.execution.process._race_watchers as _patch_process__race_watchers
from autoskillit.execution.process._process_race import RaceAccumulator
from autoskillit.execution.process._race_watchers import (
    CLEANUP_BUDGET_SECONDS,
    _watch_stdout_idle,
)
from tests.conftest import make_stub_inspector


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("terminal_after", "minimum_elapsed"),
    [(0.03, 0.03), (None, 0.06)],
    ids=["terminal-releases", "active-max-bound"],
)
async def test_pending_task_suppression_releases_on_terminal_or_max_bound(
    tmp_path: anyio.Path,
    terminal_after: float | None,
    minimum_elapsed: float,
) -> None:
    stdout_file = tmp_path / "stdout.txt"
    await anyio.Path(stdout_file).write_bytes(b"initial\n")
    pending = [True]
    acc = RaceAccumulator()
    trigger = anyio.Event()
    started = time.monotonic()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(
            functools.partial(
                _watch_stdout_idle,
                stdout_file,
                0.01,
                acc,
                trigger,
                0.005,
                max_suppression_seconds=0.06,
                has_pending_tasks=lambda: pending[0],
            )
        )
        if terminal_after is not None:
            await anyio.sleep(terminal_after)
            pending[0] = False
        with anyio.fail_after(0.3):
            await trigger.wait()

    elapsed = time.monotonic() - started
    assert acc.idle_stall is True
    assert elapsed >= minimum_elapsed
    assert elapsed < 0.2


pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

WRITE_BURST_THEN_STALL_SCRIPT = textwrap.dedent("""\
    import sys, time, json
    for i in range(3):
        sys.stdout.write(json.dumps({"type": "assistant", "i": i}) + "\\n")
        sys.stdout.flush()
    time.sleep(9999)
""")

WRITE_CONTINUOUS_SCRIPT = textwrap.dedent("""\
    import sys, time, json
    for i in range(10):
        sys.stdout.write(json.dumps({"type": "assistant", "i": i}) + "\\n")
        sys.stdout.flush()
        time.sleep(0.5)
""")


@pytest.mark.anyio
async def test_watch_stdout_idle_fires_on_silence(tmp_path: anyio.Path) -> None:
    """Watchdog fires IDLE_STALL when stdout stops growing."""
    script = tmp_path / "burst_then_stall.py"
    await anyio.Path(script).write_text(WRITE_BURST_THEN_STALL_SCRIPT)
    stdout_file = tmp_path / "stdout.txt"

    acc = RaceAccumulator()
    trigger = anyio.Event()

    async with anyio.create_task_group() as tg:
        proc = await anyio.open_process(
            [sys.executable, str(script)],
            stdout=await anyio.Path(stdout_file).open("wb"),
            stderr=None,
        )

        async def run_watchdog() -> None:
            await _watch_stdout_idle(
                stdout_file,
                idle_output_timeout=2.0,
                acc=acc,
                trigger=trigger,
                _poll_interval=0.2,
            )

        start = time.monotonic()
        with anyio.fail_after(5.0):
            tg.start_soon(run_watchdog)
            await trigger.wait()

        elapsed = time.monotonic() - start
        assert acc.idle_stall is True
        assert 2.0 <= elapsed < 4.0
        tg.cancel_scope.cancel()
        proc.kill()


@pytest.mark.anyio
async def test_watch_stdout_idle_resets_on_continuous_output(tmp_path: anyio.Path) -> None:
    """Watchdog does NOT fire when stdout keeps growing."""
    script = tmp_path / "continuous.py"
    await anyio.Path(script).write_text(WRITE_CONTINUOUS_SCRIPT)
    stdout_file = tmp_path / "stdout.txt"

    acc = RaceAccumulator()
    trigger = anyio.Event()

    with anyio.fail_after(8.0):
        async with anyio.create_task_group() as tg:
            proc = await anyio.open_process(
                [sys.executable, str(script)],
                stdout=await anyio.Path(stdout_file).open("wb"),
                stderr=None,
            )

            tg.start_soon(
                _watch_stdout_idle,
                stdout_file,
                3.0,
                acc,
                trigger,
                0.2,
            )

            await proc.wait()
            # Script ran to completion — cancel the watchdog
            tg.cancel_scope.cancel()

    assert acc.idle_stall is False


@pytest.mark.anyio
async def test_watch_stdout_idle_handles_missing_file(tmp_path: anyio.Path) -> None:
    """Watchdog tolerates missing stdout file until it appears."""
    stdout_file = tmp_path / "stdout.txt"

    acc = RaceAccumulator()
    trigger = anyio.Event()

    async def create_file_after_delay() -> None:
        await anyio.sleep(1.0)
        await anyio.Path(stdout_file).write_bytes(b"some data\n")
        await anyio.sleep(3.0)

    with anyio.fail_after(5.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(create_file_after_delay)
            tg.start_soon(
                _watch_stdout_idle,
                stdout_file,
                2.0,
                acc,
                trigger,
                0.2,
            )
            await trigger.wait()

    assert acc.idle_stall is True


@pytest.mark.anyio
async def test_watch_stdout_idle_restored_file_does_not_fabricate_growth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    polls = [0]
    acc = RaceAccumulator()
    trigger = anyio.Event()

    async def advance_clock(_seconds: float) -> None:
        clock[0] += 1.0
        polls[0] += 1
        if polls[0] == 4:
            trigger.set()

    first_stat = MagicMock(st_size=10)
    recreated_stat = MagicMock(st_size=10)
    stdout_path = MagicMock()
    stdout_path.stat.side_effect = [first_stat, OSError(), recreated_stat]
    monkeypatch.setattr(_patch_process__race_watchers.anyio, "sleep", advance_clock)
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    await _watch_stdout_idle(stdout_path, 1.5, acc, trigger, 1.0)

    assert acc.idle_stall is True
    assert clock[0] == 3.0
    assert stdout_path.stat.call_count == 3


@pytest.mark.anyio
async def test_inspector_callback_kill_fires_idle_stall(tmp_path: anyio.Path) -> None:
    """KILL verdict from inspector callback sets acc.inspector_verdict + idle_stall."""
    stdout_file = tmp_path / "stdout.txt"
    await anyio.Path(stdout_file).write_bytes(b"initial output\n")

    acc = RaceAccumulator()
    trigger = anyio.Event()

    timeout_scope_ref: list[anyio.CancelScope | None] = [None]

    async def set_scope() -> None:
        with anyio.move_on_after(30.0) as scope:
            timeout_scope_ref[0] = scope
            await trigger.wait()
        if not trigger.is_set():
            trigger.set()

    with anyio.fail_after(2.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(set_scope)
            await anyio.sleep(0.05)
            tg.start_soon(
                functools.partial(
                    _watch_stdout_idle,
                    stdout_file,
                    0.2,
                    acc,
                    trigger,
                    0.05,
                    inspector_callback=make_stub_inspector("KILL"),
                    timeout_scope_ref=timeout_scope_ref,
                )
            )
            await trigger.wait()

    assert acc.inspector_verdict is not None
    assert acc.inspector_verdict.action == "KILL"
    assert acc.idle_stall is True
    assert trigger.is_set()


@pytest.mark.anyio
async def test_inspector_callback_spare_resets_timer(tmp_path: anyio.Path) -> None:
    """SPARE verdict resets last_growth_time and continues polling without firing kill."""
    stdout_file = tmp_path / "stdout.txt"
    await anyio.Path(stdout_file).write_bytes(b"initial output\n")

    acc = RaceAccumulator()
    trigger = anyio.Event()

    timeout_scope_ref: list[anyio.CancelScope | None] = [None]
    callback_calls: list[int] = [0]

    async def killing_callback(evidence):  # type: ignore[no-untyped-def]
        callback_calls[0] += 1
        from autoskillit.core import InspectorVerdict

        if callback_calls[0] == 1:
            return InspectorVerdict(
                action="SPARE", reasoning="first", confidence="high", elapsed_seconds=0.0
            )
        trigger.set()
        return InspectorVerdict(
            action="KILL", reasoning="second", confidence="high", elapsed_seconds=0.0
        )

    async def set_scope() -> None:
        with anyio.move_on_after(30.0) as scope:
            timeout_scope_ref[0] = scope
            await anyio.sleep(2.0)
        if not trigger.is_set():
            trigger.set()

    with anyio.fail_after(3.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(set_scope)
            await anyio.sleep(0.05)
            tg.start_soon(
                functools.partial(
                    _watch_stdout_idle,
                    stdout_file,
                    0.2,
                    acc,
                    trigger,
                    0.05,
                    inspector_callback=killing_callback,
                    timeout_scope_ref=timeout_scope_ref,
                )
            )
            await trigger.wait()

    assert callback_calls[0] >= 2
    assert acc.inspector_verdict is not None
    assert acc.inspector_verdict.action == "KILL"


@pytest.mark.anyio
async def test_inspector_callback_none_preserves_behavior(tmp_path: anyio.Path) -> None:
    """inspector_callback=None preserves backward-compatible IDLE_STALL behavior."""
    stdout_file = tmp_path / "stdout.txt"
    await anyio.Path(stdout_file).write_bytes(b"initial output\n")

    acc = RaceAccumulator()
    trigger = anyio.Event()

    with anyio.fail_after(2.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                functools.partial(
                    _watch_stdout_idle,
                    stdout_file,
                    0.2,
                    acc,
                    trigger,
                    0.05,
                )
            )
            await trigger.wait()

    assert acc.inspector_verdict is None
    assert acc.idle_stall is True
    assert trigger.is_set()


@pytest.mark.anyio
async def test_inspector_skipped_when_insufficient_time(tmp_path: anyio.Path) -> None:
    """Inspector is skipped when CancelScope has < CLEANUP_BUDGET remaining."""
    stdout_file = tmp_path / "stdout.txt"
    await anyio.Path(stdout_file).write_bytes(b"initial output\n")

    acc = RaceAccumulator()
    trigger = anyio.Event()

    callback_called: list[bool] = [False]

    async def tracking_callback(evidence):  # type: ignore[no-untyped-def]
        callback_called[0] = True
        from autoskillit.core import InspectorVerdict

        return InspectorVerdict(
            action="KILL", reasoning="should not fire", confidence="high", elapsed_seconds=0.0
        )

    timeout_scope_ref: list[anyio.CancelScope | None] = [None]

    async def set_short_scope() -> None:
        with anyio.move_on_after(CLEANUP_BUDGET_SECONDS * 0.03) as scope:
            timeout_scope_ref[0] = scope
            await trigger.wait()

    with anyio.fail_after(3.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(set_short_scope)
            await anyio.sleep(0.01)
            tg.start_soon(
                functools.partial(
                    _watch_stdout_idle,
                    stdout_file,
                    0.1,
                    acc,
                    trigger,
                    0.05,
                    inspector_callback=tracking_callback,
                    timeout_scope_ref=timeout_scope_ref,
                )
            )
            await trigger.wait()

    assert callback_called[0] is False
    assert acc.inspector_verdict is None
    assert acc.idle_stall is True


@pytest.mark.anyio
async def test_inspector_skipped_when_scope_none(tmp_path: anyio.Path) -> None:
    """Inspector is skipped when timeout_scope_ref element is None (scope not yet set)."""
    stdout_file = tmp_path / "stdout.txt"
    await anyio.Path(stdout_file).write_bytes(b"initial output\n")

    acc = RaceAccumulator()
    trigger = anyio.Event()

    callback_called: list[bool] = [False]

    async def tracking_callback(evidence):  # type: ignore[no-untyped-def]
        callback_called[0] = True
        from autoskillit.core import InspectorVerdict

        return InspectorVerdict(
            action="KILL", reasoning="should not fire", confidence="high", elapsed_seconds=0.0
        )

    timeout_scope_ref: list[anyio.CancelScope | None] = [None]

    with anyio.fail_after(2.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                functools.partial(
                    _watch_stdout_idle,
                    stdout_file,
                    0.1,
                    acc,
                    trigger,
                    0.05,
                    inspector_callback=tracking_callback,
                    timeout_scope_ref=timeout_scope_ref,
                )
            )
            await trigger.wait()

    assert callback_called[0] is False
    assert acc.inspector_verdict is None
    assert acc.idle_stall is True
