"""Tests for the stdout idle watchdog coroutine (_watch_stdout_idle)."""

from __future__ import annotations

import sys
import textwrap
import time

import anyio
import pytest

from autoskillit.execution.process._process_race import (
    RaceAccumulator,
    _watch_stdout_idle,
)

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
