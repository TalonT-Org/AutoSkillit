"""Tests for autoskillit server initialization and metadata."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import structlog.testing

from autoskillit.config import (
    AutomationConfig,
    SafetyConfig,
)
from autoskillit.pipeline.gate import DefaultGateState
from autoskillit.server.helpers import _require_enabled
from autoskillit.server.tools_kitchen import _close_kitchen_handler, _open_kitchen_handler
from autoskillit.server.tools_status import (
    get_quota_events,
    get_token_summary,
)


class TestPluginMetadataExists:
    """T1: Plugin metadata files are shipped in the package."""

    def test_plugin_json_exists(self):
        """Package contains .claude-plugin/plugin.json with correct fields."""
        import autoskillit

        pkg = Path(autoskillit.__file__).parent
        manifest = pkg / ".claude-plugin" / "plugin.json"
        assert manifest.is_file()
        data = json.loads(manifest.read_text())
        assert data["name"] == "autoskillit"
        assert data["version"] == autoskillit.__version__

    def test_mcp_json_exists(self):
        """Package contains .mcp.json with autoskillit server entry."""
        import autoskillit

        pkg = Path(autoskillit.__file__).parent
        mcp_cfg = pkg / ".mcp.json"
        assert mcp_cfg.is_file()
        data = json.loads(mcp_cfg.read_text())
        assert "autoskillit" in data["mcpServers"]
        assert data["mcpServers"]["autoskillit"]["command"] == "autoskillit"


class TestNoSkillsDirectoryProvider:
    """T3: SkillsDirectoryProvider is not used in the new plugin architecture."""

    def test_no_skills_directory_provider(self):
        """server.py must not reference SkillsDirectoryProvider."""
        import autoskillit.server as server_module

        source = Path(server_module.__file__).read_text()
        assert "SkillsDirectoryProvider" not in source


class TestPluginDirConstant:
    """T6: tool_ctx.plugin_dir defaults to the package root directory."""

    def test_plugin_dir_assignment_is_visible_via_get_ctx(self, tool_ctx):
        """By default tool_ctx.plugin_dir is set to tmp_path by the fixture.

        The real package dir is what the server uses at runtime (set by cli.py serve()).
        This test verifies that the fixture wires plugin_dir through _ctx correctly.
        """
        import autoskillit

        # The real package dir is what the server sets at startup.
        # We verify the attribute path works (tool_ctx.plugin_dir is accessible).
        real_pkg_dir = str(Path(autoskillit.__file__).parent)
        # tool_ctx uses tmp_path; set it to verify end-to-end wiring
        tool_ctx.plugin_dir = real_pkg_dir
        from autoskillit.server import _get_ctx

        assert _get_ctx().plugin_dir == real_pkg_dir


class TestVersionInfo:
    """version_info() returns package and plugin.json versions."""

    def test_version_info_returns_package_and_plugin_versions(self):
        from autoskillit import __version__
        from autoskillit.server import version_info

        info = version_info()
        assert isinstance(info["package_version"], str)
        assert isinstance(info["plugin_json_version"], str)
        assert info["package_version"] == __version__
        assert info["match"] is True

    def test_version_info_detects_mismatch(self, tmp_path, tool_ctx):
        from autoskillit.server import version_info

        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"name": "autoskillit", "version": "0.0.0"})
        )
        tool_ctx.plugin_dir = str(tmp_path)
        info = version_info()
        assert info["match"] is False
        assert info["package_version"] != info["plugin_json_version"]
        assert info["plugin_json_version"] == "0.0.0"

    def test_version_info_handles_missing_plugin_json(self, tmp_path, tool_ctx):
        from autoskillit.server import version_info

        tool_ctx.plugin_dir = str(tmp_path)
        info = version_info()
        assert info["plugin_json_version"] is None
        assert info["match"] is False

    def test_version_info_is_public(self):
        """version_info must be a public function — no underscore prefix."""
        from autoskillit import server

        assert hasattr(server, "version_info"), "server.version_info must exist"
        assert not hasattr(server, "_version_info"), "server._version_info must be removed"
        result = server.version_info()
        assert set(result.keys()) >= {"package_version", "plugin_json_version", "match"}


class TestToolRegistration:
    """All 40 tools are registered on the MCP server."""

    @pytest.mark.anyio
    async def test_all_tools_exist(self):
        from fastmcp.client import Client

        from autoskillit.server import mcp

        try:
            mcp.enable(tags={"kitchen"})
            async with Client(mcp) as client:
                all_tools = await client.list_tools()
        finally:
            mcp.disable(tags={"kitchen"})
        tool_names = {t.name for t in all_tools}

        expected = {
            "run_cmd",
            "run_python",
            "run_skill",
            "test_check",
            "reset_test_dir",
            "classify_fix",
            "reset_workspace",
            "merge_worktree",
            "read_db",
            "list_recipes",
            "load_recipe",
            "migrate_recipe",
            "kitchen_status",
            "validate_recipe",
            "get_pipeline_report",
            "get_token_summary",
            "get_timing_summary",
            "get_quota_events",
            "clone_repo",
            "remove_clone",
            "push_to_remote",
            "fetch_github_issue",
            "get_issue_title",
            "report_bug",
            "prepare_issue",
            "enrich_issues",
            "claim_issue",
            "release_issue",
            "wait_for_ci",
            "wait_for_merge_queue",
            "toggle_auto_merge",
            "get_ci_status",
            "open_kitchen",
            "close_kitchen",
            "create_unique_branch",
            "check_pr_mergeable",
            "write_telemetry_files",
            "get_pr_reviews",
            "bulk_close_issues",
            "set_commit_status",
        }
        assert expected == tool_names

    @pytest.mark.anyio
    async def test_ungated_tools_lack_kitchen_tag(self):
        """Ungated (free-range) tools are visible without kitchen and carry no 'kitchen' tag."""
        from fastmcp.client import Client

        from autoskillit.pipeline.gate import UNGATED_TOOLS
        from autoskillit.server import mcp

        async with Client(mcp) as client:
            visible_names = {t.name for t in await client.list_tools()}
        for name in UNGATED_TOOLS:
            assert name in visible_names, f"{name} should be visible without kitchen"

        # Verify no ungated tool carries the kitchen tag (internal registry check)
        all_tools = {t.name: t for t in await mcp.list_tools()}
        for name in UNGATED_TOOLS:
            tool = all_tools.get(name)
            if tool is not None:
                assert "kitchen" not in tool.tags, (
                    f"Ungated tool '{name}' must not carry the 'kitchen' tag"
                )

        # test_check now has the kitchen tag (it's headless-tier, not free-range)
        try:
            mcp.enable(tags={"kitchen"})
            all_tools_with_kitchen = {t.name: t for t in await mcp.list_tools()}
        finally:
            mcp.disable(tags={"kitchen"})
        tc = all_tools_with_kitchen.get("test_check")
        assert tc is not None, "test_check must be registered"
        assert "kitchen" in tc.tags, "test_check must carry the 'kitchen' tag"

    @pytest.mark.anyio
    async def test_kitchen_tools_have_autoskillit_and_kitchen_tags(self):
        """Every tool in GATED_TOOLS carries both 'autoskillit' and 'kitchen' tags."""
        from autoskillit.pipeline.gate import GATED_TOOLS
        from autoskillit.server import mcp

        try:
            mcp.enable(tags={"kitchen"})
            all_tools = {t.name: t for t in await mcp.list_tools()}
        finally:
            mcp.disable(tags={"kitchen"})
        for name in GATED_TOOLS:
            tool = all_tools.get(name)
            assert tool is not None, f"Gated tool '{name}' not registered on server"
            assert "autoskillit" in tool.tags, f"Gated tool '{name}' missing 'autoskillit' tag"
            assert "kitchen" in tool.tags, f"Gated tool '{name}' missing 'kitchen' tag"
            assert "automation" not in tool.tags, (
                f"Gated tool '{name}' still has deprecated 'automation' tag"
            )

    @pytest.mark.anyio
    async def test_all_tools_tagged_autoskillit(self):
        """Every registered tool carries the 'autoskillit' tag."""
        from autoskillit.server import mcp

        try:
            mcp.enable(tags={"kitchen", "headless"})
            all_tools = await mcp.list_tools()
        finally:
            mcp.disable(tags={"kitchen"})
            mcp.disable(tags={"headless"})
        for tool in all_tools:
            assert "autoskillit" in tool.tags, f"Tool '{tool.name}' missing 'autoskillit' tag"
            assert "automation" not in tool.tags, f"Tool '{tool.name}' still has 'automation' tag"

    def test_ungated_tools_docstrings_state_notification_free(self):
        """P5-1: Free-range tool docstrings state they send no MCP notifications."""

        for tool_fn in [get_token_summary, get_quota_events]:
            doc = tool_fn.__doc__ or ""
            assert "no MCP" in doc or "no progress notification" in doc.lower(), (
                f"{tool_fn.__name__} must document notification-free behavior"
            )

    @pytest.mark.anyio
    async def test_test_check_has_headless_tag(self):
        from autoskillit.server import mcp

        try:
            mcp.enable(tags={"kitchen"})
            all_tools = {t.name: t for t in await mcp.list_tools()}
        finally:
            mcp.disable(tags={"kitchen"})
        tc = all_tools.get("test_check")
        assert tc is not None
        assert "headless" in tc.tags
        assert "kitchen" in tc.tags
        assert "autoskillit" in tc.tags

    @pytest.mark.anyio
    async def test_headless_enable_reveals_only_headless_tagged_tools(self):
        from fastmcp.client import Client

        from autoskillit.pipeline.gate import GATED_TOOLS
        from autoskillit.server import mcp

        try:
            mcp.enable(tags={"headless"})
            async with Client(mcp) as client:
                visible = {t.name for t in await client.list_tools()}
        finally:
            mcp.disable(tags={"headless"})
        assert "test_check" in visible
        # Kitchen-only tools (no headless tag) must NOT be revealed
        kitchen_only = GATED_TOOLS - {"test_check"}
        for name in kitchen_only:
            assert name not in visible, f"{name} should not be revealed by headless-only enable"


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

    TOOL_FORBIDDEN_TERMS = [
        "enable_tools",
        "disable_tools",
        "autoskillit_status",
        "executor",
    ]

    @pytest.mark.anyio
    async def test_tool_descriptions_contain_no_legacy_terms(self):
        """Kitchen tool descriptions must not use any pre-rename vocabulary."""
        tools = await self._get_kitchen_tools()
        for name, tool in tools.items():
            desc = (tool.description or "").lower()
            for term in self.TOOL_FORBIDDEN_TERMS:
                assert term not in desc, (
                    f"Tool '{name}' description contains legacy term '{term}': {desc!r}"
                )

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
        """Extract text from open_kitchen result (now returns str directly)."""
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


class TestServerLazyInit:
    """Tests for the _ctx / _initialize() / _get_ctx() / _get_config() pattern."""

    def test_server_import_does_not_call_load_config(self, monkeypatch):
        """Importing server.py must not trigger load_config() as a side effect."""
        import sys

        import autoskillit

        # Restore both the package attribute and sys.modules entry after the test so
        # later tests in the same xdist worker see the original module object.
        monkeypatch.setattr(autoskillit, "server", autoskillit.server)
        monkeypatch.delitem(sys.modules, "autoskillit.server", raising=False)

        with patch("autoskillit.config.load_config") as mock_load:
            import autoskillit.server  # noqa: F401
        assert not mock_load.called

    def test_get_ctx_raises_before_initialize(self, monkeypatch):
        """_get_ctx() raises RuntimeError when _ctx is None."""
        from autoskillit.server import _state

        monkeypatch.setattr(_state, "_ctx", None)
        with pytest.raises(RuntimeError, match="serve\\(\\) must be called"):
            _state._get_ctx()

    def test_get_config_raises_before_initialize(self, monkeypatch):
        """_get_config() raises RuntimeError when _ctx is None."""
        from autoskillit.server import _state

        monkeypatch.setattr(_state, "_ctx", None)
        with pytest.raises(RuntimeError, match="serve\\(\\) must be called"):
            _state._get_config()


class TestInitializeClearMarker:
    """_initialize respects telemetry_cleared_at fence for drift prevention."""

    def test_initialize_uses_clear_marker_as_since_bound(self, tool_ctx, tmp_path, monkeypatch):
        from datetime import UTC, datetime, timedelta

        from autoskillit.execution.session_log import (
            flush_session_log,
        )
        from autoskillit.server import _state

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Write a session that completed 5 hours ago (within 24h window)
        five_hours_ago = datetime.now(UTC) - timedelta(hours=5)
        flush_session_log(
            log_dir=str(log_dir),
            cwd="/tmp",
            session_id="old-session",
            pid=999,
            skill_command="/autoskillit:foo",
            success=True,
            subtype="completed",
            exit_code=0,
            start_ts=five_hours_ago.isoformat(),
            proc_snapshots=None,
            step_name="old-step",
            token_usage={
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            timing_seconds=10.0,
        )

        # Write a clear marker 3 hours ago (after the session completed)
        three_hours_ago = datetime.now(UTC) - timedelta(hours=3)
        (log_dir / ".telemetry_cleared_at").write_text(three_hours_ago.isoformat())

        monkeypatch.setattr(tool_ctx.config.linux_tracing, "log_dir", str(log_dir))
        _state._initialize(tool_ctx)

        # The old-session happened before the clear marker → should NOT be replayed
        report = tool_ctx.token_log.get_report()
        assert all(s["step_name"] != "old-step" for s in report)

    def test_initialize_loads_sessions_after_marker(self, tool_ctx, tmp_path, monkeypatch):
        from datetime import UTC, datetime, timedelta

        from autoskillit.execution.session_log import flush_session_log
        from autoskillit.server import _state

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Write clear marker 5 hours ago
        five_hours_ago = datetime.now(UTC) - timedelta(hours=5)
        (log_dir / ".telemetry_cleared_at").write_text(five_hours_ago.isoformat())

        # Write a session 3 hours ago (after the marker)
        three_hours_ago = datetime.now(UTC) - timedelta(hours=3)
        flush_session_log(
            log_dir=str(log_dir),
            cwd="/tmp",
            session_id="new-session",
            pid=1001,
            skill_command="/autoskillit:bar",
            success=True,
            subtype="completed",
            exit_code=0,
            start_ts=three_hours_ago.isoformat(),
            proc_snapshots=None,
            step_name="new-step",
            token_usage={
                "input_tokens": 800,
                "output_tokens": 200,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            timing_seconds=8.0,
        )

        monkeypatch.setattr(tool_ctx.config.linux_tracing, "log_dir", str(log_dir))
        _state._initialize(tool_ctx)

        report = tool_ctx.token_log.get_report()
        step_names = [s["step_name"] for s in report]
        assert "new-step" in step_names

    def test_initialize_includes_session_at_marker_boundary(self, tool_ctx, tmp_path, monkeypatch):
        """Session with ts == marker ts is included (fence uses strict less-than)."""
        from datetime import UTC, datetime, timedelta

        from autoskillit.execution.session_log import flush_session_log
        from autoskillit.server import _state

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Write clear marker 2 hours ago
        two_hours_ago = datetime.now(UTC) - timedelta(hours=2)
        (log_dir / ".telemetry_cleared_at").write_text(two_hours_ago.isoformat())

        # Write a session with ts == marker ts (boundary: should be included)
        flush_session_log(
            log_dir=str(log_dir),
            cwd="/tmp",
            session_id="boundary-session",
            pid=1002,
            skill_command="/autoskillit:baz",
            success=True,
            subtype="completed",
            exit_code=0,
            start_ts=two_hours_ago.isoformat(),
            proc_snapshots=None,
            step_name="boundary-step",
            token_usage={
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            timing_seconds=1.0,
        )

        monkeypatch.setattr(tool_ctx.config.linux_tracing, "log_dir", str(log_dir))
        _state._initialize(tool_ctx)

        report = tool_ctx.token_log.get_report()
        step_names = [s["step_name"] for s in report]
        assert "boundary-step" in step_names


class TestConfigDrivenBehavior:
    """S1-S10: Verify tools use config instead of hardcoded values."""

    @pytest.mark.anyio
    async def test_test_check_uses_config_command(self, tool_ctx):
        """S1: test_check runs config.test_check.command."""
        from autoskillit.config import TestCheckConfig
        from autoskillit.execution import DefaultTestRunner
        from autoskillit.server.tools_workspace import test_check
        from tests.conftest import _make_result

        tool_ctx.config = AutomationConfig(
            test_check=TestCheckConfig(command=["pytest", "-x"], timeout=300)
        )
        # Re-create tester with updated config so it reads the new command
        tool_ctx.tester = DefaultTestRunner(config=tool_ctx.config, runner=tool_ctx.runner)

        tool_ctx.runner.push(_make_result(0, "= 100 passed =\n", ""))
        await test_check(worktree_path="/tmp/wt")

        assert tool_ctx.runner.call_args_list[0][0] == ["pytest", "-x"]
        assert tool_ctx.runner.call_args_list[0][2] == 300.0

    @pytest.mark.anyio
    async def test_classify_fix_uses_config_prefixes(self, tool_ctx, tmp_path):
        """S2: classify_fix uses config.classify_fix.path_prefixes."""
        from autoskillit.config import ClassifyFixConfig
        from autoskillit.core.types import RestartScope
        from autoskillit.server.tools_git import classify_fix
        from tests.conftest import _make_result

        tool_ctx.config = AutomationConfig(
            classify_fix=ClassifyFixConfig(path_prefixes=["src/custom/"])
        )

        changed = "src/custom/handler.py\nsrc/other/util.py\n"
        tool_ctx.runner.push(_make_result(0, "", ""))  # git fetch succeeds
        tool_ctx.runner.push(_make_result(0, changed, ""))
        result = json.loads(await classify_fix(worktree_path=str(tmp_path), base_branch="main"))

        assert result["restart_scope"] == RestartScope.FULL_RESTART
        assert "src/custom/handler.py" in result["critical_files"]

    @pytest.mark.anyio
    async def test_classify_fix_empty_prefixes_always_partial(self, tool_ctx, tmp_path):
        """S3: Empty prefix list -> always returns partial_restart."""
        from autoskillit.config import ClassifyFixConfig
        from autoskillit.core.types import RestartScope
        from autoskillit.server.tools_git import classify_fix
        from tests.conftest import _make_result

        tool_ctx.config = AutomationConfig(classify_fix=ClassifyFixConfig(path_prefixes=[]))

        changed = "src/core/handler.py\n"
        tool_ctx.runner.push(_make_result(0, "", ""))  # git fetch succeeds
        tool_ctx.runner.push(_make_result(0, changed, ""))
        result = json.loads(await classify_fix(worktree_path=str(tmp_path), base_branch="main"))

        assert result["restart_scope"] == RestartScope.PARTIAL_RESTART

    @pytest.mark.anyio
    async def test_reset_workspace_uses_config_command(self, tool_ctx, tmp_path):
        """S4: reset_workspace runs config.reset_workspace.command."""
        from autoskillit.config import ResetWorkspaceConfig
        from autoskillit.server.tools_workspace import reset_workspace
        from tests.conftest import _make_result

        tool_ctx.config = AutomationConfig(
            reset_workspace=ResetWorkspaceConfig(command=["make", "reset"])
        )

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / ".autoskillit-workspace").write_text("# marker\n")
        tool_ctx.runner.push(_make_result(0, "", ""))

        await reset_workspace(test_dir=str(workspace))
        assert tool_ctx.runner.call_args_list[0][0] == ["make", "reset"]

    @pytest.mark.anyio
    async def test_reset_workspace_not_configured_returns_error(self, tool_ctx, tmp_path):
        """S5: command=None -> returns not-configured error."""
        from autoskillit.config import ResetWorkspaceConfig
        from autoskillit.server.tools_workspace import reset_workspace

        tool_ctx.config = AutomationConfig(reset_workspace=ResetWorkspaceConfig(command=None))

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / ".autoskillit-workspace").write_text("# marker\n")
        result = json.loads(await reset_workspace(test_dir=str(workspace)))

        assert result["error"] == "reset_workspace not configured for this project"

    @pytest.mark.anyio
    async def test_reset_workspace_uses_config_preserve_dirs(self, tool_ctx, tmp_path):
        """S6: Preserves config.reset_workspace.preserve_dirs."""
        from autoskillit.config import ResetWorkspaceConfig
        from autoskillit.server.tools_workspace import reset_workspace
        from tests.conftest import _make_result

        tool_ctx.config = AutomationConfig(
            reset_workspace=ResetWorkspaceConfig(
                command=["true"],
                preserve_dirs={"keep_me"},
            )
        )

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / ".autoskillit-workspace").write_text("# marker\n")
        (workspace / "keep_me").mkdir()
        (workspace / "delete_me").touch()
        tool_ctx.runner.push(_make_result(0, "", ""))

        result = json.loads(await reset_workspace(test_dir=str(workspace)))

        assert "keep_me" in result["skipped"]
        assert "delete_me" in result["deleted"]
        assert (workspace / "keep_me").exists()
        assert not (workspace / "delete_me").exists()

    def test_dry_walkthrough_uses_config_marker(self, tool_ctx, tmp_path):
        """S7: Gate checks config.implement_gate.marker."""
        from autoskillit.config import ImplementGateConfig
        from autoskillit.server.helpers import _check_dry_walkthrough

        tool_ctx.config = AutomationConfig(
            implement_gate=ImplementGateConfig(marker="CUSTOM MARKER")
        )

        plan = tmp_path / "plan.md"
        plan.write_text("CUSTOM MARKER\n# Plan content")
        result = _check_dry_walkthrough(f"/autoskillit:implement-worktree {plan}", str(tmp_path))
        assert result is None  # passes with custom marker

        plan.write_text("Dry-walkthrough verified = TRUE\n# Plan content")
        result = _check_dry_walkthrough(f"/autoskillit:implement-worktree {plan}", str(tmp_path))
        assert result is not None  # fails — marker doesn't match

    def test_dry_walkthrough_uses_config_skill_names(self, tool_ctx, tmp_path):
        """S8: Gate checks config.implement_gate.skill_names."""
        from autoskillit.config import ImplementGateConfig
        from autoskillit.server.helpers import _check_dry_walkthrough

        tool_ctx.config = AutomationConfig(
            implement_gate=ImplementGateConfig(skill_names={"/custom-impl"})
        )

        plan = tmp_path / "plan.md"
        plan.write_text("# No marker")

        result = _check_dry_walkthrough(f"/custom-impl {plan}", str(tmp_path))
        assert result is not None  # /custom-impl is gated

        result = _check_dry_walkthrough(f"/autoskillit:implement-worktree {plan}", str(tmp_path))
        assert result is None  # /autoskillit:implement-worktree is NOT gated (not in skill_names)

    @pytest.mark.anyio
    async def test_merge_worktree_uses_config_test_command(self, tool_ctx, tmp_path):
        """S9: Merge's test gate runs config.test_check.command."""
        from autoskillit.config import TestCheckConfig
        from autoskillit.core.types import MergeFailedStep
        from autoskillit.execution import DefaultTestRunner
        from autoskillit.server.tools_git import merge_worktree
        from tests.conftest import _make_result

        tool_ctx.config = AutomationConfig(
            test_check=TestCheckConfig(command=["make", "test"], timeout=120)
        )
        # Re-create tester with updated config so it reads the new command
        tool_ctx.tester = DefaultTestRunner(config=tool_ctx.config, runner=tool_ctx.runner)

        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))  # rev-parse
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        tool_ctx.runner.push(_make_result(0, "", ""))  # git ls-files (pre-dirty-tree check)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        tool_ctx.runner.push(_make_result(1, "FAIL", ""))  # test gate fails
        result = json.loads(await merge_worktree(str(wt), "dev"))
        assert result["failed_step"] == MergeFailedStep.TEST_GATE

        # Verify the test command was ["make", "test"] (5th call, after ls-files + porcelain)
        test_call = tool_ctx.runner.call_args_list[4]
        assert test_call[0] == ["make", "test"]

    @pytest.mark.anyio
    async def test_require_enabled_still_gates_execution(self, tool_ctx):
        """S10: _require_enabled() defense-in-depth still works with config."""
        from autoskillit.server.tools_execution import run_cmd

        tool_ctx.gate = DefaultGateState(enabled=False)
        result = json.loads(await run_cmd(cmd="echo hi", cwd="/tmp"))
        assert result["success"] is False
        assert result["is_error"] is True
        assert "not enabled" in result["result"].lower()


