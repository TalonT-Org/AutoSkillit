"""Tests for allowed_write_prefix computation in run_skill — decoupled from read_only."""

from __future__ import annotations

import pytest

from autoskillit.server.tools.tools_execution import run_skill

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_allowed_write_prefix_set_from_output_dir_even_when_not_read_only(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """allowed_write_prefix is set from output_dir even for non-read-only skills."""
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    output_dir = str(tmp_path / "planner" / "run-xyz")
    await run_skill("/test planner-skill", str(tmp_path), output_dir=output_dir)

    assert len(executor.calls) == 1
    assert executor.calls[0].allowed_write_prefix == output_dir + "/"


@pytest.mark.anyio
async def test_allowed_write_prefix_uses_fallback_without_output_dir(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """When no output_dir is given, fallback computes prefix from skill name."""
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/test skill", str(tmp_path))

    assert len(executor.calls) == 1
    expected = str(tmp_path / ".autoskillit" / "temp" / "test") + "/"
    assert executor.calls[0].allowed_write_prefix == expected
