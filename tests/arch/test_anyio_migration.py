"""Regression guards for the asyncio→anyio migration (C-6)."""

from __future__ import annotations

from tests.arch._helpers import (
    PROCESS_KILL_PY,
    PROCESS_MONITOR_PY,
    PROCESS_PY,
    PROCESS_RACE_PY,
    SRC_ROOT,
)


class TestNoAsyncioRuntimePrimitives:
    """REQ-MIG-001: asyncio primitives are removed from execution/process.py call sites."""

    def test_no_asyncio_sleep_calls(self):
        source = PROCESS_PY.read_text()
        assert "asyncio.sleep(" not in source

    def test_no_asyncio_to_thread_calls(self):
        source = PROCESS_PY.read_text()
        assert "asyncio.to_thread(" not in source

    def test_no_asyncio_create_subprocess_exec(self):
        source = PROCESS_PY.read_text()
        assert "asyncio.create_subprocess_exec(" not in source

    def test_no_asyncio_event_instantiation(self):
        source = PROCESS_PY.read_text()
        assert "asyncio.Event()" not in source

    def test_no_asyncio_wait_for_calls(self):
        source = PROCESS_PY.read_text()
        assert "asyncio.wait_for(" not in source

    def test_no_asyncio_get_event_loop_time(self):
        source = PROCESS_PY.read_text()
        assert "asyncio.get_event_loop()" not in source

    def test_no_asyncio_get_running_loop_run_in_executor(self):
        source = PROCESS_PY.read_text()
        assert "asyncio.get_running_loop()" not in source

    def test_no_asyncio_cancelled_error_reference(self):
        """REQ-BEH-010: asyncio.CancelledError must not appear in process.py.

        anyio raises anyio.get_cancelled_exc_class() (trio.Cancelled on the trio
        backend), not asyncio.CancelledError. Catching asyncio.CancelledError in
        a finally/except block would silently miss cancellations on trio, breaking
        the anyio backend contract.
        """
        source = PROCESS_PY.read_text()
        assert "asyncio.CancelledError" not in source


class TestAnyioPrimitivesUsed:
    """REQ-MIG-002..004: anyio primitives replace the removed asyncio calls."""

    def test_anyio_to_thread_run_sync_present(self):
        source = PROCESS_KILL_PY.read_text()
        assert "anyio.to_thread.run_sync(" in source

    def test_anyio_sleep_present(self):
        source = PROCESS_MONITOR_PY.read_text()
        assert "anyio.sleep(" in source

    def test_time_monotonic_replaces_event_loop_time(self):
        source = PROCESS_MONITOR_PY.read_text()
        assert ".monotonic()" in source

    def test_anyio_open_process_present(self):
        source = PROCESS_PY.read_text()
        assert "anyio.open_process(" in source

    def test_anyio_event_present(self):
        source = PROCESS_PY.read_text()
        assert "anyio.Event()" in source

    def test_anyio_move_on_after_present(self):
        source = PROCESS_PY.read_text()
        assert "anyio.move_on_after(" in source


def test_server_has_no_asyncio_create_task() -> None:
    """server/ must not use asyncio.create_task.

    Use DefaultBackgroundSupervisor.submit() instead.
    """
    server_dir = SRC_ROOT / "server"
    violations: list[str] = []
    for path in sorted(server_dir.rglob("*.py")):
        text = path.read_text()
        if "asyncio.create_task" in text:
            violations.append(str(path.relative_to(SRC_ROOT.parent.parent)))
    assert not violations, (
        "server/ must not use asyncio.create_task directly.\n"
        "Use tool_ctx.background.submit() (DefaultBackgroundSupervisor) instead.\n"
        "Violations:\n" + "\n".join(f"  {v}" for v in violations)
    )


def test_server_has_no_asyncio_ensure_future() -> None:
    """server/ must not use asyncio.ensure_future.

    Use DefaultBackgroundSupervisor.submit() instead.
    """
    server_dir = SRC_ROOT / "server"
    violations: list[str] = []
    for path in sorted(server_dir.rglob("*.py")):
        text = path.read_text()
        if "asyncio.ensure_future" in text or "loop.create_task" in text:
            violations.append(str(path.relative_to(SRC_ROOT.parent.parent)))
    assert not violations, (
        "server/ must not use asyncio.ensure_future or loop.create_task.\n"
        "Violations:\n" + "\n".join(f"  {v}" for v in violations)
    )


class TestProcTypeAnnotationUpdated:
    """REQ-MIG-005/scan_done_signals: proc annotation is anyio.abc.Process, not asyncio."""

    def test_scan_done_signals_proc_annotation_not_asyncio_subprocess(self):
        source = PROCESS_PY.read_text()
        assert "asyncio.subprocess.Process" not in source

    def test_scan_done_signals_proc_annotation_is_anyio(self):
        source = PROCESS_RACE_PY.read_text()
        assert "anyio.abc.Process" in source
