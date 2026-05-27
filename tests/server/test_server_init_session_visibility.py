"""Tests for server init: session type visibility, fleet gate boot, feature gate visibility."""

from __future__ import annotations

import pytest
import structlog.testing

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


@pytest.mark.feature("fleet")
class TestSessionTypeVisibility:
    """3-branch session-type tag visibility dispatch."""

    @pytest.mark.anyio
    async def test_fleet_dispatch_mode_enables_fleet_dispatch_tools(self, monkeypatch):
        """fleet + FLEET_MODE=dispatch reveals fleet tools + fleet-dispatch tools."""
        from autoskillit.core import (
            FLEET_DISPATCH_MODE,
            FLEET_DISPATCH_TOOLS,
            FLEET_MODE_ENV_VAR,
            FLEET_TOOLS,
            FREE_RANGE_TOOLS,
        )
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
        monkeypatch.setenv(FLEET_MODE_ENV_VAR, FLEET_DISPATCH_MODE)
        _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        visible = {t.name for t in tools}

        expected = FLEET_TOOLS | FLEET_DISPATCH_TOOLS | FREE_RANGE_TOOLS
        assert visible == expected

    @pytest.mark.parametrize("mode_value", ["campaign", None])
    @pytest.mark.anyio
    async def test_fleet_campaign_mode_hides_fleet_dispatch_tools(self, monkeypatch, mode_value):
        """fleet + FLEET_MODE=campaign (or absent) hides fleet-dispatch tools."""
        from autoskillit.core import FLEET_DISPATCH_TOOLS, FLEET_MODE_ENV_VAR
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
        if mode_value is not None:
            monkeypatch.setenv(FLEET_MODE_ENV_VAR, mode_value)
        else:
            monkeypatch.delenv(FLEET_MODE_ENV_VAR, raising=False)
        _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        visible = {t.name for t in tools}
        assert visible.isdisjoint(FLEET_DISPATCH_TOOLS), (
            f"fleet-dispatch tools unexpectedly visible with FLEET_MODE={mode_value!r}"
        )

    @pytest.mark.anyio
    async def test_fleet_dispatch_constant_matches_tagged_tools(self, monkeypatch):
        """FLEET_DISPATCH_TOOLS constant must exactly match tools tagged fleet-dispatch."""
        from autoskillit.core import FLEET_DISPATCH_MODE, FLEET_DISPATCH_TOOLS, FLEET_MODE_ENV_VAR
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
        monkeypatch.setenv(FLEET_MODE_ENV_VAR, FLEET_DISPATCH_MODE)
        _apply_session_type_visibility()

        all_tools = {t.name: t for t in await mcp.list_tools()}
        tagged = {name for name, t in all_tools.items() if "fleet-dispatch" in t.tags}
        assert tagged == FLEET_DISPATCH_TOOLS, (
            f"FLEET_DISPATCH_TOOLS constant out of sync. "
            f"Extra in constant: {FLEET_DISPATCH_TOOLS - tagged}. "
            f"Extra on server: {tagged - FLEET_DISPATCH_TOOLS}."
        )

    @pytest.mark.anyio
    async def test_fleet_enables_fleet_tag(self, monkeypatch):
        from autoskillit.core import FLEET_TOOLS, GATED_TOOLS, HEADLESS_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
        _apply_session_type_visibility()

        tool_names = {t.name for t in await mcp.list_tools()}

        # Positive: fleet-tagged tools are visible
        for name in FLEET_TOOLS:
            assert name in tool_names, f"{name} should be visible for fleet session"
        # Negative: non-fleet kitchen/headless tools remain hidden
        for name in GATED_TOOLS - FLEET_TOOLS:
            assert name not in tool_names, f"{name} should be hidden for fleet session"
        for name in HEADLESS_TOOLS:
            assert name not in tool_names, f"{name} should be hidden for fleet session"

    @pytest.mark.anyio
    async def test_fleet_tools_do_not_carry_kitchen_tag(self, monkeypatch):
        """Fleet-tagged tools must NOT carry the kitchen tag (tag partition)."""
        from autoskillit.core import FLEET_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
        _apply_session_type_visibility()

        all_tools = {t.name: t for t in await mcp.list_tools()}
        for name in FLEET_TOOLS:
            tool = all_tools.get(name)
            assert tool is not None, f"{name} not registered"
            assert "kitchen" not in tool.tags, f"{name} must not carry kitchen tag"
            assert "fleet" in tool.tags, f"{name} must have fleet tag"
            assert "autoskillit" in tool.tags, f"{name} must retain autoskillit tag"

    @pytest.mark.anyio
    async def test_fleet_tools_constant_matches_tagged_tools(self, monkeypatch):
        """FLEET_TOOLS constant matches exactly the tools with fleet tag."""
        from autoskillit.core import FLEET_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
        _apply_session_type_visibility()

        all_tools = {t.name: t for t in await mcp.list_tools()}
        tagged = {name for name, t in all_tools.items() if "fleet" in t.tags}
        assert tagged == FLEET_TOOLS, (
            f"FLEET_TOOLS constant out of sync. "
            f"Extra in constant: {FLEET_TOOLS - tagged}. "
            f"Extra on server: {tagged - FLEET_TOOLS}."
        )

    @pytest.mark.anyio
    async def test_orchestrator_headless_enables_kitchen_tag(self, monkeypatch):
        from autoskillit.core import FLEET_DISPATCH_TOOLS, FLEET_TOOLS, GATED_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}
        for name in GATED_TOOLS - FLEET_TOOLS - FLEET_DISPATCH_TOOLS:
            assert name in tool_names, f"{name} should be visible for orchestrator+headless"

    @pytest.mark.anyio
    async def test_orchestrator_interactive_no_pre_reveal(self, monkeypatch):
        from autoskillit.core import GATED_TOOLS, HEADLESS_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
        _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}
        for name in GATED_TOOLS:
            assert name not in tool_names, f"{name} should be hidden for orchestrator+interactive"
        for name in HEADLESS_TOOLS:
            assert name not in tool_names, f"{name} should be hidden for orchestrator+interactive"

    @pytest.mark.anyio
    async def test_skill_headless_enables_headless_tag(self, monkeypatch):
        from autoskillit.core import GATED_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}
        assert "test_check" in tool_names, "test_check should be visible for skill+headless"
        for name in GATED_TOOLS:
            assert name not in tool_names, f"{name} (kitchen) should be hidden for skill+headless"

    @pytest.mark.anyio
    async def test_skill_headless_auto_gate_enables_kitchen_core_and_headless(self, monkeypatch):
        from autoskillit.core import GATED_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS_AUTO_GATE", "1")
        _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}
        tool_tags = {t.name: t.tags for t in tools}
        assert "test_check" in tool_names, (
            "test_check should be visible for skill+headless+auto_gate"
        )
        kitchen_core_tools = {t.name for t in tools if "kitchen-core" in t.tags}
        assert kitchen_core_tools, "kitchen-core-tagged tools should be visible when AUTO_GATE=1"
        visible_gated = {name for name in GATED_TOOLS if name in tool_names}
        assert visible_gated, "At least one GATED_TOOL should be visible via kitchen-core tag"
        for name in visible_gated:
            assert "kitchen-core" in tool_tags[name], f"{name} visible without kitchen-core tag"

    @pytest.mark.anyio
    async def test_skill_headless_without_auto_gate_only_headless(self, monkeypatch):
        from autoskillit.core import GATED_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS_AUTO_GATE", raising=False)
        _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}
        assert "test_check" in tool_names, "test_check should be visible for skill+headless"
        for name in GATED_TOOLS:
            assert name not in tool_names, f"{name} (kitchen) should be hidden for skill+headless"

    @pytest.mark.anyio
    async def test_skill_headless_auto_gate_zero_only_headless(self, monkeypatch):
        from autoskillit.core import GATED_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS_AUTO_GATE", "0")
        _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}
        assert "test_check" in tool_names, "test_check should be visible for skill+headless+gate=0"
        for name in GATED_TOOLS:
            assert name not in tool_names, (
                f"{name} (kitchen) should be hidden for skill+headless+gate=0"
            )

    @pytest.mark.anyio
    async def test_food_truck_with_tool_tags_sees_kitchen_core_plus_declared(self, monkeypatch):
        """ORCHESTRATOR+HEADLESS with FOOD_TRUCK_TOOL_TAGS sees kitchen-core + github only."""
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS", "github")
        _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}

        assert "run_cmd" in tool_names
        assert "run_skill" in tool_names
        assert "merge_worktree" in tool_names
        assert "fetch_github_issue" in tool_names
        assert "wait_for_ci" not in tool_names
        assert "clone_repo" not in tool_names

    @pytest.mark.anyio
    async def test_food_truck_with_multiple_packs(self, monkeypatch):
        """ORCHESTRATOR+HEADLESS with FOOD_TRUCK_TOOL_TAGS=github,ci sees both packs."""
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS", "github,ci")
        _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}

        assert "fetch_github_issue" in tool_names
        assert "wait_for_ci" in tool_names
        assert "clone_repo" not in tool_names

    @pytest.mark.anyio
    async def test_food_truck_without_tool_tags_sees_full_kitchen(self, monkeypatch):
        """ORCHESTRATOR+HEADLESS without FOOD_TRUCK_TOOL_TAGS falls back to full kitchen."""
        from autoskillit.core import FLEET_DISPATCH_TOOLS, FLEET_TOOLS, GATED_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.delenv("AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS", raising=False)
        _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}

        for name in GATED_TOOLS - FLEET_TOOLS - FLEET_DISPATCH_TOOLS:
            assert name in tool_names

    @pytest.mark.anyio
    async def test_cook_interactive_unaffected_by_tool_tags(self, monkeypatch):
        """Interactive ORCHESTRATOR (cook) ignores FOOD_TRUCK_TOOL_TAGS."""
        from autoskillit.core import GATED_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
        monkeypatch.setenv("AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS", "github")
        _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}

        for name in GATED_TOOLS:
            assert name not in tool_names

    @pytest.mark.anyio
    async def test_skill_interactive_no_pre_reveal(self, monkeypatch):
        from autoskillit.core import GATED_TOOLS, HEADLESS_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
        _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}
        for name in GATED_TOOLS:
            assert name not in tool_names, f"{name} should be hidden for skill+interactive"
        for name in HEADLESS_TOOLS:
            assert name not in tool_names, f"{name} should be hidden for skill+interactive"

    @pytest.mark.anyio
    async def test_transitional_bridge_enables_headless(self, monkeypatch):
        import warnings

        from autoskillit.core import GATED_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}
        assert "test_check" in tool_names, "test_check should be visible for bridge HEADLESS=1"
        for name in GATED_TOOLS:
            assert name not in tool_names, f"{name} (kitchen) should be hidden for bridge"

    @pytest.mark.anyio
    async def test_fleet_tag_reset_by_conftest(self, monkeypatch):
        from autoskillit.server import mcp

        # The conftest _reset_mcp_tags fixture has already disabled the fleet tag.
        # Verify: no fleet-enabled state leaked from a previous test.
        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}
        # No kitchen tools should be visible — fleet tag was reset
        from autoskillit.core import GATED_TOOLS

        for name in GATED_TOOLS:
            assert name not in tool_names, f"{name} should be hidden after conftest reset"


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
                    "autoskillit.pipeline.create_background_task",
                    return_value=MagicMock(),
                ) as mock_create_bg_task:
                    with patch(
                        "autoskillit.core.register_active_kitchen"
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
    @pytest.mark.parametrize(
        "boot_fn_name",
        ["_fleet_auto_gate_boot", "_food_truck_auto_gate_boot", "_skill_auto_gate_boot"],
    )
    async def test_boot_paths_inherit_campaign_id(self, boot_fn_name, tool_ctx, monkeypatch):
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
                    "autoskillit.pipeline.create_background_task",
                    return_value=MagicMock(),
                ):
                    with patch("autoskillit.core.register_active_kitchen"):
                        await boot_fn(tool_ctx)

        assert tool_ctx.kitchen_id == expected_id


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
                    "autoskillit.pipeline.create_background_task",
                    return_value=MagicMock(),
                ):
                    with patch(
                        "autoskillit.core.register_active_kitchen"
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
                    "autoskillit.pipeline.create_background_task",
                    return_value=MagicMock(),
                ):
                    with patch(
                        "autoskillit.core.register_active_kitchen"
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
                    "autoskillit.pipeline.create_background_task",
                    return_value=MagicMock(),
                ):
                    with patch("autoskillit.core.register_active_kitchen"):
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
                    "autoskillit.pipeline.create_background_task",
                    return_value=MagicMock(),
                ):
                    with patch("autoskillit.core.register_active_kitchen"):
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
                    "autoskillit.pipeline.create_background_task",
                    side_effect=RuntimeError("task creation error"),
                ):
                    with patch("autoskillit.core.register_active_kitchen"):
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
                    "autoskillit.pipeline.create_background_task",
                    return_value=MagicMock(),
                ):
                    with patch(
                        "autoskillit.core.register_active_kitchen",
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
                    "autoskillit.pipeline.create_background_task",
                    return_value=MagicMock(),
                ):
                    with patch("autoskillit.core.register_active_kitchen"):
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
                    "autoskillit.pipeline.create_background_task",
                    return_value=MagicMock(),
                ):
                    with patch("autoskillit.core.register_active_kitchen"):
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

        with patch("autoskillit.core._collect_disabled_feature_tags") as mock_helper:
            mock_helper.return_value = frozenset({"fleet"})
            with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                    with patch(
                        "autoskillit.pipeline.create_background_task",
                        return_value=MagicMock(),
                    ):
                        with patch("autoskillit.core.register_active_kitchen"):
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
                    "autoskillit.pipeline.create_background_task", return_value=MagicMock()
                ):
                    with patch("autoskillit.core.register_active_kitchen"):
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
                    "autoskillit.pipeline.create_background_task", return_value=MagicMock()
                ):
                    with patch("autoskillit.core.register_active_kitchen"):
                        await _food_truck_auto_gate_boot(tool_ctx)

        assert tool_ctx.active_recipe_packs == frozenset({"kitchen-core", "rectify"})
        assert tool_ctx.active_recipe_features == frozenset()

    @pytest.mark.anyio
    async def test_food_truck_auto_gate_boot_skips_non_headless_orchestrator(
        self, tool_ctx, monkeypatch
    ) -> None:
        from autoskillit.core import FOOD_TRUCK_TOOL_TAGS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _food_truck_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
        monkeypatch.setenv(FOOD_TRUCK_TOOL_TAGS_ENV_VAR, "kitchen-core")

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
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.setenv(FOOD_TRUCK_TOOL_TAGS_ENV_VAR, "kitchen-core")

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch(
                    "autoskillit.pipeline.create_background_task", return_value=MagicMock()
                ):
                    with patch("autoskillit.core.register_active_kitchen"):
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
                with patch("autoskillit.pipeline.create_background_task", mock_create_bg_task):
                    with patch("autoskillit.core.register_active_kitchen"):
                        with patch(
                            "autoskillit.fleet.sweep_stale_dispatch_labels",
                            new_callable=AsyncMock,
                        ):
                            with patch(
                                "autoskillit.fleet.discover_campaign_state_files",
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

        from autoskillit.core import DISPATCH_ID_ENV_VAR, FOOD_TRUCK_TOOL_TAGS_ENV_VAR
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

        mock_reap = AsyncMock()

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch(
                    "autoskillit.pipeline.create_background_task", return_value=MagicMock()
                ):
                    with patch("autoskillit.core.register_active_kitchen"):
                        with patch(
                            "autoskillit.fleet.discover_campaign_state_files",
                            return_value=[dispatches_dir / "campaign1.json"],
                        ):
                            with patch(
                                "autoskillit.fleet.reap_stale_dispatches_async",
                                mock_reap,
                            ):
                                await _food_truck_auto_gate_boot(tool_ctx)

        mock_reap.assert_called_once_with(
            [dispatches_dir / "campaign1.json"],
            skip_dispatch_ids=frozenset({"my-ft-dispatch"}),
        )

    @pytest.mark.anyio
    async def test_food_truck_auto_gate_boot_no_dispatch_id_passes_none(
        self, tool_ctx, monkeypatch, tmp_path
    ) -> None:
        """When AUTOSKILLIT_DISPATCH_ID is unset, reaper is called with skip_dispatch_ids=None."""
        import json as _json
        from unittest.mock import AsyncMock, MagicMock, patch

        from autoskillit.core import DISPATCH_ID_ENV_VAR, FOOD_TRUCK_TOOL_TAGS_ENV_VAR
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

        mock_reap = AsyncMock()

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch(
                    "autoskillit.pipeline.create_background_task", return_value=MagicMock()
                ):
                    with patch("autoskillit.core.register_active_kitchen"):
                        with patch(
                            "autoskillit.fleet.discover_campaign_state_files",
                            return_value=[dispatches_dir / "campaign1.json"],
                        ):
                            with patch(
                                "autoskillit.fleet.reap_stale_dispatches_async",
                                mock_reap,
                            ):
                                await _food_truck_auto_gate_boot(tool_ctx)

        mock_reap.assert_called_once_with(
            [dispatches_dir / "campaign1.json"],
            skip_dispatch_ids=None,
        )


@pytest.mark.feature("skill")
class TestSkillAutoGateBoot:
    """Skill lifespan auto-gate: _skill_auto_gate_boot opens gate."""

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_opens_gate(self, tool_ctx, monkeypatch):
        """SKILL+HEADLESS+AUTO_GATE: gate is open after _skill_auto_gate_boot() runs."""
        from unittest.mock import AsyncMock, patch

        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.setenv(HEADLESS_ENV_VAR, "1")
        monkeypatch.setenv(HEADLESS_AUTO_GATE_ENV_VAR, "1")

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.core.register_active_kitchen"):
                    await _skill_auto_gate_boot(tool_ctx)

        assert tool_ctx.gate.enabled is True
        assert tool_ctx.kitchen_id is not None
        assert tool_ctx.active_recipe_packs == frozenset()
        assert tool_ctx.active_recipe_features == frozenset()

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_noop_when_headless_not_1(self, tool_ctx, monkeypatch):
        """SKILL without HEADLESS=1: gate remains closed."""
        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.delenv(HEADLESS_ENV_VAR, raising=False)
        monkeypatch.setenv(HEADLESS_AUTO_GATE_ENV_VAR, "1")

        await _skill_auto_gate_boot(tool_ctx)

        assert tool_ctx.gate.enabled is False

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_noop_when_auto_gate_not_1(self, tool_ctx, monkeypatch):
        """HEADLESS=1 without HEADLESS_AUTO_GATE=1: gate remains closed."""
        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.setenv(HEADLESS_ENV_VAR, "1")
        monkeypatch.delenv(HEADLESS_AUTO_GATE_ENV_VAR, raising=False)

        await _skill_auto_gate_boot(tool_ctx)

        assert tool_ctx.gate.enabled is False

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_warns_and_returns_when_no_gate(
        self, tool_ctx, monkeypatch
    ):
        """gate is None: warning logged and early return (gate stays closed)."""
        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        tool_ctx.gate = None
        monkeypatch.setenv(HEADLESS_ENV_VAR, "1")
        monkeypatch.setenv(HEADLESS_AUTO_GATE_ENV_VAR, "1")

        with structlog.testing.capture_logs() as logs:
            await _skill_auto_gate_boot(tool_ctx)

        assert any(entry.get("event") == "skill_auto_gate_boot_no_gate" for entry in logs)
        assert tool_ctx.kitchen_id is not None

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_fails_open_on_feature_suppression_error(
        self, tool_ctx, monkeypatch
    ):
        """Feature suppression failure: gate stays open."""
        from unittest.mock import AsyncMock, patch

        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.setenv(HEADLESS_ENV_VAR, "1")
        monkeypatch.setenv(HEADLESS_AUTO_GATE_ENV_VAR, "1")

        with patch(
            "autoskillit.core._collect_disabled_feature_tags",
            side_effect=RuntimeError("feature error"),
        ):
            with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                    with patch("autoskillit.core.register_active_kitchen"):
                        await _skill_auto_gate_boot(tool_ctx)

        assert tool_ctx.gate.enabled is True

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_fails_open_on_hook_config_error(
        self, tool_ctx, monkeypatch
    ):
        """_write_hook_config failure: gate stays open."""
        from unittest.mock import AsyncMock, patch

        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.setenv(HEADLESS_ENV_VAR, "1")
        monkeypatch.setenv(HEADLESS_AUTO_GATE_ENV_VAR, "1")

        with patch(
            "autoskillit.server.tools.tools_kitchen._write_hook_config",
            side_effect=OSError("disk full"),
        ):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.core.register_active_kitchen"):
                    await _skill_auto_gate_boot(tool_ctx)

        assert tool_ctx.gate.enabled is True

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_fails_open_on_quota_cache_error(
        self, tool_ctx, monkeypatch
    ):
        """_prime_quota_cache failure: gate stays open."""
        from unittest.mock import AsyncMock, patch

        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.setenv(HEADLESS_ENV_VAR, "1")
        monkeypatch.setenv(HEADLESS_AUTO_GATE_ENV_VAR, "1")

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch(
                "autoskillit.server._misc._prime_quota_cache",
                new=AsyncMock(side_effect=RuntimeError("quota cache error")),
            ):
                with patch("autoskillit.core.register_active_kitchen"):
                    await _skill_auto_gate_boot(tool_ctx)

        assert tool_ctx.gate.enabled is True

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_fails_open_on_registry_error(self, tool_ctx, monkeypatch):
        """register_active_kitchen failure: gate stays open."""
        from unittest.mock import AsyncMock, patch

        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.setenv(HEADLESS_ENV_VAR, "1")
        monkeypatch.setenv(HEADLESS_AUTO_GATE_ENV_VAR, "1")

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch(
                    "autoskillit.core.register_active_kitchen",
                    side_effect=OSError("registry write error"),
                ):
                    await _skill_auto_gate_boot(tool_ctx)

        assert tool_ctx.gate.enabled is True

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_no_quota_refresh_loop(self, tool_ctx, monkeypatch):
        """_skill_auto_gate_boot does NOT create a quota_refresh_loop background task."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.setenv(HEADLESS_ENV_VAR, "1")
        monkeypatch.setenv(HEADLESS_AUTO_GATE_ENV_VAR, "1")

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch(
                    "autoskillit.pipeline.create_background_task",
                    return_value=MagicMock(),
                ) as mock_create_bg_task:
                    with patch("autoskillit.core.register_active_kitchen"):
                        await _skill_auto_gate_boot(tool_ctx)

        quota_loop_calls = [
            call
            for call in mock_create_bg_task.call_args_list
            if call.kwargs.get("label") == "quota_refresh_loop"
        ]
        assert len(quota_loop_calls) == 0, (
            "quota_refresh_loop must not be created for SKILL sessions"
        )
        mock_create_bg_task.assert_not_called()

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_uses_project_dir_for_registration(
        self, build_ctx, tmp_path, monkeypatch
    ):
        """register_active_kitchen must be called with ctx.project_dir, not Path.cwd()."""
        import os
        from unittest.mock import AsyncMock, patch

        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        monkeypatch.chdir(tmp_path)
        different_dir = tmp_path / "project_root"
        different_dir.mkdir()

        ctx = build_ctx(project_dir=different_dir)
        ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.setenv(HEADLESS_ENV_VAR, "1")
        monkeypatch.setenv(HEADLESS_AUTO_GATE_ENV_VAR, "1")

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.core.register_active_kitchen") as mock_register_kitchen:
                    await _skill_auto_gate_boot(ctx)

        mock_register_kitchen.assert_called_once_with(
            ctx.kitchen_id, os.getpid(), str(different_dir)
        )

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_emits_structured_log(self, tool_ctx, monkeypatch):
        """_skill_auto_gate_boot emits info log with event name, gate_state, and kitchen_id."""
        from unittest.mock import AsyncMock, patch

        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.setenv(HEADLESS_ENV_VAR, "1")
        monkeypatch.setenv(HEADLESS_AUTO_GATE_ENV_VAR, "1")

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.core.register_active_kitchen"):
                    with structlog.testing.capture_logs() as logs:
                        await _skill_auto_gate_boot(tool_ctx)

        assert any(
            entry.get("event") == "skill_auto_gate_boot"
            and entry.get("gate_state") == "open"
            and entry.get("kitchen_id") is not None
            for entry in logs
        )

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_writes_hook_config(self, tool_ctx, monkeypatch):
        """SKILL+HEADLESS+AUTO_GATE: _write_hook_config is called once."""
        from unittest.mock import AsyncMock, patch

        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.setenv(HEADLESS_ENV_VAR, "1")
        monkeypatch.setenv(HEADLESS_AUTO_GATE_ENV_VAR, "1")

        with patch(
            "autoskillit.server.tools.tools_kitchen._write_hook_config"
        ) as mock_write_hook_config:
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.core.register_active_kitchen"):
                    await _skill_auto_gate_boot(tool_ctx)

        mock_write_hook_config.assert_called_once_with()
        assert tool_ctx.gate.enabled is True

    @pytest.mark.anyio
    async def test_skill_auto_gate_boot_skips_when_both_absent(self, tool_ctx, monkeypatch):
        """Neither HEADLESS nor AUTO_GATE set: gate remains closed."""
        from unittest.mock import patch

        from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR, HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        tool_ctx.gate = DefaultGateState(enabled=False)
        monkeypatch.delenv(HEADLESS_ENV_VAR, raising=False)
        monkeypatch.delenv(HEADLESS_AUTO_GATE_ENV_VAR, raising=False)

        with patch(
            "autoskillit.server.tools.tools_kitchen._write_hook_config"
        ) as mock_write_hook_config:
            with patch("autoskillit.core.register_active_kitchen") as mock_register_kitchen:
                await _skill_auto_gate_boot(tool_ctx)

        assert tool_ctx.gate.enabled is False
        mock_write_hook_config.assert_not_called()
        mock_register_kitchen.assert_not_called()


@pytest.mark.feature("skill")
class TestSkillAutoGateBootRegistry:
    """SKILL session type registry wiring."""

    def test_lifespan_boot_registry_maps_skill_to_handler(self):
        """SessionType.SKILL maps to _skill_auto_gate_boot in _LIFESPAN_BOOT_REGISTRY."""
        from autoskillit.core import SessionType
        from autoskillit.server._lifespan import (
            _LIFESPAN_BOOT_REGISTRY,
            _skill_auto_gate_boot,
        )

        assert _LIFESPAN_BOOT_REGISTRY[SessionType.SKILL] is _skill_auto_gate_boot

    def test_lifespan_boot_registry_covers_all_session_types(self):
        """_LIFESPAN_BOOT_REGISTRY has no None values (all session types wired)."""
        from autoskillit.core import SessionType
        from autoskillit.server._lifespan import _LIFESPAN_BOOT_REGISTRY

        for session_type in SessionType:
            assert _LIFESPAN_BOOT_REGISTRY.get(session_type) is not None, (
                f"{session_type} has no boot handler in _LIFESPAN_BOOT_REGISTRY"
            )

    def test_headless_auto_gate_env_var_constant_value(self) -> None:
        from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS, HEADLESS_AUTO_GATE_ENV_VAR

        assert HEADLESS_AUTO_GATE_ENV_VAR == "AUTOSKILLIT_HEADLESS_AUTO_GATE"
        assert HEADLESS_AUTO_GATE_ENV_VAR in AUTOSKILLIT_PRIVATE_ENV_VARS


@pytest.mark.feature("fleet")
class TestFeatureGateVisibility:
    """Session-type dispatch in _apply_session_type_visibility (phase 1 only)."""

    @pytest.fixture(autouse=True)
    def _reset_mcp_visibility(self):
        """Reset gated tag visibility on the shared mcp singleton before each test."""
        from autoskillit.core import ALL_VISIBILITY_TAGS
        from autoskillit.server import mcp

        mcp._transforms.clear()
        for tag in sorted(ALL_VISIBILITY_TAGS):
            mcp.disable(tags={tag})
        yield
        mcp._transforms.clear()
        for tag in sorted(ALL_VISIBILITY_TAGS):
            mcp.disable(tags={tag})

    @pytest.mark.anyio
    async def test_fleet_tools_visible_when_feature_enabled(self, monkeypatch):
        """SESSION_TYPE=fleet → fleet tools visible (session-type dispatch only)."""
        from autoskillit.core import FLEET_TOOLS
        from autoskillit.server import mcp
        from autoskillit.server._session_type import _apply_session_type_visibility

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
        _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}
        for name in FLEET_TOOLS:
            assert name in tool_names, (
                f"{name} should be visible for fleet session (phase-1 reveal)"
            )

    def test_apply_session_type_visibility_sole_calling_convention(self):
        """No feature_gates parameter exists — session-type dispatch only."""
        import inspect

        from autoskillit.server._session_type import _apply_session_type_visibility

        sig = inspect.signature(_apply_session_type_visibility)
        assert "feature_gates" not in sig.parameters

    @pytest.mark.anyio
    async def test_session_type_fleet_enables_fleet_tags(self, monkeypatch):
        """FLEET session activates fleet tool visibility (no feature gate needed)."""
        from autoskillit.core import FLEET_TOOLS
        from autoskillit.server import mcp
        from autoskillit.server._session_type import _apply_session_type_visibility

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}
        assert FLEET_TOOLS
        for tool in FLEET_TOOLS:
            assert tool in tool_names
