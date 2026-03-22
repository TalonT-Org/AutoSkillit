"""Unit tests for BackgroundTaskSupervisor."""

from __future__ import annotations

import anyio
import pytest

from autoskillit.pipeline.background import BackgroundTaskSupervisor


@pytest.mark.anyio
async def test_supervisor_captures_exception():
    """Submitted coroutines that raise must not produce unobserved task exceptions."""
    supervisor = BackgroundTaskSupervisor(audit=None, log=None)
    captured = []

    async def failing_coro():
        raise ValueError("boom")

    supervisor.submit(failing_coro(), on_exception=captured.append)

    for _ in range(20):
        await anyio.sleep(0)
        if captured:
            break

    assert len(captured) == 1
    assert isinstance(captured[0], ValueError)
    assert str(captured[0]) == "boom"


@pytest.mark.anyio
async def test_supervisor_pending_tasks_cleared_after_completion():
    """In-flight task count must return to zero after all submitted coros complete."""
    supervisor = BackgroundTaskSupervisor(audit=None, log=None)

    async def noop():
        pass

    supervisor.submit(noop())
    assert supervisor.pending_count == 1

    for _ in range(20):
        await anyio.sleep(0)
        if supervisor.pending_count == 0:
            break

    assert supervisor.pending_count == 0