class TestSafetyConfigWiring:
    """Safety config fields are read at the point of enforcement."""

    @pytest.mark.anyio
    async def test_reset_test_dir_allows_with_marker(self, tool_ctx, tmp_path):
        """2a: Directory with marker passes the reset guard."""
        from autoskillit.server.tools_workspace import reset_test_dir

        target = tmp_path / "my_project"
        target.mkdir()
        (target / ".autoskillit-workspace").write_text("# marker\n")
        (target / "file.txt").touch()

        result = json.loads(await reset_test_dir(test_dir=str(target), force=False))
        assert result["success"] is True

    @pytest.mark.anyio
    async def test_reset_test_dir_enforces_marker_when_missing(self, tool_ctx, tmp_path):
        """2b: Missing marker blocks reset_test_dir."""
        from autoskillit.server.tools_workspace import reset_test_dir

        target = tmp_path / "unmarked"
        target.mkdir()
        result = json.loads(await reset_test_dir(test_dir=str(target)))
        assert "error" in result
        assert "marker" in result["error"].lower()

    @pytest.mark.anyio
    async def test_reset_workspace_enforces_marker(self, tool_ctx, tmp_path):
        """2c: reset_workspace requires marker, then checks command config."""
        from autoskillit.config import ResetWorkspaceConfig
        from autoskillit.server.tools_workspace import reset_workspace

        tool_ctx.config = AutomationConfig(reset_workspace=ResetWorkspaceConfig(command=None))

        target = tmp_path / "my_project"
        target.mkdir()
        (target / ".autoskillit-workspace").write_text("# marker\n")

        result = json.loads(await reset_workspace(test_dir=str(target)))
        # Should pass marker guard but fail on "not configured"
        assert result["error"] == "reset_workspace not configured for this project"

    @pytest.mark.anyio
    async def test_merge_worktree_skips_test_gate_when_disabled(self, tool_ctx, tmp_path):
        """2d: test_gate_on_merge=False skips test execution."""
        from autoskillit.server.tools_git import merge_worktree
        from tests.conftest import _make_result

        tool_ctx.config = AutomationConfig(safety=SafetyConfig(test_gate_on_merge=False))

        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /repo/.git/worktrees/wt")

        tool_ctx.runner.push(_make_result(0, "/repo/.git/worktrees/wt\n", ""))  # rev-parse
        tool_ctx.runner.push(_make_result(0, "impl-branch\n", ""))  # branch
        tool_ctx.runner.push(_make_result(0, "", ""))  # git ls-files (pre-dirty-tree check)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git status --porcelain (clean)
        # NO test-check call — skipped
        tool_ctx.runner.push(_make_result(0, "", ""))  # git fetch
        tool_ctx.runner.push(_make_result(0, "abc123\n", ""))  # rev-parse --verify (step 5.5)
        tool_ctx.runner.push(
            _make_result(0, "", "")
        )  # git log --merges (step 5.6 — no merge commits)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git rebase
        tool_ctx.runner.push(
            _make_result(
                0,
                "worktree /repo\nHEAD abc\nbranch refs/heads/main\n\n",
                "",
            )
        )  # worktree list
        tool_ctx.runner.push(_make_result(0, "", ""))  # git merge
        tool_ctx.runner.push(_make_result(0, "", ""))  # worktree remove
        tool_ctx.runner.push(_make_result(0, "", ""))  # branch -D
        result = json.loads(await merge_worktree(str(wt), "dev"))
        assert result["merge_succeeded"] is True

        # Verify no test command was called — the 5th call should be git fetch, not test
        fifth_call_cmd = tool_ctx.runner.call_args_list[4][0]
        assert fifth_call_cmd == ["git", "fetch", "origin"]

    @pytest.mark.anyio
    async def test_run_skill_2e_skips_dry_walkthrough_when_disabled(self, tool_ctx, tmp_path):
        """2e: require_dry_walkthrough=False bypasses dry-walkthrough gate (using run_skill)."""
        from autoskillit.server.tools_execution import run_skill
        from tests.conftest import _make_result

        tool_ctx.config = AutomationConfig(safety=SafetyConfig(require_dry_walkthrough=False))

        plan = tmp_path / "plan.md"
        plan.write_text("# No marker plan")

        tool_ctx.runner.push(_make_result(0, '{"result": "done"}', ""))
        result = json.loads(
            await run_skill(f"/autoskillit:implement-worktree {plan}", str(tmp_path))
        )
        assert result["subtype"] != "gate_error"
        assert result["exit_code"] == 0

    @pytest.mark.anyio
    async def test_run_skill_enforces_dry_walkthrough_when_enabled(self, tool_ctx, tmp_path):
        """2f: run_skill enforces dry-walkthrough gate when enabled (default)."""
        from autoskillit.server.tools_execution import run_skill

        plan = tmp_path / "plan.md"
        plan.write_text("# No marker plan")

        result = json.loads(
            await run_skill(f"/autoskillit:implement-worktree {plan}", str(tmp_path))
        )
        assert result["success"] is False
        assert result["is_error"] is True
        assert "dry-walked" in result["result"].lower()

    @pytest.mark.anyio
    async def test_run_skill_skips_dry_walkthrough_when_disabled(self, tool_ctx, tmp_path):
        """2g: run_skill skips dry-walkthrough gate when disabled."""
        from autoskillit.server.tools_execution import run_skill
        from tests.conftest import _make_result

        tool_ctx.config = AutomationConfig(safety=SafetyConfig(require_dry_walkthrough=False))

        plan = tmp_path / "plan.md"
        plan.write_text("# No marker plan")

        tool_ctx.runner.push(_make_result(0, '{"result": "done"}', ""))
        result = json.loads(
            await run_skill(f"/autoskillit:implement-worktree {plan}", str(tmp_path))
        )
        assert result["subtype"] != "gate_error"


