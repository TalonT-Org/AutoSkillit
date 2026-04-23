"""Tests for kitchen gate access, visibility, and subset management."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import structlog.testing

from autoskillit.pipeline.gate import DefaultGateState
from autoskillit.server.helpers import _require_enabled
from autoskillit.server.tools_kitchen import _close_kitchen_handler, _open_kitchen_handler

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


class TestKitchenVisibility:
    """FastMCP v3 tag-based visibility: kitchen tools hidden at startup."""

    @pytest.mark.anyio
    async def test_kitchen_tools_hidden_at_startup(self):
        """No kitchen tool (gated or headless-tagged) appears in tools/list for a fresh session."""
        from fastmcp.client import Client

        from autoskillit.core import HEADLESS_TOOLS
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

    @pytest.mark.anyio
    async def test_redisable_subsets_hides_kitchen_core(self) -> None:
        """When kitchen-core is in disabled subsets, those tools stay hidden after open_kitchen."""
        from unittest.mock import AsyncMock

        from autoskillit.server.tools_kitchen import _redisable_subsets

        mock_ctx = AsyncMock()

        await _redisable_subsets(mock_ctx, ["kitchen-core"])

        mock_ctx.disable_components.assert_called_once_with(tags={"kitchen-core"})


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


# ---------------------------------------------------------------------------
# T9 — server/__init__.py has no shebang
# ---------------------------------------------------------------------------


def test_server_init_no_shebang() -> None:
    """server/__init__.py must not have a shebang line."""
    import autoskillit.server as server_pkg

    src_path = Path(server_pkg.__file__)  # type: ignore[arg-type]
    src = src_path.read_text(encoding="utf-8")
    assert not src.startswith("#!"), "server/__init__.py must not have a shebang"


# ---------------------------------------------------------------------------
# Wire-format compliance (Claude Code #25081)
# ---------------------------------------------------------------------------

# Fields the middleware strips to avoid Claude Code #25081 tool-list rejection.
# Maps wire field name (camelCase) to the FastMCP Tool snake_case attribute name.
# annotations is intentionally absent: it carries readOnlyHint and must be preserved.
_STRIPPED_WIRE_FIELDS: dict[str, str] = {
    "outputSchema": "output_schema",
    "title": "title",
}


class TestWireFormatCompliance:
    """tools/list wire response must be compatible with Claude Code's MCP parser."""

    @pytest.mark.anyio
    async def test_tools_list_contains_no_stripped_fields(self):
        """tools/list wire response must not contain fields that trigger
        Claude Code #25081 (silent full-tool-list rejection).

        Only output_schema and title are stripped. annotations is preserved
        because it carries readOnlyHint for parallel execution semantics.
        """
        from fastmcp.client import Client

        from autoskillit.server import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()
        for tool in tools:
            for wire_field, attr in _STRIPPED_WIRE_FIELDS.items():
                value = getattr(tool, attr, None)
                assert value is None, (
                    f"Tool '{tool.name}' has non-None {wire_field}={value!r}. "
                    f"Claude Code #25081 silently drops ALL tools when this field is present."
                )


