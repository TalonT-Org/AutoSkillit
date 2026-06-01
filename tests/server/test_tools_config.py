"""Tests for configure_fleet and configure_order MCP tools."""

from __future__ import annotations

import json

import pytest

from autoskillit.config import AutomationConfig
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
    mock_ctx.config = AutomationConfig()

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

    hook_cfg_path = tmp_path.joinpath(*_HOOK_CONFIG_RELPATH)
    hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg_path.write_text(json.dumps({}))

    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path
    mock_ctx.fleet_lock = FleetSemaphore(max_concurrent=3)
    mock_ctx.config = AutomationConfig()

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
    mock_ctx.config = AutomationConfig()

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
    mock_ctx.config = AutomationConfig()

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
    mock_ctx.config = AutomationConfig()

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
    mock_ctx.config = AutomationConfig()

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
    mock_ctx.config = AutomationConfig()

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
    from autoskillit.server import _state

    hook_cfg_path = tmp_path.joinpath(*_HOOK_CONFIG_RELPATH)
    hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg_path.write_text(json.dumps({}))

    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path
    mock_ctx.fleet_lock = None
    mock_ctx.config = AutomationConfig()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_state, "_ctx", mock_ctx)

    from autoskillit.server.tools.tools_config import configure_fleet

    result = await configure_fleet(max_concurrent_dispatches=4)
    payload = json.loads(result)

    assert payload["success"] is True
    assert mock_ctx.fleet_lock.max_concurrent == 4
    assert mock_ctx.fleet_lock.timeout is None


@pytest.mark.anyio
async def test_configure_fleet_snapshot_matches_live_semaphore(tmp_path, monkeypatch) -> None:
    """Snapshot max_concurrent_dispatches must equal ctx.fleet_lock.max_concurrent."""
    from autoskillit.fleet._semaphore import FleetSemaphore
    from autoskillit.server import _state

    hook_cfg_path = tmp_path.joinpath(*_HOOK_CONFIG_RELPATH)
    hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg_path.write_text(json.dumps({}))

    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path
    mock_ctx.fleet_lock = FleetSemaphore(max_concurrent=3)
    mock_ctx.config = AutomationConfig()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_state, "_ctx", mock_ctx)

    from autoskillit.server.tools.tools_config import configure_fleet

    result = await configure_fleet(max_concurrent_dispatches=5)
    payload = json.loads(result)

    assert payload["success"] is True
    assert mock_ctx.fleet_lock.max_concurrent == 5
    assert payload["config"]["fleet"]["max_concurrent_dispatches"] == 5


@pytest.mark.anyio
async def test_configure_fleet_acquire_timeout_only_updates_semaphore(
    tmp_path, monkeypatch
) -> None:
    """acquire_timeout_sec-only call must update the live semaphore."""
    from autoskillit.fleet._semaphore import FleetSemaphore
    from autoskillit.server import _state

    hook_cfg_path = tmp_path.joinpath(*_HOOK_CONFIG_RELPATH)
    hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg_path.write_text(json.dumps({}))

    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path
    mock_ctx.fleet_lock = FleetSemaphore(max_concurrent=3)
    mock_ctx.config = AutomationConfig()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_state, "_ctx", mock_ctx)

    from autoskillit.server.tools.tools_config import configure_fleet

    result = await configure_fleet(acquire_timeout_sec=30.0)
    payload = json.loads(result)

    assert payload["success"] is True
    assert mock_ctx.fleet_lock.timeout == 30.0
    assert payload["config"]["fleet"]["acquire_timeout_sec"] == 30.0


