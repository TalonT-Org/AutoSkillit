"""Tests for fleet and food-truck lifespan auto-gate boot functions."""

from __future__ import annotations

import pytest
import structlog.testing

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


@pytest.mark.feature("fleet")
class TestFleetAutoGateBoot:
    """Fleet lifespan auto-gate: _fleet_auto_gate_boot opens gate before first tool call."""

    @pytest.mark.anyio
    async def test_fleet_lifespan_auto_opens_gate(self, tool_ctx):
        """Fleet session: gate is open after _fleet_auto_gate_boot() runs."""
        import os
        from unittest.mock import AsyncMock, MagicMock, patch

        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _fleet_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        tool_ctx.quota_refresh_task = None

        with patch(
            "autoskillit.server.tools.tools_kitchen._write_hook_config"
        ) as mock_write_hook_config:
            with patch(
                "autoskillit.server._misc._prime_quota_cache", new=AsyncMock()
            ) as mock_prime_quota_cache:
                with patch(
                    "autoskillit.server._lifespan.create_background_task",
                    return_value=MagicMock(),
                ) as mock_create_bg_task:
                    with patch(
                        "autoskillit.server._lifespan.register_active_kitchen"
                    ) as mock_register_kitchen:
                        await _fleet_auto_gate_boot(tool_ctx)

        assert tool_ctx.gate.enabled is True
        assert tool_ctx.kitchen_id is not None
        assert tool_ctx.active_recipe_packs == frozenset()
        mock_write_hook_config.assert_called_once_with()
        mock_prime_quota_cache.assert_awaited_once_with()
        mock_create_bg_task.assert_called_once()
        mock_register_kitchen.assert_called_once_with(
            tool_ctx.kitchen_id, os.getpid(), str(tool_ctx.project_dir)
        )

    @pytest.mark.anyio
    async def test_fleet_auto_gate_boot_passes_campaign_id_to_reaper(
        self, tool_ctx, monkeypatch, tmp_path
    ) -> None:
        """Fleet boot passes own_campaign_id=ctx.kitchen_id (not None) to the reaper."""
        import json as _json
        from unittest.mock import AsyncMock, MagicMock, patch

        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _fleet_auto_gate_boot

        dispatches_dir = tmp_path / ".autoskillit" / "temp" / "dispatches"
        dispatches_dir.mkdir(parents=True)
        (dispatches_dir / "campaign1.json").write_text(_json.dumps({}))
        tool_ctx.gate = DefaultGateState(enabled=False)
        tool_ctx.quota_refresh_task = None
        tool_ctx.project_dir = tmp_path
        tool_ctx.github_client = AsyncMock()

        mock_reap = AsyncMock()

        with (
            patch("autoskillit.server.tools.tools_kitchen._write_hook_config"),
            patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()),
            patch("autoskillit.server._lifespan.create_background_task", return_value=MagicMock()),
            patch("autoskillit.server._lifespan.register_active_kitchen"),
            patch(
                "autoskillit.server._lifespan.discover_campaign_state_files",
                return_value=[dispatches_dir / "campaign1.json"],
            ),
            patch("autoskillit.server._lifespan.reap_stale_dispatches_async", mock_reap),
        ):
            await _fleet_auto_gate_boot(tool_ctx)

        _call = mock_reap.call_args
        assert _call is not None, "reap_stale_dispatches_async was not called"
        assert _call.kwargs.get("own_campaign_id") is not None
        assert _call.kwargs["own_campaign_id"] == tool_ctx.kitchen_id


