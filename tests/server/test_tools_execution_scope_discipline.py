"""Tests for scope_discipline_skill wiring in run_skill dispatch (#4478 review).

The delivery decision lives in the skill contract (`scope_discipline: true`); these
tests pin the fresh-dispatch path that resolves it — the backend-level delivery tests
exercise the flag directly and cannot catch a dispatch layer that never sets it.
"""

from __future__ import annotations

import pytest

from autoskillit.server.tools.tools_execution import run_skill

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_fresh_dispatch_sets_scope_discipline_from_contract(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """A fresh dispatch of a change-authoring skill resolves scope_discipline=True."""
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    investigation = tmp_path / "investigation.md"
    investigation.write_text("# Investigation: x\n")
    result = await run_skill(f"/autoskillit:rectify {investigation}", str(tmp_path))

    assert len(executor.calls) == 1, result
    assert executor.calls[0].scope_discipline_skill is True


@pytest.mark.anyio
async def test_fresh_dispatch_of_non_authoring_skill_stays_unscoped(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
) -> None:
    """A skill without scope_discipline in its contract dispatches with the flag off."""
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

    await run_skill("/autoskillit:investigate regression", str(tmp_path))

    assert len(executor.calls) == 1
    assert executor.calls[0].scope_discipline_skill is False
