"""Tests for classify_fix tool."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from autoskillit.config import AutomationConfig, ClassifyFixConfig
from autoskillit.core.types import RestartScope
from autoskillit.server.tools.tools_git import classify_fix
from tests.conftest import _make_result
from tests.server.conftest import assert_no_timing, assert_step_timed

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestClassifyFix:
    """T4, T5: classify_fix returns correct restart scope based on changed files."""

    @pytest.fixture(autouse=True)
    def _set_prefixes(self, tool_ctx):
        """Configure critical path prefixes for classify_fix tests."""
        tool_ctx.config = AutomationConfig(
            classify_fix=ClassifyFixConfig(
                path_prefixes=[
                    "src/core/",
                    "src/api/",
                    "lib/handlers/",
                ]
            )
        )

    @pytest.mark.anyio
    async def test_critical_files_return_full_restart(self, tool_ctx_kitchen_open, tmp_path):
        changed = "src/core/handler.py\nlib/utils/helpers.py\n"
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # git fetch succeeds
        tool_ctx_kitchen_open.runner.push(_make_result(0, changed, ""))

        result = json.loads(await classify_fix(worktree_path=str(tmp_path), base_branch="main"))

        assert result["restart_scope"] == RestartScope.FULL_RESTART
        assert len(result["critical_files"]) == 1
        assert result["critical_files"][0] == "src/core/handler.py"
        assert len(result["all_changed_files"]) == 2

    @pytest.mark.anyio
    async def test_non_critical_returns_partial_restart(self, tool_ctx_kitchen_open, tmp_path):
        changed = "src/workers/runner.py\nlib/utils/helpers.py\n"
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # git fetch succeeds
        tool_ctx_kitchen_open.runner.push(_make_result(0, changed, ""))

        result = json.loads(await classify_fix(worktree_path=str(tmp_path), base_branch="main"))

        assert result["restart_scope"] == RestartScope.PARTIAL_RESTART
        assert result["critical_files"] == []
        assert len(result["all_changed_files"]) == 2

    @pytest.mark.anyio
    async def test_git_diff_failure(self, tool_ctx_kitchen_open, tmp_path):
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # git fetch succeeds
        tool_ctx_kitchen_open.runner.push(_make_result(128, "", "fatal: bad revision"))

        result = json.loads(await classify_fix(worktree_path=str(tmp_path), base_branch="main"))

        assert "restart_scope" in result
        assert "Cannot diff" in result["reason"]

    @pytest.mark.anyio
    async def test_critical_path_in_diff_triggers_full_restart(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        changed = "src/api/routes.py\n"
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # git fetch succeeds
        tool_ctx_kitchen_open.runner.push(_make_result(0, changed, ""))

        result = json.loads(await classify_fix(worktree_path=str(tmp_path), base_branch="main"))

        assert result["restart_scope"] == RestartScope.FULL_RESTART

    @pytest.mark.anyio
    async def test_classify_fix_nonexistent_worktree_path_returns_clear_error(
        self, tool_ctx_kitchen_open
    ):
        """[FAILS NOW] nonexistent path returns a distinct path-not-found error."""
        result = json.loads(await classify_fix("/no/such/path", "main"))
        assert result["restart_scope"] == RestartScope.FULL_RESTART
        assert "does not exist" in result["reason"].lower()

    @pytest.mark.anyio
    async def test_classify_fix_git_fetch_called_before_diff(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        """git fetch must be issued before git diff."""
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # fetch succeeds
        tool_ctx_kitchen_open.runner.push(_make_result(0, "src/foo.py\n", ""))  # diff succeeds
        with patch(
            "autoskillit.server._misc.resolve_remote_name",
            new=AsyncMock(return_value="origin"),
        ):
            await classify_fix(str(tmp_path), "main")
        assert tool_ctx_kitchen_open.runner.call_args_list[0][0] == [
            "git",
            "fetch",
            "origin",
            "main",
        ]
        assert tool_ctx_kitchen_open.runner.call_args_list[1][0][0:3] == [
            "git",
            "diff",
            "--name-only",
        ]

    @pytest.mark.anyio
    async def test_classify_fix_gate_closed_returns_gate_error(
        self, tool_ctx, monkeypatch, tmp_path
    ):
        """[NEW COVERAGE] gate closed path returns gate_error."""
        from autoskillit.pipeline import DefaultGateState

        monkeypatch.setattr(tool_ctx, "gate", DefaultGateState(enabled=False))
        result = json.loads(await classify_fix(str(tmp_path), "main"))
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_classify_fix_empty_diff_returns_partial_restart_with_no_files(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        """[NEW COVERAGE] empty diff is a valid state returning partial_restart with no files."""
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # fetch
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # diff (empty)
        result = json.loads(await classify_fix(str(tmp_path), "main"))
        assert result["restart_scope"] == RestartScope.PARTIAL_RESTART
        assert result["all_changed_files"] == []
        assert result["critical_files"] == []


class TestClassifyFixTiming:
    """classify_fix records wall-clock timing when step_name is provided."""

    @pytest.mark.anyio
    async def test_classify_fix_step_name_records_timing(self, tool_ctx_kitchen_open, tmp_path):
        tool_ctx_kitchen_open.runner.push(_make_result(stdout="src/other/file.py\n"))
        await classify_fix(str(tmp_path), "main", step_name="classify")
        assert_step_timed(tool_ctx_kitchen_open.timing_log, "classify")

    @pytest.mark.anyio
    async def test_classify_fix_empty_step_name_skips_timing(self, tool_ctx, tmp_path):
        tool_ctx.runner.push(_make_result(stdout="src/other/file.py\n"))
        await classify_fix(str(tmp_path), "main")
        assert_no_timing(tool_ctx.timing_log)


class TestClassifyFixRemoteResolution:
    """T3: classify_fix resolves the remote via resolve_remote_name internally."""

    @pytest.mark.anyio
    async def test_classify_fix_uses_upstream_when_available(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        """When resolve_remote_name returns 'upstream', fetch and diff use 'upstream'."""
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # fetch succeeds
        tool_ctx_kitchen_open.runner.push(_make_result(0, "src/foo.py\n", ""))  # diff succeeds

        with patch(
            "autoskillit.server._misc.resolve_remote_name",
            new=AsyncMock(return_value="upstream"),
        ):
            await classify_fix(str(tmp_path), "main")

        fetch_cmd = tool_ctx_kitchen_open.runner.call_args_list[0][0]
        assert fetch_cmd == ["git", "fetch", "upstream", "main"]
        diff_cmd = tool_ctx_kitchen_open.runner.call_args_list[1][0]
        assert "upstream/main...HEAD" in diff_cmd[-1]

    @pytest.mark.anyio
    async def test_classify_fix_falls_back_to_origin(self, tool_ctx_kitchen_open, tmp_path):
        """When resolve_remote_name returns 'origin', fetch and diff use 'origin'."""
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # fetch succeeds
        tool_ctx_kitchen_open.runner.push(_make_result(0, "src/bar.py\n", ""))  # diff succeeds

        with patch(
            "autoskillit.server._misc.resolve_remote_name",
            new=AsyncMock(return_value="origin"),
        ):
            await classify_fix(str(tmp_path), "main")

        fetch_cmd = tool_ctx_kitchen_open.runner.call_args_list[0][0]
        assert fetch_cmd == ["git", "fetch", "origin", "main"]
        diff_cmd = tool_ctx_kitchen_open.runner.call_args_list[1][0]
        assert "origin/main...HEAD" in diff_cmd[-1]
