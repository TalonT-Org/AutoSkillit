"""Tests for session-type tag visibility dispatch (_apply_session_type_visibility)."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import EXPLORATION_TOOLS, KITCHEN_GATED_TOOLS

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

        assert FLEET_TOOLS, "FLEET_TOOLS must be non-empty for this assertion to be meaningful"
        assert FLEET_DISPATCH_TOOLS, (
            "FLEET_DISPATCH_TOOLS must be non-empty for this assertion to be meaningful"
        )
        assert FREE_RANGE_TOOLS, (
            "FREE_RANGE_TOOLS must be non-empty for this assertion to be meaningful"
        )
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
        from autoskillit.core import (
            EVIDENCE_READER_TOOLS,
            FLEET_TOOLS,
            GATED_TOOLS,
            HEADLESS_TOOLS,
        )
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
        assert tool_names.isdisjoint(EVIDENCE_READER_TOOLS)

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
        from autoskillit.core import (
            EVIDENCE_READER_TOOLS,
            FLEET_DISPATCH_TOOLS,
            FLEET_TOOLS,
            GATED_TOOLS,
        )
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}
        kitchen_tools = GATED_TOOLS - FLEET_TOOLS - FLEET_DISPATCH_TOOLS - EVIDENCE_READER_TOOLS
        for name in kitchen_tools:
            assert name in tool_names, f"{name} should be visible for orchestrator+headless"
        assert tool_names.isdisjoint(EVIDENCE_READER_TOOLS)

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
        from autoskillit.core import GATED_TOOLS, HEADLESS_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}
        assert "test_check" in tool_names, "test_check should be visible for skill+headless"
        assert "post_pr_review" in tool_names
        assert "delegate_evidence_reader" in tool_names
        for name in GATED_TOOLS - HEADLESS_TOOLS:
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
        from autoskillit.core import GATED_TOOLS, HEADLESS_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS_AUTO_GATE", raising=False)
        _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}
        assert "test_check" in tool_names, "test_check should be visible for skill+headless"
        for name in GATED_TOOLS - HEADLESS_TOOLS:
            assert name not in tool_names, f"{name} (kitchen) should be hidden for skill+headless"

    @pytest.mark.anyio
    async def test_skill_headless_auto_gate_zero_only_headless(self, monkeypatch):
        from autoskillit.core import GATED_TOOLS, HEADLESS_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS_AUTO_GATE", "0")
        _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}
        assert "test_check" in tool_names, "test_check should be visible for skill+headless+gate=0"
        for name in GATED_TOOLS - HEADLESS_TOOLS:
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
        from autoskillit.core import (
            EVIDENCE_READER_TOOLS,
            FLEET_DISPATCH_TOOLS,
            FLEET_TOOLS,
            GATED_TOOLS,
        )
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.delenv("AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS", raising=False)
        _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}

        kitchen_tools = GATED_TOOLS - FLEET_TOOLS - FLEET_DISPATCH_TOOLS - EVIDENCE_READER_TOOLS
        for name in kitchen_tools:
            assert name in tool_names
        assert tool_names.isdisjoint(EVIDENCE_READER_TOOLS)

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

        from autoskillit.core import EVIDENCE_READER_TOOLS, GATED_TOOLS, HEADLESS_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            _apply_session_type_visibility()

        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}
        assert "test_check" in tool_names, "test_check should be visible for bridge HEADLESS=1"
        assert "delegate_evidence_reader" in tool_names
        assert EVIDENCE_READER_TOOLS.isdisjoint(tool_names)
        for name in GATED_TOOLS - HEADLESS_TOOLS:
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

    @pytest.mark.anyio
    async def test_orchestrator_headless_hides_fleet_tools(self, monkeypatch):
        """Regression guard: fleet tools must NOT be visible in orchestrator+headless sessions."""
        from autoskillit.core import FLEET_DISPATCH_TOOLS, FLEET_MODE_ENV_VAR, FLEET_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.delenv(FLEET_MODE_ENV_VAR, raising=False)
        _apply_session_type_visibility()

        visible = {t.name for t in await mcp.list_tools()}
        assert visible.isdisjoint(FLEET_TOOLS), (
            f"Fleet tools visible in orchestrator+headless: {visible & FLEET_TOOLS}"
        )
        assert visible.isdisjoint(FLEET_DISPATCH_TOOLS), (
            f"Fleet-dispatch visible in orchestrator+headless: {visible & FLEET_DISPATCH_TOOLS}"
        )

    @pytest.mark.anyio
    async def test_orchestrator_interactive_hides_fleet_tools(self, monkeypatch):
        """Regression guard: fleet tools must NOT be visible in orchestrator+interactive
        sessions"""
        from autoskillit.core import FLEET_DISPATCH_TOOLS, FLEET_MODE_ENV_VAR, FLEET_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
        monkeypatch.delenv(FLEET_MODE_ENV_VAR, raising=False)
        _apply_session_type_visibility()

        visible = {t.name for t in await mcp.list_tools()}
        assert visible.isdisjoint(FLEET_TOOLS), (
            f"Fleet tools visible in orchestrator+interactive: {visible & FLEET_TOOLS}"
        )
        assert visible.isdisjoint(FLEET_DISPATCH_TOOLS), (
            f"Fleet-dispatch visible in orchestrator+interactive: {visible & FLEET_DISPATCH_TOOLS}"
        )

    @pytest.mark.anyio
    async def test_skill_headless_hides_fleet_tools(self, monkeypatch):
        """Regression guard: fleet tools must NOT be visible in skill+headless sessions."""
        from autoskillit.core import FLEET_DISPATCH_TOOLS, FLEET_MODE_ENV_VAR, FLEET_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.delenv(FLEET_MODE_ENV_VAR, raising=False)
        _apply_session_type_visibility()

        visible = {t.name for t in await mcp.list_tools()}
        assert visible.isdisjoint(FLEET_TOOLS), (
            f"Fleet tools visible in skill+headless: {visible & FLEET_TOOLS}"
        )
        assert visible.isdisjoint(FLEET_DISPATCH_TOOLS), (
            f"Fleet-dispatch tools visible in skill+headless: {visible & FLEET_DISPATCH_TOOLS}"
        )

    @pytest.mark.anyio
    async def test_skill_interactive_hides_fleet_tools(self, monkeypatch):
        """Regression guard: fleet tools must NOT be visible in skill+interactive sessions."""
        from autoskillit.core import FLEET_DISPATCH_TOOLS, FLEET_MODE_ENV_VAR, FLEET_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
        monkeypatch.delenv(FLEET_MODE_ENV_VAR, raising=False)
        _apply_session_type_visibility()

        visible = {t.name for t in await mcp.list_tools()}
        assert visible.isdisjoint(FLEET_TOOLS), (
            f"Fleet tools visible in skill+interactive: {visible & FLEET_TOOLS}"
        )
        assert visible.isdisjoint(FLEET_DISPATCH_TOOLS), (
            f"Fleet-dispatch tools visible in skill+interactive: {visible & FLEET_DISPATCH_TOOLS}"
        )

    @pytest.mark.anyio
    async def test_no_session_type_hides_fleet_tools(self, monkeypatch):
        """Regression guard: fleet tools must NOT be visible when no session type is set."""
        from autoskillit.core import FLEET_DISPATCH_TOOLS, FLEET_MODE_ENV_VAR, FLEET_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
        monkeypatch.delenv(FLEET_MODE_ENV_VAR, raising=False)
        _apply_session_type_visibility()

        visible = {t.name for t in await mcp.list_tools()}
        assert visible.isdisjoint(FLEET_TOOLS), (
            f"Fleet tools visible with no session type: {visible & FLEET_TOOLS}"
        )
        assert visible.isdisjoint(FLEET_DISPATCH_TOOLS), (
            f"Fleet-dispatch tools visible with no session type: {visible & FLEET_DISPATCH_TOOLS}"
        )

    @pytest.mark.anyio
    async def test_non_notification_backend_gets_kitchen_pre_reveal(self, build_ctx, monkeypatch):
        """Non-notification backend gets kitchen tools pre-revealed via lifespan boot."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from autoskillit.core import (
            HEADLESS_ENV_VAR,
        )
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server import mcp
        from autoskillit.server._lifespan import _skill_auto_gate_boot

        monkeypatch.delenv(HEADLESS_ENV_VAR, raising=False)

        mock_backend = MagicMock()
        mock_backend.capabilities.supports_tool_list_changed = False
        ctx = build_ctx(backend=mock_backend)
        ctx.gate = DefaultGateState(enabled=False)

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server._lifespan.register_active_kitchen"):
                    await _skill_auto_gate_boot(ctx)

        assert ctx.gate.enabled is True, (
            "gate must be enabled after _skill_auto_gate_boot pre-reveal"
        )

        tools = list(await mcp.list_tools())
        tool_names = {t.name for t in tools}
        assert KITCHEN_GATED_TOOLS.issubset(tool_names), (
            "All kitchen-tagged gated tools should be visible for non-notification backend"
        )

    @pytest.mark.anyio
    async def test_non_notification_backend_plan_review_pre_revealed(self, build_ctx, monkeypatch):
        """Non-notification backend gets plan-review resources pre-revealed via lifespan boot."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from autoskillit.core import HEADLESS_ENV_VAR
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server import mcp
        from autoskillit.server._lifespan import _food_truck_auto_gate_boot

        monkeypatch.delenv(HEADLESS_ENV_VAR, raising=False)

        mock_backend = MagicMock()
        mock_backend.capabilities.supports_tool_list_changed = False
        ctx = build_ctx(backend=mock_backend)
        ctx.gate = DefaultGateState(enabled=False)

        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server._misc._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server._lifespan.register_active_kitchen"):
                    await _food_truck_auto_gate_boot(ctx)

        assert ctx.gate.enabled is True, (
            "gate must be enabled after _food_truck_auto_gate_boot pre-reveal"
        )

        templates = await mcp.list_resource_templates()
        uris = {t.uri_template for t in templates}
        assert "agent://plan-review/{name}" in uris, (
            "plan-review resource template should be visible for non-notification backend"
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


class TestExplorerBindingVisibility:
    """A bound terminal explorer is an MCP allowlist, not a kitchen session."""

    @pytest.fixture(autouse=True)
    def _reset_mcp_visibility(self):
        from autoskillit.core import ALL_VISIBILITY_TAGS
        from autoskillit.server import mcp

        mcp._transforms.clear()
        for tag in sorted(ALL_VISIBILITY_TAGS):
            mcp.disable(tags={tag})
        yield
        mcp._transforms.clear()
        for tag in sorted(ALL_VISIBILITY_TAGS):
            mcp.disable(tags={tag})

    @pytest.mark.parametrize(
        "role",
        (
            "shared-explorer-session",
            "semantic-code-navigator",
            "repository-impact-profiler",
        ),
    )
    @pytest.mark.anyio
    async def test_unverified_explorer_environment_reveals_no_broker_tools(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        role: str,
    ) -> None:
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_EXPLORATION_CAPABILITY", "explore_test_capability")
        monkeypatch.setenv("AUTOSKILLIT_EXPLORATION_ROLE", role)
        monkeypatch.setenv("AUTOSKILLIT_EXPLORATION_SESSION_ID", "headless-test")
        monkeypatch.setenv(
            "AUTOSKILLIT_EXPLORATION_AUTHORITY_PATH",
            str(tmp_path / ".autoskillit-exploration-authority.json"),
        )
        _apply_session_type_visibility()

        visible = {tool.name for tool in await mcp.list_tools()}
        assert not (visible & EXPLORATION_TOOLS)

    @pytest.mark.anyio
    async def test_verified_explorer_authority_reveals_only_broker_tools(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock

        from autoskillit.core import RepositoryIdentity, RepositorySnapshot, SessionType
        from autoskillit.pipeline import OwnerBoundExplorationContextStore
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server import _lifespan, mcp

        project = tmp_path / "project"
        cwd = project / "worktree"
        authority_home = tmp_path / "session"
        for path in (cwd, authority_home):
            path.mkdir(parents=True)
        service = MagicMock()
        service.capture_snapshot.return_value = RepositorySnapshot(
            RepositoryIdentity("test-repository", "test-revision"),
            tree_digest="test-tree",
            collector_manifest_digest="test-manifest",
        )
        parent: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
            trusted_root=project,
            service=service,
        )
        binding = parent.bind_launch(
            owner_id="uid:1000",
            role="semantic-code-navigator",
            session_id="session-a",
            cwd=cwd,
            repository_root=project,
            source_identity="bundled:semantic-code-navigator:digest",
            authority_home=authority_home,
        )
        for key, value in binding.provider_extras().items():
            monkeypatch.setenv(key, value)
        gate = DefaultGateState()
        child: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
            trusted_root=project
        )
        monkeypatch.chdir(cwd)
        ordinary_boot = AsyncMock()
        monkeypatch.setitem(_lifespan._LIFESPAN_BOOT_REGISTRY, SessionType.SKILL, ordinary_boot)
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
        boot_ctx = SimpleNamespace(exploration_context_store=child, gate=gate, backend=None)
        monkeypatch.setattr(_lifespan, "_get_ctx_or_none", lambda: boot_ctx)
        monkeypatch.setattr(_lifespan, "run_startup_fix_required_coverage_check", lambda: None)
        monkeypatch.setattr(_lifespan, "write_readiness_sentinel", lambda: None)
        monkeypatch.setattr(_lifespan, "cleanup_readiness_sentinel", lambda: None)
        monkeypatch.setattr(_lifespan, "_finalize_recorder", lambda: None)

        def _discard_background(coroutine, *, label):
            del label
            coroutine.close()
            return asyncio.create_task(asyncio.sleep(0))

        monkeypatch.setattr(_lifespan, "create_background_task", _discard_background)

        async with _lifespan._autoskillit_lifespan(SimpleNamespace()):
            ordinary_boot.assert_not_awaited()
            assert gate.enabled is True
            assert {tool.name for tool in await mcp.list_tools()} == EXPLORATION_TOOLS
            assert list(await mcp.list_resources()) == []
            assert list(await mcp.list_resource_templates()) == []

    @pytest.mark.anyio
    async def test_missing_explorer_authority_fails_closed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from autoskillit.core import SessionType
        from autoskillit.pipeline import OwnerBoundExplorationContextStore
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server import mcp
        from autoskillit.server._lifespan import (
            _LIFESPAN_BOOT_REGISTRY,
            _run_lifespan_session_boot,
        )

        for name, value in {
            "AUTOSKILLIT_EXPLORATION_CAPABILITY": "not-a-capability",
            "AUTOSKILLIT_EXPLORATION_ROLE": "semantic-code-navigator",
            "AUTOSKILLIT_EXPLORATION_SESSION_ID": "session-a",
            "AUTOSKILLIT_EXPLORATION_AUTHORITY_PATH": str(tmp_path / "missing-authority.json"),
        }.items():
            monkeypatch.setenv(name, value)
        gate = DefaultGateState()
        ordinary_boot = AsyncMock()
        monkeypatch.setitem(_LIFESPAN_BOOT_REGISTRY, SessionType.SKILL, ordinary_boot)
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")

        await _run_lifespan_session_boot(
            SimpleNamespace(
                exploration_context_store=OwnerBoundExplorationContextStore[object](),
                gate=gate,
            )
        )

        ordinary_boot.assert_awaited_once()
        assert gate.enabled is False
        assert not ({tool.name for tool in await mcp.list_tools()} & EXPLORATION_TOOLS)

    @pytest.mark.anyio
    async def test_parent_without_explorer_binding_keeps_free_range_tools(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from autoskillit.core import FREE_RANGE_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        for name in (
            "AUTOSKILLIT_EXPLORATION_CAPABILITY",
            "AUTOSKILLIT_EXPLORATION_ROLE",
            "AUTOSKILLIT_EXPLORATION_SESSION_ID",
            "AUTOSKILLIT_EXPLORATION_AUTHORITY_PATH",
        ):
            monkeypatch.delenv(name, raising=False)
        _apply_session_type_visibility()

        assert FREE_RANGE_TOOLS <= {tool.name for tool in await mcp.list_tools()}


class TestEvidenceReaderBindingVisibility:
    """Evidence-reader startup identity grants only its two broker tools."""

    @staticmethod
    def _set_complete_identity(monkeypatch: pytest.MonkeyPatch) -> None:
        from autoskillit.core import (
            EVIDENCE_READER_AUTHORITY_ENV_VAR,
            EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR,
            EVIDENCE_READER_CAPABILITY_ENV_VAR,
        )

        monkeypatch.setenv(EVIDENCE_READER_AUTHORITY_ENV_VAR, "sha256:" + ("a" * 64))
        monkeypatch.setenv(EVIDENCE_READER_CAPABILITY_ENV_VAR, "reader-capability")
        monkeypatch.setenv(EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR, "/sealed/authority.json")

    @pytest.mark.anyio
    async def test_complete_startup_identity_reveals_exact_reader_surface(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock

        from autoskillit.core import EVIDENCE_READER_TOOLS, SessionType
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server import _lifespan, mcp
        from autoskillit.server.tools import _evidence_reader

        self._set_complete_identity(monkeypatch)
        validate = Mock()
        monkeypatch.setattr(_evidence_reader, "validate_evidence_reader_startup", validate)
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
        ordinary_boot = AsyncMock()
        monkeypatch.setitem(_lifespan._LIFESPAN_BOOT_REGISTRY, SessionType.SKILL, ordinary_boot)
        gate = DefaultGateState()

        await _lifespan._run_lifespan_session_boot(SimpleNamespace(gate=gate))

        visible = {tool.name for tool in await mcp.list_tools()}
        assert (
            visible
            == EVIDENCE_READER_TOOLS
            == {
                "read_authorized_artifact",
                "get_authorized_artifact_page",
            }
        )
        assert visible.isdisjoint(
            {
                "delegate_evidence_reader",
                "open_kitchen",
                "close_kitchen",
                "run_cmd",
                "run_python",
                "run_skill",
                "fetch_github_issue",
                "submit_exploration_query",
            }
        )
        assert list(await mcp.list_resources()) == []
        assert list(await mcp.list_resource_templates()) == []
        assert gate.enabled is True
        ordinary_boot.assert_not_awaited()
        validate.assert_called_once()

    @pytest.mark.anyio
    async def test_complete_but_unauthenticated_identity_aborts_startup(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import Mock

        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server import _lifespan
        from autoskillit.server.tools import _evidence_reader

        self._set_complete_identity(monkeypatch)
        monkeypatch.setattr(
            _evidence_reader,
            "validate_evidence_reader_startup",
            Mock(side_effect=_evidence_reader.EvidenceReaderError("authority_tampered")),
        )
        gate = DefaultGateState()

        with pytest.raises(_evidence_reader.EvidenceReaderError, match="authority_tampered"):
            await _lifespan._run_lifespan_session_boot(SimpleNamespace(gate=gate))

        assert gate.enabled is False

    @pytest.mark.anyio
    async def test_complete_identity_with_zero_matching_tools_aborts_startup(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import Mock

        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server import _lifespan, mcp
        from autoskillit.server.tools import _evidence_reader

        self._set_complete_identity(monkeypatch)
        monkeypatch.setattr(
            _evidence_reader,
            "validate_evidence_reader_startup",
            Mock(),
        )

        async def no_visible_tools():
            return []

        monkeypatch.setattr(mcp, "list_tools", no_visible_tools)
        gate = DefaultGateState()

        with pytest.raises(RuntimeError, match="tool projection is incomplete"):
            await _lifespan._run_lifespan_session_boot(SimpleNamespace(gate=gate))

        assert gate.enabled is False

    @pytest.mark.anyio
    async def test_complete_ambient_identity_does_not_reveal_brokers_before_boot(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from autoskillit.core import EVIDENCE_READER_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        self._set_complete_identity(monkeypatch)
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")

        _apply_session_type_visibility()

        visible = {tool.name for tool in await mcp.list_tools()}
        assert visible.isdisjoint(EVIDENCE_READER_TOOLS)

    @pytest.mark.parametrize("identity", ["partial", "empty"])
    @pytest.mark.anyio
    async def test_malformed_startup_identity_fails_closed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        identity: str,
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from autoskillit.core import (
            EVIDENCE_READER_AUTHORITY_ENV_VAR,
            EVIDENCE_READER_CAPABILITY_ENV_VAR,
            EVIDENCE_READER_ENV_FORWARD_VARS,
            EVIDENCE_READER_TOOLS,
            SessionType,
        )
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server import _lifespan, mcp

        for name in EVIDENCE_READER_ENV_FORWARD_VARS:
            monkeypatch.delenv(name, raising=False)
        if identity == "partial":
            monkeypatch.setenv(EVIDENCE_READER_AUTHORITY_ENV_VAR, "sha256:" + ("a" * 64))
        else:
            self._set_complete_identity(monkeypatch)
            monkeypatch.setenv(EVIDENCE_READER_CAPABILITY_ENV_VAR, "")
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
        ordinary_boot = AsyncMock()
        monkeypatch.setitem(_lifespan._LIFESPAN_BOOT_REGISTRY, SessionType.SKILL, ordinary_boot)
        gate = DefaultGateState()

        with pytest.raises(RuntimeError, match="startup identity is malformed"):
            await _lifespan._run_lifespan_session_boot(SimpleNamespace(gate=gate))

        assert gate.enabled is False
        ordinary_boot.assert_not_awaited()
        assert {tool.name for tool in await mcp.list_tools()}.isdisjoint(EVIDENCE_READER_TOOLS)

    @pytest.mark.parametrize("gate_enabled", [False, True])
    @pytest.mark.anyio
    async def test_absent_reader_identity_preserves_ordinary_kitchen_boot(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gate_enabled: bool,
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from autoskillit.core import (
            EVIDENCE_READER_ENV_FORWARD_VARS,
            EVIDENCE_READER_TOOLS,
            SessionType,
        )
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server import _lifespan, mcp

        for name in EVIDENCE_READER_ENV_FORWARD_VARS:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
        gate = DefaultGateState()
        if gate_enabled:
            gate.enable()
        ordinary_boot = AsyncMock()
        monkeypatch.setitem(_lifespan._LIFESPAN_BOOT_REGISTRY, SessionType.SKILL, ordinary_boot)

        await _lifespan._run_lifespan_session_boot(
            SimpleNamespace(gate=gate, exploration_context_store=None)
        )

        ordinary_boot.assert_awaited_once()
        assert gate.enabled is gate_enabled
        assert {tool.name for tool in await mcp.list_tools()}.isdisjoint(EVIDENCE_READER_TOOLS)