@pytest.mark.anyio
async def test_configure_fleet_snapshot_reads_semaphore_not_overlay(tmp_path, monkeypatch) -> None:
    """Snapshot acquire_timeout_sec must reflect the live semaphore's carried-forward timeout."""
    from autoskillit.fleet._semaphore import FleetSemaphore
    from autoskillit.server import _state

    hook_cfg_path = tmp_path.joinpath(*_HOOK_CONFIG_RELPATH)
    hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg_path.write_text(json.dumps({}))

    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path
    mock_ctx.fleet_lock = FleetSemaphore(max_concurrent=3, timeout=60.0)
    mock_ctx.config = AutomationConfig()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_state, "_ctx", mock_ctx)

    from autoskillit.server.tools.tools_config import configure_fleet

    result = await configure_fleet(max_concurrent_dispatches=5)
    payload = json.loads(result)

    assert payload["success"] is True
    assert mock_ctx.fleet_lock.timeout == 60.0
    assert payload["config"]["fleet"]["acquire_timeout_sec"] == 60.0


@pytest.mark.anyio
async def test_configure_fleet_close_reopen_resets_semaphore_to_defaults(
    tmp_path, monkeypatch
) -> None:
    """After close/reopen, semaphore must return to config defaults."""
    from unittest.mock import patch

    from autoskillit.fleet._semaphore import FleetSemaphore
    from autoskillit.server import _state

    hook_cfg_path = tmp_path.joinpath(*_HOOK_CONFIG_RELPATH)
    hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg_path.write_text(json.dumps({}))

    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path
    mock_ctx.fleet_lock = FleetSemaphore(max_concurrent=3)
    mock_ctx.config = AutomationConfig()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_state, "_ctx", mock_ctx)

    from autoskillit.server.tools.tools_config import configure_fleet

    result = await configure_fleet(max_concurrent_dispatches=6)
    payload = json.loads(result)
    assert payload["success"] is True
    assert mock_ctx.fleet_lock.max_concurrent == 6

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.tools.tools_kitchen import _close_kitchen_handler

            _close_kitchen_handler()

    assert mock_ctx.fleet_lock.max_concurrent == mock_ctx.config.fleet.max_concurrent_dispatches

    hook_cfg_path.write_text(json.dumps({}))
    result2 = await configure_fleet()
    payload2 = json.loads(result2)
    assert payload2["success"] is True
    assert (
        payload2["config"]["fleet"]["max_concurrent_dispatches"]
        == mock_ctx.config.fleet.max_concurrent_dispatches
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "calls",
    [
        [{"max_concurrent_dispatches": 4}],
        [{"acquire_timeout_sec": 15.0}],
        [{"max_concurrent_dispatches": 4, "acquire_timeout_sec": 15.0}],
        [{"max_concurrent_dispatches": 4}, {"acquire_timeout_sec": 25.0}],
    ],
    ids=["max_only", "timeout_only", "both", "sequential"],
)
async def test_configure_fleet_snapshot_semaphore_invariant(tmp_path, monkeypatch, calls) -> None:
    """Snapshot must always match the live semaphore for semaphore-managed fields."""
    from autoskillit.fleet._semaphore import FleetSemaphore
    from autoskillit.server import _state

    hook_cfg_path = tmp_path.joinpath(*_HOOK_CONFIG_RELPATH)
    hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg_path.write_text(json.dumps({}))

    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path
    mock_ctx.fleet_lock = FleetSemaphore(max_concurrent=3)
    mock_ctx.config = AutomationConfig()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_state, "_ctx", mock_ctx)

    from autoskillit.server.tools.tools_config import configure_fleet

    payload = None
    for call_kwargs in calls:
        result = await configure_fleet(**call_kwargs)
        payload = json.loads(result)
        assert payload["success"] is True

    assert payload is not None
    assert (
        payload["config"]["fleet"]["max_concurrent_dispatches"]
        == mock_ctx.fleet_lock.max_concurrent
    )
    if mock_ctx.fleet_lock.timeout is not None:
        assert payload["config"]["fleet"]["acquire_timeout_sec"] == mock_ctx.fleet_lock.timeout
    else:
        assert (
            payload["config"]["fleet"]["acquire_timeout_sec"]
            == mock_ctx.config.fleet.acquire_timeout_sec
        )
