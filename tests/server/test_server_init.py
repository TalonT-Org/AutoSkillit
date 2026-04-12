"""Tests for kitchen gate access, visibility, and subset management."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import structlog.testing

from autoskillit.pipeline.gate import DefaultGateState
from autoskillit.server.helpers import _require_enabled
from autoskillit.server.tools_kitchen import _close_kitchen_handler, _open_kitchen_handler


class TestKitchenVisibility:
    """FastMCP v3 tag-based visibility: kitchen tools hidden at startup."""

    @pytest.mark.anyio
    async def test_kitchen_tools_hidden_at_startup(self):
        """No kitchen tool (gated or headless-tagged) appears in tools/list for a fresh session."""
        from fastmcp.client import Client

        from autoskillit.core.types import HEADLESS_TOOLS
        from autoskillit.pipeline.gate import GATED_TOOLS
        from autoskillit.server import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = {t.name for t in tools}
            for name in GATED_TOOLS:
                assert name not in tool_names, f"{name} should be hidden at startup"
            # test_check has kitchen tag so it is also hidden at startup
            for name in HEADLESS_TOOLS:
                assert name not in tool_names, f"{name} should be hidden at startup"

    @pytest.mark.anyio
    async def test_ungated_tools_visible_at_startup(self):
        """Only free-range tools (open_kitchen, close_kitchen) are visible at startup."""
        from fastmcp.client import Client

        from autoskillit.pipeline.gate import UNGATED_TOOLS
        from autoskillit.server import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = {t.name for t in tools}
            for name in UNGATED_TOOLS:
                assert name in tool_names, f"{name} should be visible at startup"
            # test_check must NOT be visible at startup (has kitchen tag)
            assert "test_check" not in tool_names, "test_check should not be visible at startup"


class TestGatedToolAccess:
    """Prompt-gated tool access: tools disabled by default, user-only activation."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        """Override the global autouse fixture — start disabled for gate tests."""
        tool_ctx.gate = DefaultGateState(enabled=False)

    @pytest.mark.anyio
    async def test_tools_return_error_when_disabled(self, tool_ctx):
        """All tools return standard gate error when gate is disabled."""
        from autoskillit.server.tools_execution import run_cmd

        result = json.loads(await run_cmd(cmd="echo hi", cwd="/tmp"))
        assert result["success"] is False
        assert result["is_error"] is True
        assert "not enabled" in result["result"].lower()

    @pytest.mark.anyio
    async def test_tools_work_after_enable(self, tool_ctx):
        """After open_kitchen prompt handler sets the flag, tools execute normally."""
        from unittest.mock import AsyncMock

        from autoskillit.server import tools_kitchen as tools_kitchen_mod
        from autoskillit.server.tools_execution import run_cmd
        from tests.conftest import _make_result

        with patch.object(tools_kitchen_mod, "_prime_quota_cache", new=AsyncMock()):
            with patch.object(tools_kitchen_mod, "_write_hook_config"):
                await _open_kitchen_handler()
        tool_ctx.runner.push(_make_result(0, "hello\n", ""))
        result = json.loads(await run_cmd(cmd="echo hello", cwd="/tmp"))
        assert result["success"] is True

    @pytest.mark.anyio
    async def test_disable_reverses_enable(self, tool_ctx):
        """After close_kitchen prompt handler, tools return error again."""
        from unittest.mock import AsyncMock

        from autoskillit.server import tools_kitchen as tools_kitchen_mod
        from autoskillit.server.tools_execution import run_cmd

        with patch.object(tools_kitchen_mod, "_prime_quota_cache", new=AsyncMock()):
            with patch.object(tools_kitchen_mod, "_write_hook_config"):
                await _open_kitchen_handler()
        _close_kitchen_handler()
        result = json.loads(await run_cmd(cmd="echo hi", cwd="/tmp"))
        assert result["success"] is False
        assert result["is_error"] is True

    @pytest.mark.anyio
    async def test_kitchen_tools_registered_as_tools(self):
        """open_kitchen and close_kitchen are registered as tools on the server."""
        from fastmcp.client import Client

        from autoskillit.server import mcp

        async with Client(mcp) as client:
            tool_names = {t.name for t in await client.list_tools()}
        assert "open_kitchen" in tool_names
        assert "close_kitchen" in tool_names

    @pytest.mark.anyio
    async def test_kitchen_tools_not_registered_as_prompts(self):
        """open_kitchen and close_kitchen are tools, not MCP prompts."""
        from fastmcp.client import Client

        from autoskillit.server import mcp

        async with Client(mcp) as client:
            prompt_names = {p.name for p in await client.list_prompts()}
        assert "open_kitchen" not in prompt_names
        assert "close_kitchen" not in prompt_names

    @pytest.mark.anyio
    async def test_run_python_gated(self):
        """run_python requires tools to be enabled."""
        from autoskillit.server.tools_execution import run_python

        result = json.loads(await run_python(callable="json.dumps", args={"obj": 1}))
        assert result["success"] is False
        assert result["is_error"] is True
        assert "not enabled" in result["result"].lower()

    def test_gate_error_structure(self):
        """_require_enabled returns standard schema with activation instructions."""
        error = _require_enabled()
        assert error is not None
        parsed = json.loads(error)
        assert parsed["success"] is False
        assert parsed["is_error"] is True
        assert parsed["subtype"] == "gate_error"
        assert "open_kitchen" in parsed["result"]


class TestGateTransitionLogs:
    """N11: open_kitchen and close_kitchen handlers emit structured log events."""

    @pytest.mark.anyio
    async def test_open_kitchen_logs_gate_open(self, tool_ctx):
        from unittest.mock import AsyncMock

        from autoskillit.server import tools_kitchen as tools_kitchen_mod

        with patch.object(tools_kitchen_mod, "_prime_quota_cache", new=AsyncMock()):
            with patch.object(tools_kitchen_mod, "_write_hook_config"):
                with structlog.testing.capture_logs() as logs:
                    await _open_kitchen_handler()
        assert any(
            entry.get("event") == "open_kitchen" and entry.get("gate_state") == "open"
            for entry in logs
        )

    def test_close_kitchen_logs_gate_closed(self, tool_ctx):
        with structlog.testing.capture_logs() as logs:
            _close_kitchen_handler()
        assert any(
            entry.get("event") == "close_kitchen" and entry.get("gate_state") == "closed"
            for entry in logs
        )


class TestKitchenToolSchemas:
    """Kitchen tool descriptions must be accurate, current, and cooking-themed."""

    async def _get_kitchen_tools(self):
        from fastmcp.client import Client

        from autoskillit.server import mcp

        names = {"open_kitchen", "close_kitchen"}
        async with Client(mcp) as client:
            tools = await client.list_tools()
        return {t.name: t for t in tools if t.name in names}

    @pytest.mark.anyio
    async def test_tool_descriptions_are_cooking_themed(self):
        """Kitchen tool descriptions must use cooking vocabulary."""
        tools = await self._get_kitchen_tools()
        for name, tool in tools.items():
            desc = (tool.description or "").lower()
            assert "kitchen" in desc, (
                f"Tool '{name}' description must contain cooking vocabulary ('kitchen'): {desc!r}"
            )

    @pytest.mark.anyio
    async def test_close_kitchen_returns_cooking_confirmation(self, tool_ctx):
        """close_kitchen must return a cooking-themed closing message."""
        from unittest.mock import AsyncMock, MagicMock

        from autoskillit.server.tools_kitchen import close_kitchen

        mock_ctx = MagicMock()
        mock_ctx.reset_visibility = AsyncMock()
        result = await close_kitchen(ctx=mock_ctx)
        assert "kitchen" in result.lower(), (
            f"close_kitchen return message must be cooking-themed: {result!r}"
        )


class TestOpenKitchenVersionReporting:
    """open_kitchen returns version info and warns on mismatch."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        from autoskillit.pipeline.gate import DefaultGateState

        tool_ctx.gate = DefaultGateState(enabled=False)

    @staticmethod
    def _prompt_text(result) -> str:
        import json as _json

        try:
            parsed = _json.loads(result)
            return parsed.get("content", result)
        except (ValueError, TypeError):
            return result

    @pytest.mark.anyio
    async def test_open_kitchen_instructs_status_call(self):
        from unittest.mock import AsyncMock, MagicMock

        from autoskillit.server import tools_kitchen as tools_kitchen_mod
        from autoskillit.server.tools_kitchen import open_kitchen

        mock_ctx = MagicMock()
        mock_ctx.enable_components = AsyncMock()
        with patch.object(tools_kitchen_mod, "_prime_quota_cache", new=AsyncMock()):
            with patch.object(tools_kitchen_mod, "_write_hook_config"):
                result = await open_kitchen(ctx=mock_ctx)
        msg = self._prompt_text(result)
        assert "kitchen_status" in msg

    @pytest.mark.anyio
    async def test_open_kitchen_carries_orchestrator_contract(self):
        """open_kitchen tool must use prohibition framing and name all forbidden tools."""
        from unittest.mock import AsyncMock, MagicMock

        from autoskillit.server import PIPELINE_FORBIDDEN_TOOLS
        from autoskillit.server import tools_kitchen as tools_kitchen_mod
        from autoskillit.server.tools_kitchen import open_kitchen

        mock_ctx = MagicMock()
        mock_ctx.enable_components = AsyncMock()
        with patch.object(tools_kitchen_mod, "_prime_quota_cache", new=AsyncMock()):
            with patch.object(tools_kitchen_mod, "_write_hook_config"):
                result = await open_kitchen(ctx=mock_ctx)
        msg = self._prompt_text(result)

        # Must name every forbidden tool
        missing = [t for t in PIPELINE_FORBIDDEN_TOOLS if t not in msg]
        assert not missing, f"open_kitchen tool missing forbidden tools: {missing}"

        # Must use prohibition framing
        prohibition_terms = ["NEVER", "Do NOT", "MUST NOT", "are prohibited"]
        assert any(term in msg for term in prohibition_terms), (
            "open_kitchen tool must use prohibition framing "
            f"(one of {prohibition_terms}), got: {msg[:200]}"
        )

        # Must NOT use the conditional escape-hatch phrasing
        assert "During pipeline execution, only use" not in msg, (
            "open_kitchen tool must not use conditional 'During pipeline execution, only use' "
            "phrasing — the restriction should be unconditional"
        )

    @pytest.mark.anyio
    async def test_open_kitchen_still_enables_on_mismatch(self, tmp_path, tool_ctx):
        from unittest.mock import AsyncMock, MagicMock

        from autoskillit.server import tools_kitchen as tools_kitchen_mod
        from autoskillit.server.tools_kitchen import open_kitchen

        mock_ctx = MagicMock()
        mock_ctx.enable_components = AsyncMock()
        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"name": "autoskillit", "version": "0.0.0"})
        )
        tool_ctx.plugin_dir = str(tmp_path)
        with patch.object(tools_kitchen_mod, "_prime_quota_cache", new=AsyncMock()):
            with patch.object(tools_kitchen_mod, "_write_hook_config"):
                await open_kitchen(ctx=mock_ctx)
        assert tool_ctx.gate.enabled is True


