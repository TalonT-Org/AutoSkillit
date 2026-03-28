"""Unit tests for DefaultBackgroundSupervisor."""

from __future__ import annotations

import pytest

from autoskillit.pipeline.background import DefaultBackgroundSupervisor


@pytest.mark.anyio
async def test_supervisor_captures_exception():
    """Submitted coroutines that raise must not produce unobserved task exceptions."""
    supervisor = DefaultBackgroundSupervisor(audit=None, log=None)
    captured = []

    async def failing_coro():
        raise ValueError("boom")

    supervisor.submit(failing_coro(), on_exception=captured.append)

    await supervisor.drain()

    assert len(captured) == 1
    assert isinstance(captured[0], ValueError)
    assert str(captured[0]) == "boom"


@pytest.mark.anyio
async def test_supervisor_pending_tasks_cleared_after_completion():
    """In-flight task count must return to zero after all submitted coros complete."""
    supervisor = DefaultBackgroundSupervisor(audit=None, log=None)

    async def noop():
        pass

    supervisor.submit(noop())
    assert supervisor.pending_count == 1

    await supervisor.drain()

    assert supervisor.pending_count == 0
