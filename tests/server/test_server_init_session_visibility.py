"""Tests for server init: session type visibility, fleet gate boot, feature gate visibility."""

from __future__ import annotations

from pathlib import Path

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
    async def test_fleet_tools_retain_kitchen_tag(self, monkeypatch):
        """Fleet-tagged tools must still carry the kitchen tag."""
        from autoskillit.core import FLEET_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
        _apply_session_type_visibility()

        all_tools = {t.name: t for t in await mcp.list_tools()}
        for name in FLEET_TOOLS:
            tool = all_tools.get(name)
            assert tool is not None, f"{name} not registered"
            assert "kitchen" in tool.tags, f"{name} must retain kitchen tag"
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
        from autoskillit.core import GATED_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}
        for name in GATED_TOOLS:
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
    async def test_food_truck_with_tool_tags_sees_kitchen_core_plus_declared(self, monkeypatch):
        """ORCHESTRATOR+HEADLESS with L3_TOOL_TAGS sees kitchen-core + github only."""
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_L3_TOOL_TAGS", "github")
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
        """ORCHESTRATOR+HEADLESS with L3_TOOL_TAGS=github,ci sees both packs."""
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_L3_TOOL_TAGS", "github,ci")
        _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}

        assert "fetch_github_issue" in tool_names
        assert "wait_for_ci" in tool_names
        assert "clone_repo" not in tool_names

    @pytest.mark.anyio
    async def test_food_truck_without_tool_tags_sees_full_kitchen(self, monkeypatch):
        """ORCHESTRATOR+HEADLESS without L3_TOOL_TAGS falls back to full kitchen."""
        from autoskillit.core import GATED_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.delenv("AUTOSKILLIT_L3_TOOL_TAGS", raising=False)
        _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}

        for name in GATED_TOOLS:
            assert name in tool_names

    @pytest.mark.anyio
    async def test_cook_interactive_unaffected_by_tool_tags(self, monkeypatch):
        """Interactive ORCHESTRATOR (cook) ignores L3_TOOL_TAGS."""
        from autoskillit.core import GATED_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
        monkeypatch.setenv("AUTOSKILLIT_L3_TOOL_TAGS", "github")
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
            tool_ctx.kitchen_id, os.getpid(), str(Path.cwd())
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