class TestToolSchemas:
    """Regression guard: tool descriptions must not contain legacy terminology."""

    FORBIDDEN_TERMS = {
        "executor",
        "planner",
        "automation-mcp",
        "ai-executor",
        "enable_tools",  # old open_kitchen prompt name
        "disable_tools",  # old close_kitchen prompt name
        "autoskillit_status",  # old kitchen_status tool name
    }

    REQUIRED_CROSS_REFS: dict[str, list[str]] = {
        "list_recipes": [
            "write-recipe",
        ],
        "load_recipe": [
            "write-recipe",
        ],
        "validate_recipe": [
            "write-recipe",
        ],
    }

    @property
    def FORBIDDEN_NATIVE_TOOLS(self) -> list[str]:  # noqa: N802
        from autoskillit.server import PIPELINE_FORBIDDEN_TOOLS

        return list(PIPELINE_FORBIDDEN_TOOLS)

    PIPELINE_TOOLS_WITH_GUIDANCE: dict[str, list[str]] = {
        "run_skill": ["MCP tool", "delegate"],
    }

    async def _get_all_tools(self) -> dict:
        """Return dict of tool_name -> tool for all tools, including kitchen-gated ones."""
        from fastmcp.client import Client

        from autoskillit.server import mcp

        try:
            mcp.enable(tags={"kitchen"})
            async with Client(mcp) as client:
                tools = await client.list_tools()
        finally:
            mcp.disable(tags={"kitchen"})
        return {t.name: t for t in tools}

    @pytest.mark.anyio
    async def test_tool_descriptions_contain_no_legacy_terms(self):
        """No registered tool should reference old package terminology."""
        all_tools_dict = await self._get_all_tools()
        all_tools = list(all_tools_dict.values())
        for tool in all_tools:
            desc = (tool.description or "").lower()
            for term in self.FORBIDDEN_TERMS:
                assert term not in desc, (
                    f"Tool '{tool.name}' description contains legacy term '{term}'"
                )

    @pytest.mark.anyio
    async def test_tool_docstrings_contain_required_cross_refs(self):
        """Tool docstrings must contain required cross-references."""
        tools = await self._get_all_tools()
        for tool_name, required_terms in self.REQUIRED_CROSS_REFS.items():
            tool = tools.get(tool_name)
            assert tool is not None, f"Tool '{tool_name}' not found in server"
            desc = tool.description or ""
            for term in required_terms:
                assert term in desc, f"Tool '{tool_name}' description must reference '{term}'"

    @pytest.mark.anyio
    async def test_classify_fix_docstring_has_routing_guidance(self):
        """classify_fix must explain what to do with each return value."""
        tools = await self._get_all_tools()
        assert "classify_fix" in tools, "Tool 'classify_fix' not found in server"
        desc = tools["classify_fix"].description or ""
        # Must mention both routing outcomes
        assert "full_restart" in desc
        assert "partial_restart" in desc
        # Must mention at least one skill as routing target
        assert "investigate" in desc or "implement" in desc

    @pytest.mark.anyio
    async def test_recipe_tools_have_disambiguation(self):
        """All recipe-related tools must carry the 'NOT slash commands' disclaimer."""
        tools = await self._get_all_tools()
        recipe_tools = ["list_recipes", "load_recipe", "validate_recipe"]
        for tool_name in recipe_tools:
            tool = tools.get(tool_name)
            assert tool is not None, f"Tool '{tool_name}' not found"
            desc = tool.description or ""
            assert "NOT slash commands" in desc, (
                f"Tool '{tool_name}' must contain 'NOT slash commands' disclaimer"
            )

    @pytest.mark.anyio
    async def test_load_recipe_names_all_forbidden_tools(self):
        """load_recipe must enumerate all forbidden native tools."""
        tools = await self._get_all_tools()
        desc = tools["load_recipe"].description or ""

        missing = [t for t in self.FORBIDDEN_NATIVE_TOOLS if t not in desc]
        assert not missing, (
            f"load_recipe docstring must name all forbidden tools. Missing: {missing}"
        )

    @pytest.mark.anyio
    async def test_pipeline_tools_have_orchestrator_guidance(self):
        """run_skill must reinforce MCP-only delegation."""
        tools = await self._get_all_tools()
        failures = []
        for tool_name, required_terms in self.PIPELINE_TOOLS_WITH_GUIDANCE.items():
            desc = tools[tool_name].description or ""
            for term in required_terms:
                if term.lower() not in desc.lower():
                    failures.append(f"Tool '{tool_name}' missing orchestrator term '{term}'")
        assert not failures, "Pipeline tools missing orchestrator guidance:\n" + "\n".join(
            f"  - {f}" for f in failures
        )

    def test_pipeline_forbidden_tools_constant_is_complete(self):
        """PIPELINE_FORBIDDEN_TOOLS must contain all 10 native Claude Code tools.

        "Agent" replaces the stale "Task" and "Explore" names — Agent is the
        actual tool name; Explore is a subagent_type parameter, not a tool name.."""
        from autoskillit.server import PIPELINE_FORBIDDEN_TOOLS

        expected = {
            "Read",
            "Grep",
            "Glob",
            "Edit",
            "Write",
            "Bash",
            "Agent",
            "WebFetch",
            "WebSearch",
            "NotebookEdit",
        }
        actual = set(PIPELINE_FORBIDDEN_TOOLS)
        missing = expected - actual
        assert not missing, f"PIPELINE_FORBIDDEN_TOOLS missing tools: {missing}"

    @pytest.mark.anyio
    async def test_run_skill_names_all_forbidden_tools(self):
        """run_skill docstring must name all forbidden tools."""
        from autoskillit.server import PIPELINE_FORBIDDEN_TOOLS

        tools = await self._get_all_tools()
        for tool_name in ("run_skill",):
            desc = tools[tool_name].description or ""
            missing = [t for t in PIPELINE_FORBIDDEN_TOOLS if t not in desc]
            assert not missing, (
                f"{tool_name} docstring must name all forbidden tools. Missing: {missing}"
            )

    def test_bundled_recipe_kitchen_rules_name_all_forbidden_tools(self):
        """All bundled recipe kitchen_rules blocks must name every forbidden tool."""
        from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
        from autoskillit.server import PIPELINE_FORBIDDEN_TOOLS

        wf_dir = builtin_recipes_dir()
        for path in sorted(wf_dir.glob("*.yaml")):
            wf = load_recipe(path)
            all_constraint_text = " ".join(wf.kitchen_rules)
            missing = [t for t in PIPELINE_FORBIDDEN_TOOLS if t not in all_constraint_text]
            assert not missing, f"{path.name} kitchen_rules missing forbidden tools: {missing}"


