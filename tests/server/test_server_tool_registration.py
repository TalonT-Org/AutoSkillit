"""Tests for MCP tool registration, config-driven behavior, and schema contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.config import (
    AutomationConfig,
    SafetyConfig,
)
from autoskillit.pipeline.gate import DefaultGateState
from autoskillit.server.tools_status import (
    get_quota_events,
    get_token_summary,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestNoSkillsDirectoryProvider:
    """T3: SkillsDirectoryProvider is not used in the new plugin architecture."""

    def test_no_skills_directory_provider(self):
        """server.py must not reference SkillsDirectoryProvider."""
        import autoskillit.server as server_module

        source = Path(server_module.__file__).read_text()
        assert "SkillsDirectoryProvider" not in source


class TestToolRegistration:
    """All 49 tools are registered on the MCP server."""

    @pytest.mark.anyio
    async def test_all_tools_exist(self, kitchen_enabled):
        from fastmcp.client import Client

        from autoskillit.server import mcp

        async with Client(mcp) as client:
            all_tools = await client.list_tools()
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
            "check_repo_merge_state",
            "toggle_auto_merge",
            "enqueue_pr",
            "get_ci_status",
            "open_kitchen",
            "close_kitchen",
            "disable_quota_guard",
            "create_unique_branch",
            "check_pr_mergeable",
            "write_telemetry_files",
            "get_pr_reviews",
            "bulk_close_issues",
            "set_commit_status",
            "register_clone_status",
            "batch_cleanup_clones",
            "dispatch_food_truck",
            "record_gate_dispatch",
            "reload_session",
            "analyze_tool_sequences",
        }
        assert expected == tool_names

    @pytest.mark.anyio
    async def test_ungated_tools_lack_kitchen_tag(self, kitchen_enabled):
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
        all_tools_with_kitchen = {t.name: t for t in await mcp.list_tools()}
        tc = all_tools_with_kitchen.get("test_check")
        assert tc is not None, "test_check must be registered"
        assert "kitchen" in tc.tags, "test_check must carry the 'kitchen' tag"

    @pytest.mark.anyio
    async def test_kitchen_tools_have_autoskillit_and_kitchen_tags(self, kitchen_enabled):
        """Every tool in GATED_TOOLS carries both 'autoskillit' and 'kitchen' tags."""
        from autoskillit.pipeline.gate import GATED_TOOLS
        from autoskillit.server import mcp

        all_tools = {t.name: t for t in await mcp.list_tools()}
        for name in GATED_TOOLS:
            tool = all_tools.get(name)
            assert tool is not None, f"Gated tool '{name}' not registered on server"
            assert "autoskillit" in tool.tags, f"Gated tool '{name}' missing 'autoskillit' tag"
            assert "kitchen" in tool.tags, f"Gated tool '{name}' missing 'kitchen' tag"
            assert "automation" not in tool.tags, (
                f"Gated tool '{name}' still has deprecated 'automation' tag"
            )

    @pytest.mark.anyio
    async def test_all_tools_tagged_autoskillit(self, kitchen_enabled, headless_enabled):
        """Every registered tool carries the 'autoskillit' tag."""
        from autoskillit.server import mcp

        all_tools = await mcp.list_tools()
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
    async def test_test_check_has_headless_tag(self, kitchen_enabled):
        from autoskillit.server import mcp

        all_tools = {t.name: t for t in await mcp.list_tools()}
        tc = all_tools.get("test_check")
        assert tc is not None
        assert "headless" in tc.tags
        assert "kitchen" in tc.tags
        assert "autoskillit" in tc.tags

    @pytest.mark.anyio
    async def test_headless_enable_reveals_only_headless_tagged_tools(self, headless_enabled):
        from fastmcp.client import Client

        from autoskillit.pipeline.gate import GATED_TOOLS
        from autoskillit.server import mcp

        async with Client(mcp) as client:
            visible = {t.name for t in await client.list_tools()}
        assert "test_check" in visible
        # Kitchen-only tools (no headless tag) must NOT be revealed
        kitchen_only = GATED_TOOLS - {"test_check"}
        for name in kitchen_only:
            assert name not in visible, f"{name} should not be revealed by headless-only enable"

    @pytest.mark.anyio
    async def test_no_tool_has_bare_kitchen_tag_only(self, kitchen_enabled) -> None:
        """Every kitchen-tagged tool must also carry kitchen-core or a pack tag."""
        from autoskillit.core.types import PACK_REGISTRY
        from autoskillit.server import mcp

        pack_tags = frozenset(PACK_REGISTRY.keys()) | {"headless"}
        all_tools = {t.name: t for t in await mcp.list_tools()}
        for tool in all_tools.values():
            if "kitchen" not in tool.tags:
                continue
            has_subtag = bool(tool.tags & pack_tags)
            assert has_subtag, (
                f"{tool.name} has 'kitchen' tag but no pack/kitchen-core/headless subtag: "
                f"{sorted(tool.tags)}"
            )

    @pytest.mark.anyio
    async def test_kitchen_core_and_packs_partition_kitchen_gated_tools(
        self, kitchen_enabled
    ) -> None:
        """Every gated/headless tool has kitchen-core and/or a pack tag — full coverage."""
        from autoskillit.core.types import HEADLESS_TOOLS, PACK_REGISTRY
        from autoskillit.pipeline.gate import GATED_TOOLS
        from autoskillit.server import mcp

        all_gated = GATED_TOOLS | HEADLESS_TOOLS
        pack_tags = frozenset(PACK_REGISTRY.keys())

        all_tools = {t.name: t for t in await mcp.list_tools()}
        missing: list[str] = []
        for name in sorted(all_gated):
            tool = all_tools[name]
            has_classification = bool(tool.tags & pack_tags)
            if not has_classification:
                missing.append(f"{name}: tags={sorted(tool.tags)}")

        assert not missing, (
            "Tools in GATED_TOOLS|HEADLESS_TOOLS without kitchen-core or pack tag:\n"
            + "\n".join(f"  {m}" for m in missing)
        )


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
        assert tool_ctx.runner.call_args_list[0][2] == pytest.approx(300.0, abs=1.0)

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
                "worktree /repo\nHEAD abc\nbranch refs/heads/dev\n\n",
                "",
            )
        )  # worktree list
        tool_ctx.runner.push(_make_result(0, "dev\n", ""))  # git branch --show-current (step 7.5)
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

    async def _get_all_tools(self, kitchen_enabled) -> dict:
        """Return dict of tool_name -> tool for all tools, including kitchen-gated ones."""
        from fastmcp.client import Client

        from autoskillit.server import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()
        return {t.name: t for t in tools}

    @pytest.mark.anyio
    async def test_tool_descriptions_contain_no_legacy_terms(self, kitchen_enabled):
        """No registered tool should reference old package terminology."""
        all_tools_dict = await self._get_all_tools(kitchen_enabled)
        all_tools = list(all_tools_dict.values())
        for tool in all_tools:
            desc = (tool.description or "").lower()
            for term in self.FORBIDDEN_TERMS:
                assert term not in desc, (
                    f"Tool '{tool.name}' description contains legacy term '{term}'"
                )

    @pytest.mark.anyio
    async def test_tool_docstrings_contain_required_cross_refs(self, kitchen_enabled):
        """Tool docstrings must contain required cross-references."""
        tools = await self._get_all_tools(kitchen_enabled)
        for tool_name, required_terms in self.REQUIRED_CROSS_REFS.items():
            tool = tools.get(tool_name)
            assert tool is not None, f"Tool '{tool_name}' not found in server"
            desc = tool.description or ""
            for term in required_terms:
                assert term in desc, f"Tool '{tool_name}' description must reference '{term}'"

    @pytest.mark.anyio
    async def test_classify_fix_docstring_has_routing_guidance(self, kitchen_enabled):
        """classify_fix must explain what to do with each return value."""
        tools = await self._get_all_tools(kitchen_enabled)
        assert "classify_fix" in tools, "Tool 'classify_fix' not found in server"
        desc = tools["classify_fix"].description or ""
        # Must mention both routing outcomes
        assert "full_restart" in desc
        assert "partial_restart" in desc
        # Must mention at least one skill as routing target
        assert "investigate" in desc or "implement" in desc

    @pytest.mark.anyio
    async def test_recipe_tools_have_disambiguation(self, kitchen_enabled):
        """All recipe-related tools must carry the 'NOT slash commands' disclaimer."""
        tools = await self._get_all_tools(kitchen_enabled)
        recipe_tools = ["list_recipes", "load_recipe", "validate_recipe"]
        for tool_name in recipe_tools:
            tool = tools.get(tool_name)
            assert tool is not None, f"Tool '{tool_name}' not found"
            desc = tool.description or ""
            assert "NOT slash commands" in desc, (
                f"Tool '{tool_name}' must contain 'NOT slash commands' disclaimer"
            )

    @pytest.mark.anyio
    async def test_load_recipe_names_all_forbidden_tools(self, kitchen_enabled):
        """load_recipe must enumerate all forbidden native tools."""
        tools = await self._get_all_tools(kitchen_enabled)
        desc = tools["load_recipe"].description or ""

        missing = [t for t in self.FORBIDDEN_NATIVE_TOOLS if t not in desc]
        assert not missing, (
            f"load_recipe docstring must name all forbidden tools. Missing: {missing}"
        )

    @pytest.mark.anyio
    async def test_pipeline_tools_have_orchestrator_guidance(self, kitchen_enabled):
        """run_skill must reinforce MCP-only delegation."""
        tools = await self._get_all_tools(kitchen_enabled)
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
    async def test_run_skill_names_all_forbidden_tools(self, kitchen_enabled):
        """run_skill docstring must name all forbidden tools."""
        from autoskillit.server import PIPELINE_FORBIDDEN_TOOLS

        tools = await self._get_all_tools(kitchen_enabled)
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
