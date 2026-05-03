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
