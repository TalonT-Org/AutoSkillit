"""Tests for the stdout idle watchdog coroutine (_watch_stdout_idle)."""

from __future__ import annotations

import functools
import os
import sys
import textwrap
import time
from unittest.mock import MagicMock, patch

import anyio
import pytest
import structlog.testing

from autoskillit.execution.process._process_race import (
    CLEANUP_BUDGET_SECONDS,
    RaceAccumulator,
    _watch_stdout_idle,
)
from tests.conftest import make_stub_inspector


@pytest.mark.anyio
async def test_watch_stdout_idle_emits_suppression_evaluated_debug_log(
    tmp_path: anyio.Path,
) -> None:
    """Debug log fires when marker_dir is None and IDLE_STALL fires."""
    stdout_file = tmp_path / "stdout.txt"
    await anyio.Path(stdout_file).write_bytes(b"initial output\n")

    acc = RaceAccumulator()
    trigger = anyio.Event()

    with patch(
        "autoskillit.execution.process._process_race.logger", new=MagicMock()
    ) as mock_debug:
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
                    )
                )
                await trigger.wait()

    debug_calls = mock_debug.debug.call_args_list
    evaluated_calls = [
        c for c in debug_calls if c.args and c.args[0] == "stdout_idle_stall_suppression_evaluated"
    ]
    assert len(evaluated_calls) == 1
    call_kwargs = evaluated_calls[0].kwargs
    assert call_kwargs.get("marker_dir_present") is False
    assert call_kwargs.get("session_id") is None
    assert call_kwargs.get("suppression_skipped_reason") == "marker_dir_none"


@pytest.mark.anyio
async def test_watch_stdout_idle_emits_suppression_evaluated_with_marker_dir(
    tmp_path: anyio.Path,
) -> None:
    """Debug log fires when marker_dir is present but marker inactive."""
    stdout_file = tmp_path / "stdout.txt"
    await anyio.Path(stdout_file).write_bytes(b"initial output\n")

    acc = RaceAccumulator()
    trigger = anyio.Event()

    with patch(
        "autoskillit.execution.process._process_race.logger", new=MagicMock()
    ) as mock_debug:
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
                        marker_dir=tmp_path,
                    )
                )
                await trigger.wait()

    debug_calls = mock_debug.debug.call_args_list
    evaluated_calls = [
        c for c in debug_calls if c.args and c.args[0] == "stdout_idle_stall_suppression_evaluated"
    ]
    assert len(evaluated_calls) == 1
    call_kwargs = evaluated_calls[0].kwargs
    assert call_kwargs.get("marker_dir_present") is True
    assert "suppression_skipped_reason" not in call_kwargs