class TestClaudeCodeCompatMiddleware:
    """Unit tests for the wire-format sanitization middleware."""

    @pytest.mark.anyio
    async def test_middleware_strips_output_schema(self):
        """Middleware must strip outputSchema from every tool."""
        from unittest.mock import AsyncMock, MagicMock

        from autoskillit.server._wire_compat import ClaudeCodeCompatMiddleware

        mw = ClaudeCodeCompatMiddleware()
        tool = MagicMock()
        tool.name = "test_tool"
        tool.output_schema = {"type": "string"}
        tool.annotations = MagicMock(readOnlyHint=True)
        tool.model_copy.return_value = MagicMock(
            name="test_tool",
            output_schema=None,
            annotations=MagicMock(readOnlyHint=True),
            title=None,
        )

        ctx = MagicMock()
        call_next = AsyncMock(return_value=[tool])

        result = await mw.on_list_tools(ctx, call_next)
        tool.model_copy.assert_called_once_with(
            update={"output_schema": None, "title": None},
        )
        assert result[0].output_schema is None

    @pytest.mark.anyio
    async def test_middleware_preserves_annotations(self):
        """Middleware must preserve annotations (including readOnlyHint) on every tool."""
        from unittest.mock import AsyncMock, MagicMock

        from autoskillit.server._wire_compat import ClaudeCodeCompatMiddleware

        mw = ClaudeCodeCompatMiddleware()
        tool = MagicMock()
        tool.name = "test_tool"
        tool.output_schema = None
        tool.annotations = MagicMock(readOnlyHint=True)
        tool.model_copy.return_value = MagicMock(
            name="test_tool",
            output_schema=None,
            annotations=MagicMock(readOnlyHint=True),
            title=None,
        )

        ctx = MagicMock()
        call_next = AsyncMock(return_value=[tool])

        result = await mw.on_list_tools(ctx, call_next)
        assert result[0].annotations is not None
        assert result[0].annotations.readOnlyHint is True

    @pytest.mark.anyio
    async def test_middleware_preserves_tool_identity(self):
        """Middleware must not alter tool name, description, inputSchema, or annotations."""
        from unittest.mock import AsyncMock, MagicMock

        from autoskillit.server._wire_compat import ClaudeCodeCompatMiddleware

        mw = ClaudeCodeCompatMiddleware()
        tool = MagicMock()
        tool.name = "my_tool"
        tool.description = "Does things"
        tool.parameters = {"type": "object", "properties": {}}
        tool.output_schema = {"type": "string"}
        tool.annotations = MagicMock()
        copy_mock = MagicMock()
        copy_mock.name = "my_tool"
        copy_mock.description = "Does things"
        copy_mock.parameters = {"type": "object", "properties": {}}
        copy_mock.output_schema = None
        copy_mock.annotations = MagicMock(readOnlyHint=True)
        copy_mock.title = None
        tool.model_copy.return_value = copy_mock

        ctx = MagicMock()
        call_next = AsyncMock(return_value=[tool])

        result = await mw.on_list_tools(ctx, call_next)
        assert result[0].name == "my_tool"
        assert result[0].description == "Does things"
        assert result[0].parameters == {"type": "object", "properties": {}}
        assert result[0].annotations is not None


