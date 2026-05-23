"""Tests for create_unique_branch and check_pr_mergeable tools."""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_git import check_pr_mergeable, create_unique_branch
from tests.conftest import _make_result

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestCreateUniqueBranch:
    @pytest.mark.anyio
    async def test_creates_branch_when_unique(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # ls-remote: empty = absent
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "main\n", "")
        )  # branch --show-current (HEAD state)
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # git checkout -b
        result = json.loads(await create_unique_branch("feat-foo", 42, "origin", "."))
        assert result["branch_name"] == "feat-foo-42"
        assert result["was_unique"] is True

    @pytest.mark.anyio
    async def test_appends_suffix_when_branch_exists_on_remote(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "abc123\trefs/heads/feat-foo-42\n", "")
        )  # exists
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # -2 not found
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "main\n", "")
        )  # branch --show-current (HEAD state)
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # checkout -b feat-foo-42-2
        result = json.loads(await create_unique_branch("feat-foo", 42, "origin", "."))
        assert result["branch_name"] == "feat-foo-42-2"
        assert result["was_unique"] is False

    @pytest.mark.anyio
    async def test_ls_remote_auth_failure_falls_back_gracefully(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.runner.push(_make_result(128, "", "fatal: Authentication failed"))
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "main\n", "")
        )  # branch --show-current (HEAD state)
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # checkout proceeds with base name
        result = json.loads(await create_unique_branch("feat-foo", 42, "origin", "."))
        assert result["branch_name"] == "feat-foo-42"
        assert result["was_unique"] is True

    @pytest.mark.anyio
    async def test_no_issue_uses_slug_only(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # ls-remote
        tool_ctx_kitchen_open.runner.push(_make_result(0, "main\n", ""))  # branch --show-current
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # checkout -b
        result = json.loads(await create_unique_branch("feat-bar", None, "origin", "."))
        assert result["branch_name"] == "feat-bar"

    @pytest.mark.anyio
    async def test_gate_closed_returns_gate_error(self, tool_ctx):
        tool_ctx.gate.disable()
        result = json.loads(await create_unique_branch("foo", 1, "origin", "."))
        assert result["success"] is False
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_timing_recorded_when_step_name_provided(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # ls-remote
        tool_ctx_kitchen_open.runner.push(_make_result(0, "main\n", ""))  # branch --show-current
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # checkout -b
        await create_unique_branch("feat-x", 1, "origin", ".", step_name="branch_step")
        assert any(
            e["step_name"] == "branch_step" for e in tool_ctx_kitchen_open.timing_log.get_report()
        )

    @pytest.mark.anyio
    async def test_create_unique_branch_uses_base_branch_name_when_provided(
        self, tool_ctx_kitchen_open
    ):
        """AP3: when base_branch_name is provided, use it directly as the base."""
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # ls-remote: empty = absent
        tool_ctx_kitchen_open.runner.push(_make_result(0, "main\n", ""))  # branch --show-current
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # git checkout -b
        result = json.loads(await create_unique_branch(base_branch_name="impl/238", cwd="."))
        assert result["branch_name"] == "impl/238"
        # ls-remote must check the exact base_branch_name, not slug-issue composition
        ls_remote_cmd = next(
            (
                args[0]
                for args in tool_ctx_kitchen_open.runner.call_args_list
                if "ls-remote" in args[0]
            ),
            None,
        )
        assert ls_remote_cmd is not None, "No ls-remote subprocess call found"
        assert any("impl/238" in arg for arg in ls_remote_cmd)

    @pytest.mark.anyio
    async def test_reports_base_ref_in_return_value(self, tool_ctx_kitchen_open):
        """Test 1.6: create_unique_branch reports base_ref (the branch HEAD was on)."""
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # ls-remote: empty = absent
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "feature-branch\n", "")
        )  # branch --show-current
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # git checkout -b
        result = json.loads(await create_unique_branch("feat-foo", 42, "origin", "."))
        assert result["branch_name"] == "feat-foo-42"
        assert result["was_unique"] is True
        assert result["base_ref"] == "feature-branch"

    @pytest.mark.anyio
    async def test_reports_detached_head_as_base_ref(self, tool_ctx_kitchen_open):
        """create_unique_branch reports DETACHED_HEAD when HEAD is detached."""
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # ls-remote: empty = absent
        tool_ctx_kitchen_open.runner.push(
            _make_result(0, "", "")
        )  # branch --show-current returns empty
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))  # git checkout -b
        result = json.loads(await create_unique_branch("feat-foo", 42, "origin", "."))
        assert result["branch_name"] == "feat-foo-42"
        assert result["base_ref"] == "DETACHED_HEAD"


class TestCheckPrMergeable:
    @pytest.mark.anyio
    async def test_mergeable_pr(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.runner.push(
            _make_result(
                0, json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"}), ""
            )
        )
        result = json.loads(await check_pr_mergeable(42, "."))
        assert result["mergeable"] is True
        assert result["merge_state_status"] == "CLEAN"
        assert result["mergeable_status"] == "MERGEABLE"

    @pytest.mark.anyio
    async def test_conflicting_pr_is_not_mergeable(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.runner.push(
            _make_result(
                0, json.dumps({"mergeable": "CONFLICTING", "mergeStateStatus": "DIRTY"}), ""
            )
        )
        result = json.loads(await check_pr_mergeable(42, "."))
        assert result["mergeable"] is False
        assert result["merge_state_status"] == "DIRTY"
        assert result["mergeable_status"] == "CONFLICTING"

    @pytest.mark.anyio
    async def test_unknown_mergeable_status_returned_raw(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.runner.push(
            _make_result(
                0, json.dumps({"mergeable": "UNKNOWN", "mergeStateStatus": "UNKNOWN"}), ""
            )
        )
        result = json.loads(await check_pr_mergeable(42, "."))
        assert result["mergeable"] is False  # UNKNOWN != MERGEABLE → False
        assert result["mergeable_status"] == "UNKNOWN"

    @pytest.mark.anyio
    async def test_gh_command_failure_returns_error(self, tool_ctx):
        tool_ctx.runner.push(_make_result(1, "", "pr not found"))
        result = json.loads(await check_pr_mergeable(99, "."))
        assert result["success"] is False

    @pytest.mark.anyio
    async def test_gate_closed_returns_gate_error(self, tool_ctx):
        tool_ctx.gate.disable()
        result = json.loads(await check_pr_mergeable(1, "."))
        assert result["success"] is False
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_repo_flag_passed_to_gh(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.runner.push(
            _make_result(
                0, json.dumps({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"}), ""
            )
        )
        result = json.loads(await check_pr_mergeable(42, ".", repo="owner/myrepo"))
        call_cmd = tool_ctx_kitchen_open.runner.call_args_list[-1][0]
        assert "-R" in call_cmd
        assert result["mergeable"] is True