@pytest.mark.anyio
async def test_watch_stdout_idle_suppression_evaluated_fires_once_during_suppression(
    tmp_path: anyio.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Debug log fires exactly once on first suppression entry, not each tick."""
    stdout_file = tmp_path / "stdout.txt"
    await anyio.Path(stdout_file).write_bytes(b"initial output\n")

    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_execution_marker",
        lambda marker_dir, session_id=None: True,
    )

    acc = RaceAccumulator()
    trigger = anyio.Event()

    with patch(
        "autoskillit.execution.process._process_race.logger", new=MagicMock()
    ) as mock_debug:

        async def cancel_when_suppressed() -> None:
            # Wait for at least 2 suppressed warnings before cancelling
            suppressed_count = 0
            start = anyio.current_time()
            while suppressed_count < 2:
                await anyio.sleep(0.02)
                suppressed_count = sum(
                    1
                    for c in mock_debug.call_args_list
                    if (c.kwargs.get("event") == "stdout_idle_stall_suppressed")
                )
                if anyio.current_time() - start > 1.5:
                    break
            trigger.set()

        with anyio.fail_after(2.0):
            async with anyio.create_task_group() as tg:
                tg.start_soon(cancel_when_suppressed)
                tg.start_soon(
                    functools.partial(
                        _watch_stdout_idle,
                        stdout_file,
                        0.05,
                        acc,
                        trigger,
                        0.02,
                        marker_dir=tmp_path,
                        session_id="test-sess",
                        max_suppression_seconds=10.0,
                    )
                )
                await trigger.wait()

    debug_calls = mock_debug.debug.call_args_list
    evaluated_calls = [
        c for c in debug_calls if c.args and c.args[0] == "stdout_idle_stall_suppression_evaluated"
    ]
    assert len(evaluated_calls) == 1


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
async def test_watch_stdout_idle_suppressed_by_dispatch_marker(
    tmp_path: anyio.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Active dispatch marker suppresses idle stall within cap."""
    stdout_file = tmp_path / "stdout.txt"
    await anyio.Path(stdout_file).write_bytes(b"initial output\n")

    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_execution_marker",
        lambda marker_dir, session_id=None: True,
    )

    acc = RaceAccumulator()
    trigger = anyio.Event()

    with structlog.testing.capture_logs() as cap:

        async def cancel_when_suppressed() -> None:
            while not any(e.get("event") == "stdout_idle_stall_suppressed" for e in cap):
                await anyio.sleep(0.01)
            trigger.set()

        async with anyio.create_task_group() as tg:
            tg.start_soon(cancel_when_suppressed)
            with anyio.fail_after(2.0):
                tg.start_soon(
                    functools.partial(
                        _watch_stdout_idle,
                        stdout_file,
                        0.1,  # idle_output_timeout — very short
                        acc,
                        trigger,
                        0.05,  # _poll_interval
                        marker_dir=tmp_path,
                        session_id="test-sess",
                        max_suppression_seconds=10.0,
                    )
                )
                await trigger.wait()

    assert acc.idle_stall is False


