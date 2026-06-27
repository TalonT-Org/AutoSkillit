"""Tests for the stale-path guard in run_skill.

Closes the fail-open path where init_session() returns a ValidatedAddDir
pointing to a /dev/shm path that no longer exists (reclaimed by the OS or
cleaned up by cleanup_stale()). The guard must crash-close with subtype
"crashed" and never reach the executor.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from autoskillit.core import ValidatedAddDir
from autoskillit.server.tools.tools_execution import run_skill
from tests.fakes import InMemoryHeadlessExecutor

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_stale_session_path_returns_crashed(tool_ctx_kitchen_open, tmp_path) -> None:
    """init_session returning a nonexistent path must crash-close before executor."""
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor

    # init_session returns a ValidatedAddDir whose path does NOT exist on disk.
    stale_path = tmp_path / "gone"
    mock_mgr = MagicMock()
    mock_mgr.init_session.return_value = ValidatedAddDir(path=str(stale_path))
    mock_mgr.compute_skill_closure.return_value = frozenset()
    tool_ctx_kitchen_open.session_skill_manager = mock_mgr

    result = await run_skill("/autoskillit:investigate foo", str(tmp_path))
    data = json.loads(result)

    assert data["success"] is False
    assert data["subtype"] == "crashed"
    assert executor.calls == []


@pytest.mark.anyio
async def test_valid_session_path_proceeds_to_executor(tool_ctx_kitchen_open, tmp_path) -> None:
    """A real on-disk session path must reach the executor."""
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor

    session_dir = tmp_path / "session"
    session_dir.mkdir()

    mock_mgr = MagicMock()
    mock_mgr.init_session.return_value = ValidatedAddDir(path=str(session_dir))
    mock_mgr.compute_skill_closure.return_value = frozenset()
    tool_ctx_kitchen_open.session_skill_manager = mock_mgr

    await run_skill("/autoskillit:investigate foo", str(tmp_path))

    assert len(executor.calls) == 1
