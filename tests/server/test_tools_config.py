"""Tests for configure_fleet and configure_order MCP tools."""

from __future__ import annotations

import json

import pytest

from tests.server.conftest import _make_mock_ctx

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


_HOOK_CONFIG_RELPATH = (".autoskillit", "temp", ".hook_config.json")


@pytest.mark.anyio
async def test_configure_fleet_writes_overlay(tmp_path, monkeypatch) -> None:
    """configure_fleet writes fleet params to overlay and returns snapshot."""
    from autoskillit.server import _state
    from tests.server._helpers import _HOOK_CONFIG_OVERLAY_RELPATH

    hook_cfg_path = tmp_path.joinpath(*_HOOK_CONFIG_RELPATH)
    hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg_path.write_text(json.dumps({}))

    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_state, "_ctx", mock_ctx)

    from autoskillit.server.tools.tools_config import configure_fleet

    result = await configure_fleet(max_concurrent_dispatches=5, max_total_issues=20)
    payload = json.loads(result)

    assert payload["success"] is True
    overlay_path = tmp_path.joinpath(*_HOOK_CONFIG_OVERLAY_RELPATH)
    overlay = json.loads(overlay_path.read_text())
    assert overlay["fleet"]["max_concurrent_dispatches"] == 5
    assert overlay["fleet"]["max_total_issues"] == 20
    assert hook_cfg_path.read_text() == "{}"
    assert "max_concurrent_dispatches" in payload["config"]["fleet"]
    assert "max_total_issues" in payload["config"]["fleet"]


@pytest.mark.anyio
async def test_configure_fleet_replaces_semaphore(tmp_path, monkeypatch) -> None:
    """configure_fleet replaces ctx.fleet_lock with resized FleetSemaphore."""
    from autoskillit.fleet._semaphore import FleetSemaphore
    from autoskillit.server import _state
    from tests.server._helpers import _HOOK_CONFIG_OVERLAY_RELPATH

    hook_cfg_path = tmp_path.joinpath(*_HOOK_CONFIG_RELPATH)
    hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg_path.write_text(json.dumps({}))

    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path
    mock_ctx.fleet_lock = FleetSemaphore(max_concurrent=3)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_state, "_ctx", mock_ctx)

    from autoskillit.server.tools.tools_config import configure_fleet

    result = await configure_fleet(max_concurrent_dispatches=6)
    payload = json.loads(result)

    assert payload["success"] is True
    assert mock_ctx.fleet_lock.max_concurrent == 6


@pytest.mark.anyio
async def test_configure_fleet_preserves_existing_overlay(tmp_path, monkeypatch) -> None:
    """configure_fleet merges into existing overlay without clobbering."""
    from autoskillit.server import _state
    from tests.server._helpers import _HOOK_CONFIG_OVERLAY_RELPATH

    hook_cfg_path = tmp_path.joinpath(*_HOOK_CONFIG_RELPATH)
    hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg_path.write_text(json.dumps({}))

    overlay_path = tmp_path.joinpath(*_HOOK_CONFIG_OVERLAY_RELPATH)
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    overlay_path.write_text(json.dumps({"quota_guard": {"disabled": True}}))

    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_state, "_ctx", mock_ctx)

    from autoskillit.server.tools.tools_config import configure_fleet

    result = await configure_fleet(max_total_issues=8)
    payload = json.loads(result)

    assert payload["success"] is True
    overlay = json.loads(overlay_path.read_text())
    assert overlay["quota_guard"]["disabled"] is True
    assert overlay["fleet"]["max_total_issues"] == 8


@pytest.mark.anyio
async def test_configure_fleet_denies_headless(tmp_path, monkeypatch) -> None:
    """configure_fleet rejects headless sessions."""
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    from autoskillit.server.tools.tools_config import configure_fleet

    result = await configure_fleet(max_concurrent_dispatches=5)
    payload = json.loads(result)
    assert payload["success"] is False


@pytest.mark.anyio
async def test_configure_fleet_returns_error_when_kitchen_not_open(tmp_path, monkeypatch) -> None:
    """configure_fleet returns error when kitchen is not open."""
    from autoskillit.server import _state

    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_state, "_ctx", mock_ctx)

    from autoskillit.server.tools.tools_config import configure_fleet

    result = await configure_fleet(max_concurrent_dispatches=5)
    payload = json.loads(result)
    assert payload["success"] is False
    assert "not open" in payload["error"]