@pytest.mark.anyio
async def test_watch_stdout_idle_fires_when_suppression_cap_exceeded(
    tmp_path: anyio.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Suppression cap reached — idle stall fires despite active marker."""
    stdout_file = tmp_path / "stdout.txt"
    await anyio.Path(stdout_file).write_bytes(b"initial output\n")

    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_execution_marker",
        lambda marker_dir, session_id=None: True,
    )

    acc = RaceAccumulator()
    trigger = anyio.Event()

    with anyio.fail_after(3.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                functools.partial(
                    _watch_stdout_idle,
                    stdout_file,
                    0.05,
                    acc,
                    trigger,
                    0.02,
                    marker_dir=tmp_path,
                    session_id=None,
                    max_suppression_seconds=0.1,
                )
            )
            await trigger.wait()

    assert acc.idle_stall is True


@pytest.mark.anyio
async def test_watch_stdout_idle_no_marker_dir_fires_immediately(tmp_path: anyio.Path) -> None:
    """No marker_dir — idle stall fires unchanged (existing behavior)."""
    stdout_file = tmp_path / "stdout.txt"
    await anyio.Path(stdout_file).write_bytes(b"initial output\n")

    acc = RaceAccumulator()
    trigger = anyio.Event()

    with anyio.fail_after(2.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                _watch_stdout_idle,
                stdout_file,
                0.1,
                acc,
                trigger,
                0.02,
            )
            await trigger.wait()

    assert acc.idle_stall is True


@pytest.mark.anyio
async def test_watch_stdout_idle_suppression_timer_resets_on_growth(
    tmp_path: anyio.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Growth resets suppression timer — second idle period gets fresh window."""
    stdout_file = tmp_path / "stdout.txt"
    await anyio.Path(stdout_file).write_bytes(b"initial\n")

    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_execution_marker",
        lambda marker_dir, session_id=None: True,
    )

    acc = RaceAccumulator()
    trigger = anyio.Event()

    async def grow_after_delay() -> None:
        await anyio.sleep(0.15)
        async with await anyio.open_file(stdout_file, "ab") as f:
            await f.write(b"more data\n")

    with anyio.fail_after(3.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(grow_after_delay)
            tg.start_soon(
                functools.partial(
                    _watch_stdout_idle,
                    stdout_file,
                    0.05,
                    acc,
                    trigger,
                    0.02,
                    marker_dir=tmp_path,
                    session_id=None,
                    max_suppression_seconds=0.2,
                )
            )
            await trigger.wait()

    # Eventually fires after growth stops and cap is exceeded
    assert acc.idle_stall is True


@pytest.mark.anyio
async def test_watch_stdout_idle_emits_suppression_warning(
    tmp_path: anyio.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Suppression emits structured warning with expected kwargs."""
    stdout_file = tmp_path / "stdout.txt"
    await anyio.Path(stdout_file).write_bytes(b"initial output\n")

    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_execution_marker",
        lambda marker_dir, session_id=None: True,
    )

    acc = RaceAccumulator()
    trigger = anyio.Event()

    with structlog.testing.capture_logs() as cap:

        async def cancel_when_suppressed() -> None:
            while not any(e.get("event") == "stdout_idle_stall_suppressed" for e in cap):
                await anyio.sleep(0.01)
            trigger.set()

        async with anyio.create_task_group() as tg:
            tg.start_soon(cancel_when_suppressed)
            with anyio.fail_after(2.0):
                tg.start_soon(
                    functools.partial(
                        _watch_stdout_idle,
                        stdout_file,
                        0.05,
                        acc,
                        trigger,
                        0.02,
                        marker_dir=tmp_path,
                        session_id="my-session",
                        max_suppression_seconds=10.0,
                    )
                )
                await trigger.wait()

    warnings = [e for e in cap if e.get("event") == "stdout_idle_stall_suppressed"]
    assert len(warnings) >= 1
    w = warnings[0]
    assert w["marker_dir"] == str(tmp_path)
    assert w["session_id"] == "my-session"
    assert "suppression_elapsed" in w
    assert w["max_suppression_seconds"] == 10.0


@pytest.mark.anyio
async def test_watch_stdout_idle_marker_false_fires_immediately(
    tmp_path: anyio.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dispatch marker returns False — idle stall fires without suppression."""
    stdout_file = tmp_path / "stdout.txt"
    await anyio.Path(stdout_file).write_bytes(b"initial output\n")

    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_execution_marker",
        lambda marker_dir, session_id=None: False,
    )

    acc = RaceAccumulator()
    trigger = anyio.Event()

    with anyio.fail_after(2.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                functools.partial(
                    _watch_stdout_idle,
                    stdout_file,
                    0.1,
                    acc,
                    trigger,
                    0.02,
                    marker_dir=tmp_path,
                    session_id="test",
                )
            )
            await trigger.wait()

    assert acc.idle_stall is True


@pytest.mark.anyio
async def test_watch_stdout_idle_dispatch_marker_suppresses_stall(
    tmp_path: anyio.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Active dispatch marker suppresses idle stall within the suppression window."""
    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_execution_marker",
        lambda marker_dir, session_id=None: True,
    )
    stdout_file = tmp_path / "stdout.txt"
    stdout_file.write_bytes(b"")

    acc = RaceAccumulator()
    trigger = anyio.Event()

    with anyio.fail_after(4.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                functools.partial(
                    _watch_stdout_idle,
                    stdout_file,
                    0.5,
                    acc,
                    trigger,
                    0.2,
                    marker_dir=tmp_path,
                )
            )
            await anyio.sleep(1.0)
            tg.cancel_scope.cancel()

    assert acc.idle_stall is False


@pytest.mark.anyio
async def test_watch_stdout_idle_fires_when_marker_absent(tmp_path: anyio.Path) -> None:
    """No marker_dir kwarg — idle stall fires unchanged (existing behavior path)."""
    stdout_file = tmp_path / "stdout.txt"
    stdout_file.write_bytes(b"")

    acc = RaceAccumulator()
    trigger = anyio.Event()

    with anyio.fail_after(3.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                _watch_stdout_idle,
                stdout_file,
                0.5,
                acc,
                trigger,
                0.2,
            )
            await trigger.wait()

    assert acc.idle_stall is True


@pytest.mark.anyio
async def test_watch_stdout_idle_fires_when_marker_stale(tmp_path: anyio.Path) -> None:
    """Marker file exists but is stale (mtime 120s ago) — watchdog fires."""
    marker_path = tmp_path / "dispatch-in-progress-testsession-abc123.marker"
    marker_path.write_text("{}")
    stale_time = time.time() - 120
    os.utime(str(marker_path), (stale_time, stale_time))

    stdout_file = tmp_path / "stdout.txt"
    stdout_file.write_bytes(b"")

    acc = RaceAccumulator()
    trigger = anyio.Event()

    with anyio.fail_after(3.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                functools.partial(
                    _watch_stdout_idle,
                    stdout_file,
                    0.5,
                    acc,
                    trigger,
                    0.2,
                    marker_dir=tmp_path,
                    session_id=None,
                )
            )
            await trigger.wait()

    assert acc.idle_stall is True


@pytest.mark.anyio
async def test_watch_stdout_idle_marker_suppression_bounded(
    tmp_path: anyio.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Suppression cap exceeded — idle stall fires despite active marker."""
    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_execution_marker",
        lambda marker_dir, session_id=None: True,
    )
    stdout_file = tmp_path / "stdout.txt"
    stdout_file.write_bytes(b"")

    acc = RaceAccumulator()
    trigger = anyio.Event()

    start = time.monotonic()
    with anyio.fail_after(6.0):
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                functools.partial(
                    _watch_stdout_idle,
                    stdout_file,
                    0.5,
                    acc,
                    trigger,
                    0.2,
                    marker_dir=tmp_path,
                    max_suppression_seconds=1.5,
                )
            )
            await trigger.wait()

    elapsed = time.monotonic() - start
    assert acc.idle_stall is True
    assert elapsed >= 1.5
    assert elapsed < 5.0