class TestSessionTypeVisibility:
    """3-branch session-type tag visibility dispatch."""

    @pytest.fixture(autouse=True)
    def _reset_mcp_visibility(self):
        """Reset gated tag visibility on the shared mcp singleton before each test."""
        from autoskillit.server import mcp

        mcp.disable(tags={"franchise", "kitchen", "headless"})
        yield
        mcp.disable(tags={"franchise", "kitchen", "headless"})

    @pytest.mark.anyio
    async def test_franchise_enables_franchise_tag(self, monkeypatch):
        from fastmcp.client import Client

        from autoskillit.core import FRANCHISE_TOOLS, GATED_TOOLS, HEADLESS_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "franchise")
        _apply_session_type_visibility()

        async with Client(mcp) as client:
            tools = await client.list_tools()
        tool_names = {t.name for t in tools}

        # Positive: franchise-tagged tools are visible
        for name in FRANCHISE_TOOLS:
            assert name in tool_names, f"{name} should be visible for franchise session"
        # Negative: non-franchise kitchen/headless tools remain hidden
        for name in GATED_TOOLS - FRANCHISE_TOOLS:
            assert name not in tool_names, f"{name} should be hidden for franchise session"
        for name in HEADLESS_TOOLS:
            assert name not in tool_names, f"{name} should be hidden for franchise session"

    @pytest.mark.anyio
    async def test_franchise_tools_retain_kitchen_tag(self, monkeypatch):
        """Franchise-tagged tools must still carry the kitchen tag."""
        from autoskillit.core import FRANCHISE_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "franchise")
        _apply_session_type_visibility()

        all_tools = {t.name: t for t in await mcp.list_tools()}
        for name in FRANCHISE_TOOLS:
            tool = all_tools.get(name)
            assert tool is not None, f"{name} not registered"
            assert "kitchen" in tool.tags, f"{name} must retain kitchen tag"
            assert "franchise" in tool.tags, f"{name} must have franchise tag"
            assert "autoskillit" in tool.tags, f"{name} must retain autoskillit tag"

    @pytest.mark.anyio
    async def test_franchise_tools_constant_matches_tagged_tools(self, monkeypatch):
        """FRANCHISE_TOOLS constant matches exactly the tools with franchise tag."""
        from autoskillit.core import FRANCHISE_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "franchise")
        _apply_session_type_visibility()

        all_tools = {t.name: t for t in await mcp.list_tools()}
        tagged = {name for name, t in all_tools.items() if "franchise" in t.tags}
        assert tagged == FRANCHISE_TOOLS, (
            f"FRANCHISE_TOOLS constant out of sync. "
            f"Extra in constant: {FRANCHISE_TOOLS - tagged}. "
            f"Extra on server: {tagged - FRANCHISE_TOOLS}."
        )

    @pytest.mark.anyio
    async def test_orchestrator_headless_enables_kitchen_tag(self, monkeypatch):
        from fastmcp.client import Client

        from autoskillit.core import GATED_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        _apply_session_type_visibility()

        async with Client(mcp) as client:
            tools = await client.list_tools()
        tool_names = {t.name for t in tools}
        for name in GATED_TOOLS:
            assert name in tool_names, f"{name} should be visible for orchestrator+headless"

    @pytest.mark.anyio
    async def test_orchestrator_interactive_no_pre_reveal(self, monkeypatch):
        from fastmcp.client import Client

        from autoskillit.core import GATED_TOOLS, HEADLESS_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
        _apply_session_type_visibility()

        async with Client(mcp) as client:
            tools = await client.list_tools()
        tool_names = {t.name for t in tools}
        for name in GATED_TOOLS:
            assert name not in tool_names, f"{name} should be hidden for orchestrator+interactive"
        for name in HEADLESS_TOOLS:
            assert name not in tool_names, f"{name} should be hidden for orchestrator+interactive"

    @pytest.mark.anyio
    async def test_leaf_headless_enables_headless_tag(self, monkeypatch):
        from fastmcp.client import Client

        from autoskillit.core import GATED_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaf")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        _apply_session_type_visibility()

        async with Client(mcp) as client:
            tools = await client.list_tools()
        tool_names = {t.name for t in tools}
        assert "test_check" in tool_names, "test_check should be visible for leaf+headless"
        for name in GATED_TOOLS:
            assert name not in tool_names, f"{name} (kitchen) should be hidden for leaf+headless"

    @pytest.mark.anyio
    async def test_food_truck_with_tool_tags_sees_kitchen_core_plus_declared(self, monkeypatch):
        """ORCHESTRATOR+HEADLESS with L2_TOOL_TAGS sees kitchen-core + github only."""
        from fastmcp.client import Client

        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_L2_TOOL_TAGS", "github")
        _apply_session_type_visibility()

        async with Client(mcp) as client:
            tools = await client.list_tools()
        tool_names = {t.name for t in tools}

        assert "run_cmd" in tool_names
        assert "run_skill" in tool_names
        assert "merge_worktree" in tool_names
        assert "fetch_github_issue" in tool_names
        assert "wait_for_ci" not in tool_names
        assert "clone_repo" not in tool_names

    @pytest.mark.anyio
    async def test_food_truck_with_multiple_packs(self, monkeypatch):
        """ORCHESTRATOR+HEADLESS with L2_TOOL_TAGS=github,ci sees both packs."""
        from fastmcp.client import Client

        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_L2_TOOL_TAGS", "github,ci")
        _apply_session_type_visibility()

        async with Client(mcp) as client:
            tools = await client.list_tools()
        tool_names = {t.name for t in tools}

        assert "fetch_github_issue" in tool_names
        assert "wait_for_ci" in tool_names
        assert "clone_repo" not in tool_names

    @pytest.mark.anyio
    async def test_food_truck_without_tool_tags_sees_full_kitchen(self, monkeypatch):
        """ORCHESTRATOR+HEADLESS without L2_TOOL_TAGS falls back to full kitchen."""
        from fastmcp.client import Client

        from autoskillit.core import GATED_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.delenv("AUTOSKILLIT_L2_TOOL_TAGS", raising=False)
        _apply_session_type_visibility()

        async with Client(mcp) as client:
            tools = await client.list_tools()
        tool_names = {t.name for t in tools}

        for name in GATED_TOOLS:
            assert name in tool_names

    @pytest.mark.anyio
    async def test_cook_interactive_unaffected_by_tool_tags(self, monkeypatch):
        """Interactive ORCHESTRATOR (cook) ignores L2_TOOL_TAGS."""
        from fastmcp.client import Client

        from autoskillit.core import GATED_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
        monkeypatch.setenv("AUTOSKILLIT_L2_TOOL_TAGS", "github")
        _apply_session_type_visibility()

        async with Client(mcp) as client:
            tools = await client.list_tools()
        tool_names = {t.name for t in tools}

        for name in GATED_TOOLS:
            assert name not in tool_names

    @pytest.mark.anyio
    async def test_leaf_interactive_no_pre_reveal(self, monkeypatch):
        from fastmcp.client import Client

        from autoskillit.core import GATED_TOOLS, HEADLESS_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaf")
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
        _apply_session_type_visibility()

        async with Client(mcp) as client:
            tools = await client.list_tools()
        tool_names = {t.name for t in tools}
        for name in GATED_TOOLS:
            assert name not in tool_names, f"{name} should be hidden for leaf+interactive"
        for name in HEADLESS_TOOLS:
            assert name not in tool_names, f"{name} should be hidden for leaf+interactive"

    @pytest.mark.anyio
    async def test_transitional_bridge_enables_headless(self, monkeypatch):
        import warnings

        from fastmcp.client import Client

        from autoskillit.core import GATED_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            _apply_session_type_visibility()

        async with Client(mcp) as client:
            tools = await client.list_tools()
        tool_names = {t.name for t in tools}
        assert "test_check" in tool_names, "test_check should be visible for bridge HEADLESS=1"
        for name in GATED_TOOLS:
            assert name not in tool_names, f"{name} (kitchen) should be hidden for bridge"

    @pytest.mark.anyio
    async def test_franchise_tag_reset_by_conftest(self, monkeypatch):
        from fastmcp.client import Client

        from autoskillit.server import mcp

        # The conftest _reset_mcp_tags fixture has already disabled the franchise tag.
        # Verify: no franchise-enabled state leaked from a previous test.
        async with Client(mcp) as client:
            tools = await client.list_tools()
        tool_names = {t.name for t in tools}
        # No kitchen tools should be visible — franchise tag was reset
        from autoskillit.core import GATED_TOOLS

        for name in GATED_TOOLS:
            assert name not in tool_names, f"{name} should be hidden after conftest reset"