class TestOpenKitchenSousChef:
    """sous-chef/SKILL.md content is injected at open_kitchen activation time."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        tool_ctx.gate = DefaultGateState(enabled=False)

    @staticmethod
    def _prompt_text(result) -> str:
        import json as _json

        try:
            parsed = _json.loads(result)
            return parsed.get("content", result)
        except (ValueError, TypeError):
            return result

    @pytest.mark.anyio
    async def test_sous_chef_rules_injected_at_open_kitchen(self):
        """open_kitchen must include sous-chef global orchestration rules."""
        from unittest.mock import AsyncMock, MagicMock

        import autoskillit.server.tools_kitchen as tools_kitchen_mod
        from autoskillit.server.tools_kitchen import open_kitchen

        mock_ctx = MagicMock()
        mock_ctx.enable_components = AsyncMock()
        with patch.object(tools_kitchen_mod, "_prime_quota_cache", new=AsyncMock()):
            with patch.object(tools_kitchen_mod, "_write_hook_config"):
                result = await open_kitchen(ctx=mock_ctx)
        text = self._prompt_text(result)
        assert "MULTI-PART PLAN SEQUENCING" in text
        assert "retry-worktree" in text.lower()

    @pytest.mark.anyio
    async def test_open_kitchen_degrades_gracefully_without_sous_chef(self, monkeypatch, tmp_path):
        """open_kitchen must not raise when sous-chef/SKILL.md is absent."""
        from unittest.mock import AsyncMock, MagicMock

        import autoskillit.server.tools_kitchen as tools_kitchen_mod
        from autoskillit.server.tools_kitchen import open_kitchen

        mock_ctx = MagicMock()
        mock_ctx.enable_components = AsyncMock()
        monkeypatch.setattr(tools_kitchen_mod, "pkg_root", lambda: tmp_path)
        with patch.object(tools_kitchen_mod, "_prime_quota_cache", new=AsyncMock()):
            with patch.object(tools_kitchen_mod, "_write_hook_config"):
                result = await open_kitchen(ctx=mock_ctx)  # must not raise
        text = self._prompt_text(result)
        assert "Kitchen is open" in text
        assert "kitchen_status" in text


@pytest.mark.anyio
async def test_open_kitchen_has_no_update_advisory(tool_ctx):
    """REQ-APP-004: open_kitchen tool contains no recipe update advisory."""
    from unittest.mock import AsyncMock, MagicMock

    from autoskillit.pipeline.gate import DefaultGateState
    from autoskillit.server import tools_kitchen as tools_kitchen_mod
    from autoskillit.server.tools_kitchen import open_kitchen

    # Ensure kitchen is closed before calling open_kitchen
    tool_ctx.gate = DefaultGateState(enabled=False)
    mock_ctx = MagicMock()
    mock_ctx.enable_components = AsyncMock()
    with patch.object(tools_kitchen_mod, "_prime_quota_cache", new=AsyncMock()):
        with patch.object(tools_kitchen_mod, "_write_hook_config"):
            text = await open_kitchen(ctx=mock_ctx)

    assert "RECIPE UPDATE AVAILABLE" not in text
    assert "accept_recipe_update" not in text
    assert "decline_recipe_update" not in text


# T-VIS-001
def test_initialize_applies_subset_disables(monkeypatch):
    """_initialize() must call mcp.disable(tags={subset}) for each disabled subset."""
    from unittest.mock import MagicMock

    from autoskillit.config.settings import AutomationConfig, SubsetsConfig
    from autoskillit.pipeline import ToolContext

    mock_mcp = MagicMock()
    ctx = MagicMock(spec=ToolContext)
    ctx.config = AutomationConfig(subsets=SubsetsConfig(disabled=["github", "ci"]))
    ctx.session_skill_manager = None
    ctx.runner = None

    with patch("autoskillit.server._ctx", None):
        with patch("autoskillit.server.mcp", mock_mcp):
            from autoskillit.server._state import _initialize

            _initialize(ctx)

    disabled_tag_sets = [c.kwargs.get("tags", set()) for c in mock_mcp.disable.call_args_list]
    assert any("github" in tags for tags in disabled_tag_sets)
    assert any("ci" in tags for tags in disabled_tag_sets)


# T-VIS-002
def test_initialize_skips_subset_disable_when_empty(monkeypatch):
    """_initialize() must not call mcp.disable for subsets when list is empty."""
    from unittest.mock import MagicMock

    from autoskillit.config.settings import AutomationConfig, SubsetsConfig
    from autoskillit.pipeline import ToolContext

    mock_mcp = MagicMock()
    ctx = MagicMock(spec=ToolContext)
    ctx.config = AutomationConfig(subsets=SubsetsConfig(disabled=[]))
    ctx.session_skill_manager = None
    ctx.runner = None

    with patch("autoskillit.server.mcp", mock_mcp):
        from autoskillit.server._state import _initialize

        _initialize(ctx)

    mock_mcp.disable.assert_not_called()