@pytest.mark.feature("fleet")
class TestFleetAutoGateBootProjectDir:
    """Regression tests: _fleet_auto_gate_boot must use ctx.project_dir, not Path.cwd()."""

    @pytest.mark.anyio
    async def test_fleet_auto_gate_boot_uses_project_dir_for_kitchen_registration(
        self, build_ctx, tmp_path, monkeypatch
    ):
        """register_active_kitchen must be called with ctx.project_dir, not Path.cwd()."""
        import os
        from unittest.mock import AsyncMock, MagicMock, patch

        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _fleet_auto_gate_boot

        monkeypatch.chdir(tmp_path)  # Ensure cwd != project_dir
        different_dir = tmp_path / "project_root"
        different_dir.mkdir()

        ctx = build_ctx(project_dir=different_dir)
        ctx.gate = DefaultGateState(enabled=False)
        ctx.quota_refresh_task = None

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch(
                    "autoskillit.server._lifespan.create_background_task",
                    return_value=MagicMock(),
                ):
                    with patch(
                        "autoskillit.server._lifespan.register_active_kitchen"
                    ) as mock_register_kitchen:
                        await _fleet_auto_gate_boot(ctx)

        mock_register_kitchen.assert_called_once_with(
            ctx.kitchen_id, os.getpid(), str(different_dir)
        )

    @pytest.mark.anyio
    async def test_food_truck_auto_gate_boot_uses_project_dir_for_kitchen_registration(
        self, build_ctx, tmp_path, monkeypatch
    ):
        """register_active_kitchen must be called with ctx.project_dir, not Path.cwd()."""
        import os
        from unittest.mock import AsyncMock, MagicMock, patch

        from autoskillit.core import FOOD_TRUCK_TOOL_TAGS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _food_truck_auto_gate_boot

        monkeypatch.chdir(tmp_path)
        different_dir = tmp_path / "project_root"
        different_dir.mkdir()

        ctx = build_ctx(project_dir=different_dir)
        ctx.gate = DefaultGateState(enabled=False)
        ctx.quota_refresh_task = None
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv(FOOD_TRUCK_TOOL_TAGS_ENV_VAR, "kitchen-core")

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch(
                    "autoskillit.server._lifespan.create_background_task",
                    return_value=MagicMock(),
                ):
                    with patch(
                        "autoskillit.server._lifespan.register_active_kitchen"
                    ) as mock_register_kitchen:
                        await _food_truck_auto_gate_boot(ctx)

        mock_register_kitchen.assert_called_once_with(
            ctx.kitchen_id, os.getpid(), str(different_dir)
        )

    @pytest.mark.anyio
    async def test_fleet_lifespan_auto_gate_fails_open_on_hook_config_error(self, tool_ctx):
        """Fleet auto-gate keeps gate open even when _write_hook_config() raises."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _fleet_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        tool_ctx.quota_refresh_task = None

        with patch(
            "autoskillit.server.tools.tools_kitchen._write_hook_config",
            side_effect=OSError("disk full"),
        ):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch(
                    "autoskillit.server._lifespan.create_background_task",
                    return_value=MagicMock(),
                ):
                    with patch("autoskillit.server._lifespan.register_active_kitchen"):
                        await _fleet_auto_gate_boot(tool_ctx)

        assert tool_ctx.gate.enabled is True  # gate stays open despite hook_config failure

    @pytest.mark.anyio
    async def test_fleet_lifespan_auto_gate_fails_open_on_quota_cache_error(self, tool_ctx):
        """Fleet auto-gate keeps gate open even when _prime_quota_cache() raises."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _fleet_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        tool_ctx.quota_refresh_task = None

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch(
                "autoskillit.server._misc._prime_quota_cache",
                new=AsyncMock(side_effect=RuntimeError("quota cache error")),
            ):
                with patch(
                    "autoskillit.server._lifespan.create_background_task",
                    return_value=MagicMock(),
                ):
                    with patch("autoskillit.server._lifespan.register_active_kitchen"):
                        await _fleet_auto_gate_boot(tool_ctx)

        assert tool_ctx.gate.enabled is True  # gate stays open despite quota cache failure

    @pytest.mark.anyio
    async def test_fleet_lifespan_auto_gate_fails_open_on_background_task_error(self, tool_ctx):
        """Fleet auto-gate keeps gate open even when create_background_task() raises."""
        from unittest.mock import AsyncMock, patch

        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _fleet_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        tool_ctx.quota_refresh_task = None

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch(
                    "autoskillit.server._lifespan.create_background_task",
                    side_effect=RuntimeError("task creation error"),
                ):
                    with patch("autoskillit.server._lifespan.register_active_kitchen"):
                        await _fleet_auto_gate_boot(tool_ctx)

        assert tool_ctx.gate.enabled is True  # gate stays open despite background task failure

    @pytest.mark.anyio
    async def test_fleet_lifespan_auto_gate_fails_open_on_registry_error(self, tool_ctx):
        """Fleet auto-gate keeps gate open even when register_active_kitchen() raises."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _fleet_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        tool_ctx.quota_refresh_task = None

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch(
                    "autoskillit.server._lifespan.create_background_task",
                    return_value=MagicMock(),
                ):
                    with patch(
                        "autoskillit.server._lifespan.register_active_kitchen",
                        side_effect=OSError("registry write error"),
                    ):
                        await _fleet_auto_gate_boot(tool_ctx)

        assert tool_ctx.gate.enabled is True  # gate stays open despite registry failure

    @pytest.mark.anyio
    async def test_fleet_lifespan_auto_gate_logs_boot_event(self, tool_ctx):
        """fleet_auto_gate_boot emits structured log event."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _fleet_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        tool_ctx.quota_refresh_task = None

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch(
                    "autoskillit.server._lifespan.create_background_task",
                    return_value=MagicMock(),
                ):
                    with patch("autoskillit.server._lifespan.register_active_kitchen"):
                        with structlog.testing.capture_logs() as logs:
                            await _fleet_auto_gate_boot(tool_ctx)

        assert any(
            entry.get("event") == "fleet_auto_gate_boot" and entry.get("gate_state") == "open"
            for entry in logs
        )

    @pytest.mark.anyio
    async def test_fleet_auto_gate_boot_suppresses_fleet_tools_when_feature_disabled(
        self, tool_ctx, monkeypatch
    ):
        """_fleet_auto_gate_boot with features.fleet: false → fleet MCP tags disabled."""
        import dataclasses
        from unittest.mock import AsyncMock, MagicMock, patch

        from fastmcp.client import Client

        from autoskillit.core import FLEET_TOOLS
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server import mcp
        from autoskillit.server._lifespan import _fleet_auto_gate_boot

        # First enable fleet tag (as import-time phase 1 would)
        mcp.enable(tags={"fleet"})

        tool_ctx.gate = DefaultGateState(enabled=False)
        tool_ctx.quota_refresh_task = None
        tool_ctx.config = dataclasses.replace(tool_ctx.config, features={"fleet": False})

        monkeypatch.setattr("autoskillit.server._lifespan._get_ctx_or_none", lambda: tool_ctx)

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch(
                    "autoskillit.server._lifespan.create_background_task",
                    return_value=MagicMock(),
                ):
                    with patch("autoskillit.server._lifespan.register_active_kitchen"):
                        await _fleet_auto_gate_boot(tool_ctx)

        async with Client(mcp) as client:
            tools = await client.list_tools()
        tool_names = {t.name for t in tools}
        assert FLEET_TOOLS, "FLEET_TOOLS must not be empty — loop would pass vacuously"
        for name in FLEET_TOOLS:
            assert name not in tool_names, f"{name} should be hidden when fleet feature disabled"

    @pytest.mark.anyio
    async def test_fleet_auto_gate_boot_calls_shared_helper(self, tool_ctx, monkeypatch):
        """_fleet_auto_gate_boot delegates to _collect_disabled_feature_tags, not inline logic."""
        import dataclasses
        from unittest.mock import AsyncMock, MagicMock, patch

        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _fleet_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        tool_ctx.quota_refresh_task = None
        tool_ctx.config = dataclasses.replace(tool_ctx.config, features={"fleet": False})

        monkeypatch.setattr("autoskillit.server._lifespan._get_ctx_or_none", lambda: tool_ctx)

        with patch("autoskillit.server._lifespan._collect_disabled_feature_tags") as mock_helper:
            mock_helper.return_value = frozenset({"fleet"})
            with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                    with patch(
                        "autoskillit.server._lifespan.create_background_task",
                        return_value=MagicMock(),
                    ):
                        with patch("autoskillit.server._lifespan.register_active_kitchen"):
                            await _fleet_auto_gate_boot(tool_ctx)

        mock_helper.assert_called_once_with(
            tool_ctx.config.features, experimental_enabled=tool_ctx.config.experimental_enabled
        )


