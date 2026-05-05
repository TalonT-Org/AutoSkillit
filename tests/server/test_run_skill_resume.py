"""Tests for run_skill resume_session_id parameter threading (T4)."""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_execution import run_skill

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_resume_session_id_threaded_to_executor(tool_ctx, monkeypatch) -> None:
    """resume_session_id flows from run_skill → executor.run()."""
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

    await run_skill("/implement foo", "/tmp", resume_session_id="sess-123")

    assert len(executor.calls) == 1
    assert executor.calls[0].resume_session_id == "sess-123"


@pytest.mark.anyio
async def test_resume_skips_skill_command_validation(tool_ctx, monkeypatch) -> None:
    """When resume_session_id is set, non-slash skill_command is allowed."""
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

    result = await run_skill(
        "Continue from where you left off",
        "/tmp",
        resume_session_id="sess-123",
    )
    data = json.loads(result)
    assert data["success"] is True  # not rejected by _validate_skill_command


@pytest.mark.anyio
async def test_no_resume_still_validates_skill_command(tool_ctx, monkeypatch) -> None:
    """Without resume_session_id, non-slash skill_command is still rejected."""
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

    result = await run_skill("Continue from where you left off", "/tmp")
    data = json.loads(result)
    assert data["success"] is False
    assert (
        "slash" in data.get("error", "").lower()
        or "skill_command" in data.get("result", "").lower()
    )