@pytest.mark.anyio
async def test_configure_order_writes_overlay(tmp_path, monkeypatch) -> None:
    """configure_order writes order + core params to overlay and returns snapshot."""
    from autoskillit.server import _state
    from tests.server._helpers import _HOOK_CONFIG_OVERLAY_RELPATH

    hook_cfg_path = tmp_path.joinpath(*_HOOK_CONFIG_RELPATH)
    hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg_path.write_text(json.dumps({}))

    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_state, "_ctx", mock_ctx)

    from autoskillit.server.tools.tools_config import configure_order

    result = await configure_order(timeout=3600, default_model="opus")
    payload = json.loads(result)

    assert payload["success"] is True
    overlay_path = tmp_path.joinpath(*_HOOK_CONFIG_OVERLAY_RELPATH)
    overlay = json.loads(overlay_path.read_text())
    assert overlay["order"]["timeout"] == 3600
    assert overlay["core"]["default_model"] == "opus"
    assert "timeout" in payload["config"]["order"]


@pytest.mark.anyio
async def test_configure_order_denies_headless(tmp_path, monkeypatch) -> None:
    """configure_order rejects headless sessions."""
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    from autoskillit.server.tools.tools_config import configure_order

    result = await configure_order(timeout=3600)
    payload = json.loads(result)
    assert payload["success"] is False


@pytest.mark.anyio
async def test_configure_fleet_accumulates_across_calls(tmp_path, monkeypatch) -> None:
    """configure_fleet accumulates params across multiple calls."""
    from autoskillit.server import _state
    from tests.server._helpers import _HOOK_CONFIG_OVERLAY_RELPATH

    hook_cfg_path = tmp_path.joinpath(*_HOOK_CONFIG_RELPATH)
    hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg_path.write_text(json.dumps({}))

    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_state, "_ctx", mock_ctx)

    from autoskillit.server.tools.tools_config import configure_fleet

    await configure_fleet(max_concurrent_dispatches=5)
    await configure_fleet(max_total_issues=20)

    overlay_path = tmp_path.joinpath(*_HOOK_CONFIG_OVERLAY_RELPATH)
    overlay = json.loads(overlay_path.read_text())
    assert overlay["fleet"]["max_concurrent_dispatches"] == 5
    assert overlay["fleet"]["max_total_issues"] == 20


@pytest.mark.anyio
async def test_configure_fleet_validates_ceiling(tmp_path, monkeypatch) -> None:
    """configure_fleet rejects max_concurrent_dispatches above ceiling."""
    from autoskillit.server import _state

    hook_cfg_path = tmp_path.joinpath(*_HOOK_CONFIG_RELPATH)
    hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg_path.write_text(json.dumps({}))

    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_state, "_ctx", mock_ctx)

    from autoskillit.server.tools.tools_config import configure_fleet

    result = await configure_fleet(max_concurrent_dispatches=9)
    payload = json.loads(result)
    assert payload["success"] is False
    assert "must be between" in payload["error"]


@pytest.mark.anyio
async def test_configure_fleet_no_params_returns_defaults(tmp_path, monkeypatch) -> None:
    """configure_fleet with no params returns full snapshot with defaults."""
    from autoskillit.server import _state

    hook_cfg_path = tmp_path.joinpath(*_HOOK_CONFIG_RELPATH)
    hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg_path.write_text(json.dumps({}))

    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_state, "_ctx", mock_ctx)

    from autoskillit.server.tools.tools_config import configure_fleet

    result = await configure_fleet()
    payload = json.loads(result)

    assert payload["success"] is True
    assert "max_concurrent_dispatches" in payload["config"]["fleet"]
    assert "max_total_issues" in payload["config"]["fleet"]


@pytest.mark.anyio
async def test_configure_order_no_params_returns_defaults(tmp_path, monkeypatch) -> None:
    """configure_order with no params returns full snapshot with defaults."""
    from autoskillit.server import _state

    hook_cfg_path = tmp_path.joinpath(*_HOOK_CONFIG_RELPATH)
    hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg_path.write_text(json.dumps({}))

    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_state, "_ctx", mock_ctx)

    from autoskillit.server.tools.tools_config import configure_order

    result = await configure_order()
    payload = json.loads(result)

    assert payload["success"] is True
    assert "timeout" in payload["config"]["order"]


@pytest.mark.anyio
async def test_configure_fleet_semaphore_null_fleet_lock(tmp_path, monkeypatch) -> None:
    """configure_fleet creates new semaphore when fleet_lock is None."""
    from autoskillit.fleet._semaphore import FleetSemaphore
    from autoskillit.server import _state

    hook_cfg_path = tmp_path.joinpath(*_HOOK_CONFIG_RELPATH)
    hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg_path.write_text(json.dumps({}))

    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path
    mock_ctx.fleet_lock = None

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_state, "_ctx", mock_ctx)

    from autoskillit.server.tools.tools_config import configure_fleet

    result = await configure_fleet(max_concurrent_dispatches=4)
    payload = json.loads(result)

    assert payload["success"] is True
    assert mock_ctx.fleet_lock.max_concurrent == 4
    assert mock_ctx.fleet_lock.timeout is None