def test_server_init_has_no_shim_reexports():
    """server/__init__.py must not re-export tool symbols (shim removed)."""
    import autoskillit.server as srv

    # These symbols must NOT be in the server package namespace after shim removal.
    # They should only be accessible via their actual submodule paths.
    shim_symbols = [
        "_check_dry_walkthrough",
        "_require_enabled",
        "_run_subprocess",
        "_open_kitchen_handler",
        "_close_kitchen_handler",
        "run_cmd",
        "run_python",
        "run_skill",
        "run_skill_retry",
        "test_check",
        "reset_test_dir",
        "reset_workspace",
        "merge_worktree",
        "classify_fix",
        "clone_repo",
        "remove_clone",
        "push_to_remote",
        "list_recipes",
        "load_recipe",
        "migrate_recipe",
        "validate_recipe",
        "get_pipeline_report",
        "get_token_summary",
        "kitchen_status",
        "read_db",
        "fetch_github_issue",
    ]
    present = [sym for sym in shim_symbols if hasattr(srv, sym)]
    assert not present, f"Shim re-exports found in server namespace (must be removed): {present}"


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

    with patch("autoskillit.server.mcp", mock_mcp):
        from autoskillit.server._state import _initialize

        _initialize(ctx)

    mock_mcp.disable.assert_not_called()
