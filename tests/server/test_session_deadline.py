"""Tests for child-only session deadline propagation."""

from __future__ import annotations

import json
import os
import time
from unittest.mock import AsyncMock, patch

import pytest

from tests.fakes import InMemoryHeadlessExecutor

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


def _move_open_context(tool_ctx, project_dir) -> None:
    tool_ctx.project_dir = project_dir
    hook_path = project_dir / ".autoskillit" / "temp" / ".hook_config.json"
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text("{}")


@pytest.mark.anyio
async def test_reconfiguration_recomputes_order_deadline_without_environment_cache(
    tool_ctx_kitchen_open,
    tmp_path,
    monkeypatch,
) -> None:
    from autoskillit.server import _state
    from autoskillit.server.tools.tools_config import configure_order
    from autoskillit.server.tools.tools_execution import run_skill

    monkeypatch.delenv("AUTOSKILLIT_SESSION_DEADLINE", raising=False)
    monkeypatch.setattr(_state, "_ctx", tool_ctx_kitchen_open)
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    _move_open_context(tool_ctx_kitchen_open, tmp_path)

    assert json.loads(await configure_order(timeout=180))["success"] is True
    first_start = time.time()
    await run_skill("/test skill", str(tmp_path))
    assert json.loads(await configure_order(timeout=360))["success"] is True
    second_start = time.time()
    await run_skill("/test skill", str(tmp_path))

    first = float(executor.calls[0].provider_extras["AUTOSKILLIT_SESSION_DEADLINE"])
    second = float(executor.calls[1].provider_extras["AUTOSKILLIT_SESSION_DEADLINE"])
    assert first_start + 175 <= first <= first_start + 185
    assert second_start + 355 <= second <= second_start + 365
    assert second > first
    assert "AUTOSKILLIT_SESSION_DEADLINE" not in os.environ


@pytest.mark.anyio
async def test_valid_inherited_deadline_wins_without_server_environment_mutation(
    tool_ctx_kitchen_open,
    tmp_path,
    monkeypatch,
) -> None:
    from autoskillit.server import _state
    from autoskillit.server.tools.tools_config import configure_order
    from autoskillit.server.tools.tools_execution import run_skill

    inherited = "1700000000"
    monkeypatch.setenv("AUTOSKILLIT_SESSION_DEADLINE", inherited)
    monkeypatch.setattr(_state, "_ctx", tool_ctx_kitchen_open)
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    _move_open_context(tool_ctx_kitchen_open, tmp_path)

    await configure_order(timeout=180)
    await run_skill("/test skill", str(tmp_path))

    assert executor.calls[0].provider_extras["AUTOSKILLIT_SESSION_DEADLINE"] == inherited
    assert os.environ["AUTOSKILLIT_SESSION_DEADLINE"] == inherited


@pytest.mark.anyio
async def test_invalid_inherited_deadline_falls_back_to_effective_timeout(
    tool_ctx_kitchen_open,
    tmp_path,
    monkeypatch,
) -> None:
    from autoskillit.server import _state
    from autoskillit.server.tools.tools_config import configure_order
    from autoskillit.server.tools.tools_execution import run_skill

    monkeypatch.setenv("AUTOSKILLIT_SESSION_DEADLINE", "invalid")
    monkeypatch.setattr(_state, "_ctx", tool_ctx_kitchen_open)
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    _move_open_context(tool_ctx_kitchen_open, tmp_path)

    await configure_order(timeout=240)
    started = time.time()
    await run_skill("/test skill", str(tmp_path))

    deadline = float(executor.calls[0].provider_extras["AUTOSKILLIT_SESSION_DEADLINE"])
    assert started + 235 <= deadline <= started + 245
    assert os.environ["AUTOSKILLIT_SESSION_DEADLINE"] == "invalid"


@pytest.mark.anyio
async def test_close_reopen_rebuilds_deadline_from_new_configuration(
    tool_ctx_kitchen_open,
    tmp_path,
    monkeypatch,
) -> None:
    from autoskillit.server import _state
    from autoskillit.server.tools.tools_config import configure_order
    from autoskillit.server.tools.tools_execution import run_skill
    from autoskillit.server.tools.tools_kitchen import (
        _close_kitchen_handler,
        _open_kitchen_handler,
    )

    monkeypatch.delenv("AUTOSKILLIT_SESSION_DEADLINE", raising=False)
    monkeypatch.setattr(_state, "_ctx", tool_ctx_kitchen_open)
    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    _move_open_context(tool_ctx_kitchen_open, tmp_path)

    assert json.loads(await configure_order(timeout=180))["success"] is True
    first_start = time.time()
    await run_skill("/test skill", str(tmp_path))

    with (
        patch("autoskillit.server.tools.tools_kitchen._prime_quota_cache", new_callable=AsyncMock),
        patch("autoskillit.core.register_active_kitchen"),
        patch("autoskillit.core.unregister_active_kitchen"),
    ):
        _close_kitchen_handler()
        assert await _open_kitchen_handler() is None

    assert json.loads(await configure_order(timeout=360))["success"] is True
    second_start = time.time()
    await run_skill("/test skill", str(tmp_path))

    first = float(executor.calls[0].provider_extras["AUTOSKILLIT_SESSION_DEADLINE"])
    second = float(executor.calls[1].provider_extras["AUTOSKILLIT_SESSION_DEADLINE"])
    assert first_start + 175 <= first <= first_start + 185
    assert second_start + 355 <= second <= second_start + 365
    assert "AUTOSKILLIT_SESSION_DEADLINE" not in os.environ
