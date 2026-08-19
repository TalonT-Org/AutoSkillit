"""Tests wiring the process-tether sweep and codex/daemon reap into lifespan boot gates."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


def _tether_report():
    from autoskillit.execution import TetherSweepReport

    return TetherSweepReport()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "boot_fn_name,extra_env",
    [
        ("_fleet_auto_gate_boot", {}),
        (
            "_food_truck_auto_gate_boot",
            {"AUTOSKILLIT_HEADLESS": "1", "AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS": "kitchen-core"},
        ),
        (
            "_skill_auto_gate_boot",
            {"AUTOSKILLIT_HEADLESS": "1", "AUTOSKILLIT_HEADLESS_AUTO_GATE": "1"},
        ),
    ],
)
async def test_boot_gate_sweeps_orphaned_tethers(boot_fn_name, extra_env, tool_ctx, monkeypatch):
    """Every boot gate — fleet, food-truck, and skill — sweeps orphaned tethers."""
    import importlib

    from autoskillit.pipeline.gate import DefaultGateState

    for key, value in extra_env.items():
        monkeypatch.setenv(key, value)
    tool_ctx.gate = DefaultGateState(enabled=False)
    tool_ctx.quota_refresh_task = None

    boot_fn = getattr(importlib.import_module("autoskillit.server._lifespan"), boot_fn_name)
    mock_sweep = AsyncMock(return_value=_tether_report())

    with (
        patch("autoskillit.server.tools.tools_kitchen._write_hook_config"),
        patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()),
        patch("autoskillit.server._lifespan.create_background_task", return_value=MagicMock()),
        patch("autoskillit.server._lifespan.register_active_kitchen"),
        patch("autoskillit.server._lifespan.sweep_orphaned_tethers_async", mock_sweep),
        patch("autoskillit.server._lifespan._reap_self_excluded_codex_and_daemon_orphans"),
        patch("autoskillit.server._lifespan.discover_campaign_state_files", return_value=[]),
    ):
        await boot_fn(tool_ctx)

    mock_sweep.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "boot_fn_name,extra_env",
    [
        ("_fleet_auto_gate_boot", {}),
        (
            "_food_truck_auto_gate_boot",
            {"AUTOSKILLIT_HEADLESS": "1", "AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS": "kitchen-core"},
        ),
    ],
)
async def test_fleet_and_food_truck_gates_reap_codex_and_daemon_orphans(
    boot_fn_name, extra_env, tool_ctx, monkeypatch
):
    """Fleet and food-truck gates promote the manual-only codex/daemon reapers to automatic."""
    import importlib

    from autoskillit.pipeline.gate import DefaultGateState

    for key, value in extra_env.items():
        monkeypatch.setenv(key, value)
    tool_ctx.gate = DefaultGateState(enabled=False)
    tool_ctx.quota_refresh_task = None

    boot_fn = getattr(importlib.import_module("autoskillit.server._lifespan"), boot_fn_name)
    mock_reap_helper = MagicMock()

    with (
        patch("autoskillit.server.tools.tools_kitchen._write_hook_config"),
        patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()),
        patch("autoskillit.server._lifespan.create_background_task", return_value=MagicMock()),
        patch("autoskillit.server._lifespan.register_active_kitchen"),
        patch(
            "autoskillit.server._lifespan.sweep_orphaned_tethers_async",
            new=AsyncMock(return_value=_tether_report()),
        ),
        patch(
            "autoskillit.server._lifespan._reap_self_excluded_codex_and_daemon_orphans",
            mock_reap_helper,
        ),
        patch("autoskillit.server._lifespan.discover_campaign_state_files", return_value=[]),
    ):
        await boot_fn(tool_ctx)

    mock_reap_helper.assert_called_once_with()


@pytest.mark.anyio
async def test_skill_gate_does_not_reap_codex_and_daemon_orphans(tool_ctx, monkeypatch):
    """SKILL sessions are short-lived — no codex/daemon reap, tether sweep only."""
    from autoskillit.pipeline.gate import DefaultGateState
    from autoskillit.server._lifespan import _skill_auto_gate_boot

    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS_AUTO_GATE", "1")
    tool_ctx.gate = DefaultGateState(enabled=False)
    tool_ctx.quota_refresh_task = None

    mock_reap_helper = MagicMock()

    with (
        patch("autoskillit.server.tools.tools_kitchen._write_hook_config"),
        patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()),
        patch("autoskillit.server._lifespan.register_active_kitchen"),
        patch(
            "autoskillit.server._lifespan.sweep_orphaned_tethers_async",
            new=AsyncMock(return_value=_tether_report()),
        ),
        patch(
            "autoskillit.server._lifespan._reap_self_excluded_codex_and_daemon_orphans",
            mock_reap_helper,
        ),
    ):
        await _skill_auto_gate_boot(tool_ctx)

    mock_reap_helper.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "boot_fn_name,extra_env",
    [
        ("_fleet_auto_gate_boot", {}),
        (
            "_food_truck_auto_gate_boot",
            {"AUTOSKILLIT_HEADLESS": "1", "AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS": "kitchen-core"},
        ),
        (
            "_skill_auto_gate_boot",
            {"AUTOSKILLIT_HEADLESS": "1", "AUTOSKILLIT_HEADLESS_AUTO_GATE": "1"},
        ),
    ],
)
async def test_boot_gate_fails_open_on_tether_sweep_error(
    boot_fn_name, extra_env, tool_ctx, monkeypatch
):
    """A tether-sweep failure never blocks gate activation (fail-open, like every other step)."""
    import importlib

    from autoskillit.pipeline.gate import DefaultGateState

    for key, value in extra_env.items():
        monkeypatch.setenv(key, value)
    tool_ctx.gate = DefaultGateState(enabled=False)
    tool_ctx.quota_refresh_task = None

    boot_fn = getattr(importlib.import_module("autoskillit.server._lifespan"), boot_fn_name)

    with (
        patch("autoskillit.server.tools.tools_kitchen._write_hook_config"),
        patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()),
        patch("autoskillit.server._lifespan.create_background_task", return_value=MagicMock()),
        patch("autoskillit.server._lifespan.register_active_kitchen"),
        patch(
            "autoskillit.server._lifespan.sweep_orphaned_tethers_async",
            new=AsyncMock(side_effect=RuntimeError("tether sweep exploded")),
        ),
        patch("autoskillit.server._lifespan._reap_self_excluded_codex_and_daemon_orphans"),
        patch("autoskillit.server._lifespan.discover_campaign_state_files", return_value=[]),
    ):
        await boot_fn(tool_ctx)

    assert tool_ctx.gate.enabled is True


@pytest.mark.anyio
async def test_open_kitchen_handler_calls_tether_sweep_and_reaper():
    """open_kitchen's tether_sweep transition calls both the sweep and the self-excluded reap."""
    from tests.server.conftest import _make_mock_ctx

    mock_ctx = _make_mock_ctx()
    mock_sweep = AsyncMock(return_value=_tether_report())
    mock_reap_helper = MagicMock()

    with (
        patch("autoskillit.server._get_ctx", return_value=mock_ctx),
        patch("autoskillit.server.logger"),
        patch("autoskillit.server.tools.tools_kitchen._write_hook_config"),
        patch("autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()),
        patch("autoskillit.server.tools.tools_kitchen.sweep_orphaned_tethers_async", mock_sweep),
        patch(
            "autoskillit.server._lifespan._reap_self_excluded_codex_and_daemon_orphans",
            mock_reap_helper,
        ),
    ):
        from autoskillit.server.tools.tools_kitchen import _open_kitchen_handler

        await _open_kitchen_handler()

    mock_sweep.assert_awaited_once()
    mock_reap_helper.assert_called_once_with()
