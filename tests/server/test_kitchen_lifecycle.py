import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.core.types._type_plugin_source import DirectInstall
from autoskillit.hooks import _HOOK_CONFIG_PATH_COMPONENTS
from autoskillit.server import _state
from autoskillit.server._factory import make_context
from autoskillit.server.tools.tools_kitchen import _close_kitchen_handler, _open_kitchen_handler

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


async def test_kitchen_open_close_lifecycle(monkeypatch, tmp_path):
    """Gate: disabled→enabled→disabled; hook_config written then removed; task cancelled."""
    monkeypatch.chdir(tmp_path)

    ctx = make_context(
        AutomationConfig(),
        runner=None,
        plugin_source=DirectInstall(plugin_dir=tmp_path),
        project_dir=tmp_path,
    )
    monkeypatch.setattr(_state, "_ctx", ctx)
    monkeypatch.setattr(_state, "_startup_ready", None)

    hook_config_path = tmp_path.joinpath(*_HOOK_CONFIG_PATH_COMPONENTS)

    with (
        patch("autoskillit.server.tools.tools_kitchen._prime_quota_cache", new_callable=AsyncMock),
        patch("autoskillit.core.register_active_kitchen"),
        patch("autoskillit.core.unregister_active_kitchen"),
    ):
        # initial state
        assert ctx.gate.enabled is False

        # open kitchen
        result = await _open_kitchen_handler()
        assert result is None  # no failure envelope

        assert ctx.gate.enabled is True
        assert hook_config_path.exists()
        data = json.loads(hook_config_path.read_text())
        assert "quota_guard" in data
        assert "kitchen_id" in data
        task = ctx.quota_refresh_task
        assert task is not None

        # close kitchen
        _close_kitchen_handler()

        assert ctx.gate.enabled is False
        assert not hook_config_path.exists()
        assert ctx.quota_refresh_task is None

    # drain the cancelled task from the event loop
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()


async def test_open_kitchen_runs_reaper(monkeypatch, tmp_path):
    """Test 1C: _open_kitchen_handler must call reap_stale_dispatches_async.

    Interactive sessions need the reaper at kitchen-open time since they
    never go through _fleet_auto_gate_boot or _food_truck_auto_gate_boot.
    """
    monkeypatch.chdir(tmp_path)

    ctx = make_context(
        AutomationConfig(),
        runner=None,
        plugin_source=DirectInstall(plugin_dir=tmp_path),
        project_dir=tmp_path,
    )
    monkeypatch.setattr(_state, "_ctx", ctx)
    monkeypatch.setattr(_state, "_startup_ready", None)

    fake_state_path = tmp_path / "dispatches" / "campaign.json"

    reaper_called = AsyncMock()

    monkeypatch.setattr(
        "autoskillit.server.tools.tools_kitchen.discover_campaign_state_files",
        lambda _project_dir: [fake_state_path],
    )
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_kitchen.reap_stale_dispatches_async",
        reaper_called,
    )

    with (
        patch("autoskillit.server.tools.tools_kitchen._prime_quota_cache", new_callable=AsyncMock),
        patch("autoskillit.core.register_active_kitchen"),
    ):
        result = await _open_kitchen_handler()
        assert result is None
        reaper_called.assert_awaited_once()
        call_args = reaper_called.await_args
        assert call_args is not None
        assert list(call_args.args[0]) == [fake_state_path]


async def test_close_kitchen_removes_tracker_dir(monkeypatch, tmp_path):
    """Tracker directory is removed by close_kitchen handler."""
    monkeypatch.chdir(tmp_path)

    ctx = make_context(
        AutomationConfig(),
        runner=None,
        plugin_source=DirectInstall(plugin_dir=tmp_path),
        project_dir=tmp_path,
    )
    monkeypatch.setattr(_state, "_ctx", ctx)
    monkeypatch.setattr(_state, "_startup_ready", None)

    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    tracker_dir.mkdir(parents=True)
    (tracker_dir / "AB.json").write_text('{"pipeline_id": "AB", "steps": {}}')

    with (
        patch("autoskillit.server.tools.tools_kitchen._prime_quota_cache", new_callable=AsyncMock),
        patch("autoskillit.core.register_active_kitchen"),
        patch("autoskillit.core.unregister_active_kitchen"),
    ):
        await _open_kitchen_handler()
        _close_kitchen_handler()

    assert not tracker_dir.exists()


async def test_back_to_back_open_close_open_resets_infrastructure(monkeypatch, tmp_path):
    """After close, gate_infrastructure_ready must be False so a re-open runs the handler."""
    monkeypatch.chdir(tmp_path)

    ctx = make_context(
        AutomationConfig(),
        runner=None,
        plugin_source=DirectInstall(plugin_dir=tmp_path),
        project_dir=tmp_path,
    )
    monkeypatch.setattr(_state, "_ctx", ctx)
    monkeypatch.setattr(_state, "_startup_ready", None)

    with (
        patch("autoskillit.server.tools.tools_kitchen._prime_quota_cache", new_callable=AsyncMock),
        patch("autoskillit.core.register_active_kitchen"),
        patch("autoskillit.core.unregister_active_kitchen"),
    ):
        result1 = await _open_kitchen_handler()
        assert result1 is None
        assert ctx.gate_infrastructure_ready is True
        first_task = ctx.quota_refresh_task

        _close_kitchen_handler()
        assert ctx.gate_infrastructure_ready is False
        await asyncio.sleep(0)
        assert first_task.cancelled() or first_task.done()

        result2 = await _open_kitchen_handler()
        assert result2 is None
        assert ctx.gate_infrastructure_ready is True
        second_task = ctx.quota_refresh_task
        assert second_task is not first_task

        _close_kitchen_handler()
        await asyncio.sleep(0)
        assert second_task.cancelled() or second_task.done()