@pytest.mark.feature("orchestrator")
class TestFoodTruckAutoGateBoot:
    """Food truck lifespan auto-gate: _food_truck_auto_gate_boot opens gate."""

    @pytest.mark.anyio
    async def test_food_truck_headless_lifespan_auto_opens_gate(
        self, tool_ctx, monkeypatch
    ) -> None:
        from unittest.mock import AsyncMock, MagicMock, patch

        from autoskillit.core import FOOD_TRUCK_TOOL_TAGS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _food_truck_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        tool_ctx.quota_refresh_task = None
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv(FOOD_TRUCK_TOOL_TAGS_ENV_VAR, "kitchen-core,rectify")

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch(
                    "autoskillit.server._lifespan.create_background_task", return_value=MagicMock()
                ):
                    with patch("autoskillit.server._lifespan.register_active_kitchen"):
                        await _food_truck_auto_gate_boot(tool_ctx)

        assert tool_ctx.gate.enabled is True

    @pytest.mark.anyio
    async def test_food_truck_auto_gate_boot_sets_active_recipe_packs(
        self, tool_ctx, monkeypatch
    ) -> None:
        from unittest.mock import AsyncMock, MagicMock, patch

        from autoskillit.core import FOOD_TRUCK_TOOL_TAGS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _food_truck_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        tool_ctx.quota_refresh_task = None
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv(FOOD_TRUCK_TOOL_TAGS_ENV_VAR, "kitchen-core,rectify")

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch(
                    "autoskillit.server._lifespan.create_background_task", return_value=MagicMock()
                ):
                    with patch("autoskillit.server._lifespan.register_active_kitchen"):
                        await _food_truck_auto_gate_boot(tool_ctx)

        assert tool_ctx.active_recipe_packs == frozenset({"kitchen-core", "rectify"})
        assert tool_ctx.active_recipe_features == frozenset()

    @pytest.mark.anyio
    async def test_food_truck_auto_gate_boot_skips_non_headless_orchestrator(
        self, tool_ctx, monkeypatch
    ) -> None:
        from unittest.mock import MagicMock

        from autoskillit.core import FOOD_TRUCK_TOOL_TAGS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _food_truck_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
        monkeypatch.setenv(FOOD_TRUCK_TOOL_TAGS_ENV_VAR, "kitchen-core")

        mock_backend = MagicMock()
        mock_backend.capabilities.supports_tool_list_changed = True
        tool_ctx.backend = mock_backend

        await _food_truck_auto_gate_boot(tool_ctx)

        assert tool_ctx.gate.enabled is False

    @pytest.mark.anyio
    async def test_food_truck_auto_gate_boot_skips_when_no_tool_tags(
        self, tool_ctx, monkeypatch
    ) -> None:
        from autoskillit.core import FOOD_TRUCK_TOOL_TAGS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _food_truck_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.delenv(FOOD_TRUCK_TOOL_TAGS_ENV_VAR, raising=False)

        await _food_truck_auto_gate_boot(tool_ctx)

        assert tool_ctx.gate.enabled is False

    def test_food_truck_tool_tags_env_var_constant_value(self) -> None:
        from autoskillit.core import FOOD_TRUCK_TOOL_TAGS_ENV_VAR

        assert FOOD_TRUCK_TOOL_TAGS_ENV_VAR == "AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS"

    @pytest.mark.anyio
    async def test_run_skill_gate_error_when_food_truck_gate_closed(
        self, tool_ctx, monkeypatch
    ) -> None:
        import json

        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server.tools.tools_execution import run_skill

        tool_ctx.gate = DefaultGateState(enabled=False)

        result = json.loads(await run_skill("/some-skill", "/tmp"))
        assert result["subtype"] == "gate_error"
        assert result["success"] is False

    @pytest.mark.anyio
    async def test_run_skill_succeeds_after_food_truck_auto_gate_boot(
        self, tool_ctx, monkeypatch
    ) -> None:
        import json
        from unittest.mock import AsyncMock, MagicMock, patch

        from autoskillit.core import FOOD_TRUCK_TOOL_TAGS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _food_truck_auto_gate_boot
        from autoskillit.server.tools.tools_execution import run_skill
        from tests.fakes import InMemoryHeadlessExecutor

        tool_ctx.gate = DefaultGateState(enabled=False)
        tool_ctx.executor = InMemoryHeadlessExecutor()
        tool_ctx.quota_refresh_task = None
        tool_ctx.skill_resolver = None
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.setenv(FOOD_TRUCK_TOOL_TAGS_ENV_VAR, "kitchen-core")

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch(
                    "autoskillit.server._lifespan.create_background_task", return_value=MagicMock()
                ):
                    with patch("autoskillit.server._lifespan.register_active_kitchen"):
                        await _food_truck_auto_gate_boot(tool_ctx)

        result = json.loads(await run_skill("/some-skill", "/tmp"))
        assert result.get("success") is True

    @pytest.mark.anyio
    async def test_food_truck_auto_gate_boot_runs_label_sweep(
        self, tool_ctx, monkeypatch, tmp_path
    ) -> None:
        """ORCHESTRATOR boot must schedule label sweep when campaign state files exist."""
        import json as _json
        from unittest.mock import AsyncMock, MagicMock, patch

        from autoskillit.core import FOOD_TRUCK_TOOL_TAGS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _food_truck_auto_gate_boot

        dispatches_dir = tmp_path / ".autoskillit" / "temp" / "dispatches"
        dispatches_dir.mkdir(parents=True)
        (dispatches_dir / "campaign1.json").write_text(_json.dumps({}))
        tool_ctx.gate = DefaultGateState(enabled=False)
        tool_ctx.quota_refresh_task = None
        tool_ctx.project_dir = tmp_path
        tool_ctx.github_client = AsyncMock()
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv(FOOD_TRUCK_TOOL_TAGS_ENV_VAR, "kitchen-core")

        mock_create_bg_task = MagicMock(return_value=MagicMock())

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                _bg = "autoskillit.server._lifespan.create_background_task"
                with patch(_bg, mock_create_bg_task):
                    _rk = "autoskillit.server._lifespan.register_active_kitchen"
                    with patch(_rk):
                        with patch(
                            "autoskillit.server._lifespan.sweep_stale_dispatch_labels",
                            new_callable=AsyncMock,
                        ):
                            with patch(
                                "autoskillit.server._lifespan.discover_campaign_state_files",
                                return_value=[dispatches_dir / "campaign1.json"],
                            ):
                                await _food_truck_auto_gate_boot(tool_ctx)

        sweep_call_labels = [
            call_args.kwargs.get("label") for call_args in mock_create_bg_task.call_args_list
        ]
        assert "startup_label_recovery_sweep" in sweep_call_labels, (
            "label sweep background task not scheduled; "
            f"create_background_task labels: {sweep_call_labels}"
        )

    @pytest.mark.anyio
    async def test_food_truck_auto_gate_boot_passes_self_exclusion(
        self, tool_ctx, monkeypatch, tmp_path
    ) -> None:
        """Boot handler reads AUTOSKILLIT_DISPATCH_ID and passes it as skip set to reaper."""
        import json as _json
        from unittest.mock import AsyncMock, MagicMock, patch

        from autoskillit.core import (
            CAMPAIGN_ID_ENV_VAR,
            DISPATCH_ID_ENV_VAR,
            FOOD_TRUCK_TOOL_TAGS_ENV_VAR,
        )
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _food_truck_auto_gate_boot

        dispatches_dir = tmp_path / ".autoskillit" / "temp" / "dispatches"
        dispatches_dir.mkdir(parents=True)
        (dispatches_dir / "campaign1.json").write_text(_json.dumps({}))
        tool_ctx.gate = DefaultGateState(enabled=False)
        tool_ctx.quota_refresh_task = None
        tool_ctx.project_dir = tmp_path
        tool_ctx.github_client = AsyncMock()
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv(FOOD_TRUCK_TOOL_TAGS_ENV_VAR, "kitchen-core")
        monkeypatch.setenv(DISPATCH_ID_ENV_VAR, "my-ft-dispatch")
        monkeypatch.setenv(CAMPAIGN_ID_ENV_VAR, "my-campaign-1")

        mock_reap = AsyncMock()

        with (
            patch("autoskillit.server.tools.tools_kitchen._write_hook_config"),
            patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()),
            patch("autoskillit.server._lifespan.create_background_task", return_value=MagicMock()),
            patch("autoskillit.server._lifespan.register_active_kitchen"),
            patch(
                "autoskillit.server._lifespan.discover_campaign_state_files",
                return_value=[dispatches_dir / "campaign1.json"],
            ),
            patch("autoskillit.server._lifespan.reap_stale_dispatches_async", mock_reap),
        ):
            await _food_truck_auto_gate_boot(tool_ctx)

        mock_reap.assert_called_once_with(
            [dispatches_dir / "campaign1.json"],
            skip_dispatch_ids=frozenset({"my-ft-dispatch"}),
            own_campaign_id="my-campaign-1",
            min_reap_age_seconds=60.0,
            reaper_dispatch_id="my-ft-dispatch",
            heartbeat_grace_seconds=90.0,
        )

    @pytest.mark.anyio
    async def test_food_truck_auto_gate_boot_no_dispatch_id_passes_none(
        self, tool_ctx, monkeypatch, tmp_path
    ) -> None:
        """When AUTOSKILLIT_DISPATCH_ID is unset, reaper is called with skip_dispatch_ids=None."""
        import json as _json
        from unittest.mock import AsyncMock, MagicMock, patch

        from autoskillit.core import (
            CAMPAIGN_ID_ENV_VAR,
            DISPATCH_ID_ENV_VAR,
            FOOD_TRUCK_TOOL_TAGS_ENV_VAR,
        )
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _food_truck_auto_gate_boot

        dispatches_dir = tmp_path / ".autoskillit" / "temp" / "dispatches"
        dispatches_dir.mkdir(parents=True)
        (dispatches_dir / "campaign1.json").write_text(_json.dumps({}))
        tool_ctx.gate = DefaultGateState(enabled=False)
        tool_ctx.quota_refresh_task = None
        tool_ctx.project_dir = tmp_path
        tool_ctx.github_client = AsyncMock()
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv(FOOD_TRUCK_TOOL_TAGS_ENV_VAR, "kitchen-core")
        monkeypatch.delenv(DISPATCH_ID_ENV_VAR, raising=False)
        monkeypatch.setenv(CAMPAIGN_ID_ENV_VAR, "my-campaign-2")

        mock_reap = AsyncMock()

        with (
            patch("autoskillit.server.tools.tools_kitchen._write_hook_config"),
            patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()),
            patch("autoskillit.server._lifespan.create_background_task", return_value=MagicMock()),
            patch("autoskillit.server._lifespan.register_active_kitchen"),
            patch(
                "autoskillit.server._lifespan.discover_campaign_state_files",
                return_value=[dispatches_dir / "campaign1.json"],
            ),
            patch("autoskillit.server._lifespan.reap_stale_dispatches_async", mock_reap),
        ):
            await _food_truck_auto_gate_boot(tool_ctx)

        mock_reap.assert_called_once_with(
            [dispatches_dir / "campaign1.json"],
            skip_dispatch_ids=None,
            own_campaign_id="my-campaign-2",
            min_reap_age_seconds=60.0,
            reaper_dispatch_id="",
            heartbeat_grace_seconds=90.0,
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "boot_fn_name",
    ["_fleet_auto_gate_boot", "_food_truck_auto_gate_boot", "_skill_auto_gate_boot"],
)
async def test_boot_paths_inherit_campaign_id(boot_fn_name, tool_ctx, monkeypatch):
    """All boot paths must resolve kitchen_id via resolve_kitchen_id()."""
    import importlib
    from unittest.mock import AsyncMock, MagicMock, patch

    from autoskillit.core import CAMPAIGN_ID_ENV_VAR
    from autoskillit.pipeline.gate import DefaultGateState

    expected_id = f"test-campaign-{boot_fn_name}"
    monkeypatch.setenv(CAMPAIGN_ID_ENV_VAR, expected_id)
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    # FOOD_TRUCK_TOOL_TAGS: only read by _food_truck_auto_gate_boot;
    # harmless for fleet/skill paths.
    monkeypatch.setenv("AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS", "kitchen-core")
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS_AUTO_GATE", "1")
    tool_ctx.gate = DefaultGateState(enabled=False)
    tool_ctx.quota_refresh_task = None

    boot_fn = getattr(
        importlib.import_module("autoskillit.server._lifespan"),
        boot_fn_name,
    )
    with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
        with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
            with patch(
                "autoskillit.server._lifespan.create_background_task",
                return_value=MagicMock(),
            ):
                with patch("autoskillit.server._lifespan.register_active_kitchen"):
                    await boot_fn(tool_ctx)

    assert tool_ctx.kitchen_id == expected_id