class TestFeatureGateVisibility:
    """Feature gate override layer in _apply_session_type_visibility."""

    @pytest.fixture(autouse=True)
    def _reset_mcp_visibility(self):
        """Reset gated tag visibility on the shared mcp singleton before each test."""
        from autoskillit.server import mcp

        mcp.disable(tags={"franchise", "kitchen", "headless"})
        yield
        mcp.disable(tags={"franchise", "kitchen", "headless"})

    @pytest.mark.anyio
    async def test_franchise_tools_hidden_when_feature_disabled(self, monkeypatch):
        """SESSION_TYPE=franchise + AUTOSKILLIT_FEATURES__FRANCHISE=false → no franchise tools."""
        from fastmcp.client import Client

        from autoskillit.core import FRANCHISE_TOOLS
        from autoskillit.server import mcp
        from autoskillit.server._session_type import (
            _apply_session_type_visibility,
            _franchise_gate,
        )

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "franchise")
        monkeypatch.setenv("AUTOSKILLIT_FEATURES__FRANCHISE", "false")
        _apply_session_type_visibility(feature_gates=[_franchise_gate])

        async with Client(mcp) as client:
            tools = await client.list_tools()
        tool_names = {t.name for t in tools}
        for name in FRANCHISE_TOOLS:
            assert name not in tool_names, (
                f"{name} should be hidden when franchise feature is disabled"
            )

    @pytest.mark.anyio
    async def test_franchise_tools_visible_when_feature_enabled(self, monkeypatch):
        """SESSION_TYPE=franchise with no override → franchise tools visible (default_enabled)."""
        from fastmcp.client import Client

        from autoskillit.core import FRANCHISE_TOOLS
        from autoskillit.server import mcp
        from autoskillit.server._session_type import (
            _apply_session_type_visibility,
            _franchise_gate,
        )

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "franchise")
        monkeypatch.delenv("AUTOSKILLIT_FEATURES__FRANCHISE", raising=False)
        _apply_session_type_visibility(feature_gates=[_franchise_gate])

        async with Client(mcp) as client:
            tools = await client.list_tools()
        tool_names = {t.name for t in tools}
        for name in FRANCHISE_TOOLS:
            assert name in tool_names, (
                f"{name} should be visible for franchise session when feature is enabled"
            )

    @pytest.mark.anyio
    async def test_session_type_franchise_respects_gate(self, monkeypatch):
        """franchise session + feature disabled → no franchise tools, non-franchise hidden too."""
        from fastmcp.client import Client

        from autoskillit.core import FRANCHISE_TOOLS, GATED_TOOLS
        from autoskillit.server import mcp
        from autoskillit.server._session_type import (
            _apply_session_type_visibility,
            _franchise_gate,
        )

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "franchise")
        monkeypatch.setenv("AUTOSKILLIT_FEATURES__FRANCHISE", "false")
        _apply_session_type_visibility(feature_gates=[_franchise_gate])

        async with Client(mcp) as client:
            tools = await client.list_tools()
        tool_names = {t.name for t in tools}
        # No franchise tool visible — gate neutralized the pre-reveal
        for name in FRANCHISE_TOOLS:
            assert name not in tool_names, f"{name} should be hidden after franchise gate override"
        # Non-franchise kitchen tools also absent (only franchise was pre-revealed)
        for name in GATED_TOOLS - FRANCHISE_TOOLS:
            assert name not in tool_names, (
                f"{name} (non-franchise kitchen) should be hidden for franchise session"
            )

    def test_feature_gate_ordering(self, monkeypatch):
        """Feature gates execute AFTER session-type dispatch (structural ordering test)."""
        from unittest.mock import patch

        from autoskillit.core import SessionType
        from autoskillit.server._session_type import _apply_session_type_visibility

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "franchise")

        call_order: list[str] = []

        # Monkeypatch mcp.enable to record dispatch happening
        import autoskillit.server._session_type as st_mod

        def recording_gate(mcp_instance, session: SessionType) -> None:
            call_order.append("gate")

        with patch.object(st_mod, "_resolve_session_type", return_value=SessionType.FRANCHISE):
            import autoskillit.server as server_mod

            real_enable = server_mod.mcp.enable

            def patched_enable(*, tags):
                call_order.append("dispatch_enable")
                return real_enable(tags=tags)

            with patch.object(server_mod.mcp, "enable", patched_enable):
                _apply_session_type_visibility(feature_gates=[recording_gate])

        # Gate must be called, and after dispatch_enable
        assert "gate" in call_order, "Gate was never called"
        dispatch_idx = next((i for i, v in enumerate(call_order) if v == "dispatch_enable"), None)
        gate_idx = next((i for i, v in enumerate(call_order) if v == "gate"), None)
        assert dispatch_idx is not None, "Dispatch enable was never called"
        assert gate_idx > dispatch_idx, (
            f"Gate (idx={gate_idx}) must run AFTER dispatch (idx={dispatch_idx})"
        )

    @pytest.mark.anyio
    async def test_apply_session_type_visibility_accepts_no_gates(self, monkeypatch):
        """Backward compat: _apply_session_type_visibility() with no args behaves identically."""
        from fastmcp.client import Client

        from autoskillit.core import FRANCHISE_TOOLS
        from autoskillit.server import mcp
        from autoskillit.server._session_type import _apply_session_type_visibility

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "franchise")
        monkeypatch.delenv("AUTOSKILLIT_FEATURES__FRANCHISE", raising=False)
        # Call with no feature_gates argument (backward-compatible call)
        _apply_session_type_visibility()

        async with Client(mcp) as client:
            tools = await client.list_tools()
        tool_names = {t.name for t in tools}
        # Phase 1 still works: franchise tools visible
        for name in FRANCHISE_TOOLS:
            assert name in tool_names, (
                f"{name} should be visible with no-gate backward-compatible call"
            )