@pytest.mark.anyio
async def test_stdout_idle_NOT_fired_when_execution_marker_active(
    tmp_path: anyio.Path,
) -> None:
    """IDLE_STALL NOT fired when stdout silent but run-skill-in-progress marker is fresh.

    Failing before implementation: run-skill-in-progress-* marker is invisible to
    dispatch-in-progress-* glob, so IDLE_STALL fires immediately.
    After fix: marker matched by *-in-progress-* glob, stall suppressed.
    """
    stdout_file = tmp_path / "stdout.txt"
    await anyio.Path(stdout_file).write_bytes(b"initial output\n")

    marker_dir = tmp_path / "marker_dir"
    await anyio.Path(marker_dir).mkdir()
    marker_path = marker_dir / "run-skill-in-progress-caller-session-step1.marker"
    await anyio.Path(marker_path).write_text("{}")

    acc = RaceAccumulator()
    trigger = anyio.Event()

    async def touch_marker() -> None:
        for _ in range(100):
            await anyio.sleep(0.02)
            try:
                await anyio.Path(marker_path).touch()
            except OSError:
                break

    with anyio.move_on_after(0.5):
        async with anyio.create_task_group() as tg:
            tg.start_soon(touch_marker)
            tg.start_soon(
                functools.partial(
                    _watch_stdout_idle,
                    stdout_file,
                    0.05,  # idle_output_timeout — short to trigger quickly if not suppressed
                    acc,
                    trigger,
                    0.02,  # _poll_interval
                    marker_dir=marker_dir,
                    session_id="caller-session",
                    max_suppression_seconds=10.0,
                )
            )
            await trigger.wait()

    assert not acc.idle_stall, (
        "IDLE_STALL fired even though run-skill execution marker was active. "
        "The *-in-progress-* glob fix is needed."
    )


# test write


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
