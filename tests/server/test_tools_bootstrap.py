"""Tests for bootstrap composite MCP tools: bootstrap_clone, claim_and_resolve_issue,
create_and_publish_branch."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from autoskillit.server.tools.tools_clone import bootstrap_clone
from autoskillit.server.tools.tools_git import create_and_publish_branch
from autoskillit.server.tools.tools_issue_composite import claim_and_resolve_issue
from tests.conftest import _make_result
from tests.server.conftest import assert_step_timed

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestBootstrapClone:
    @pytest.mark.anyio
    async def test_bootstrap_clone_success(self, tool_ctx, tmp_path):
        """bootstrap_clone returns work_dir, remote_url, base_sha, merge_target."""
        mock_mgr = MagicMock()
        mock_mgr.clone_repo.return_value = {
            "clone_path": str(tmp_path),
            "source_dir": "/src",
            "remote_url": "https://github.com/o/r.git",
        }
        tool_ctx.clone_mgr = mock_mgr
        tool_ctx.runner.push(_make_result(0, "abc123def\n", ""))
        result = json.loads(
            await bootstrap_clone(source_dir="/src", run_name="impl", base_branch="main")
        )
        assert result["work_dir"] == str(tmp_path)
        assert result["remote_url"] == "https://github.com/o/r.git"
        assert result["base_sha"] == "abc123def"
        assert result["merge_target"] == "main"

    @pytest.mark.anyio
    async def test_bootstrap_clone_gate_closed(self, tool_ctx):
        tool_ctx.gate.enabled = False
        result = json.loads(
            await bootstrap_clone(source_dir="/src", run_name="impl", base_branch="main")
        )
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_bootstrap_clone_clone_failure(self, tool_ctx):
        mock_mgr = MagicMock()
        mock_mgr.clone_repo.side_effect = RuntimeError("disk full")
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(
            await bootstrap_clone(source_dir="/src", run_name="impl", base_branch="main")
        )
        assert "error" in result
        assert "disk full" in result["error"]

    @pytest.mark.anyio
    async def test_bootstrap_clone_revparse_failure(self, tool_ctx, tmp_path):
        mock_mgr = MagicMock()
        mock_mgr.clone_repo.return_value = {
            "clone_path": str(tmp_path),
            "source_dir": "/src",
            "remote_url": "url",
        }
        tool_ctx.clone_mgr = mock_mgr
        tool_ctx.runner.push(_make_result(128, "", "fatal: not a git repo\n"))
        result = json.loads(
            await bootstrap_clone(source_dir="/src", run_name="impl", base_branch="main")
        )
        assert "error" in result
        assert "rev-parse" in result["error"]

    @pytest.mark.anyio
    async def test_bootstrap_clone_timing(self, tool_ctx, tmp_path):
        mock_mgr = MagicMock()
        mock_mgr.clone_repo.return_value = {
            "clone_path": str(tmp_path),
            "source_dir": "/src",
            "remote_url": "url",
        }
        tool_ctx.clone_mgr = mock_mgr
        tool_ctx.runner.push(_make_result(0, "sha\n", ""))
        await bootstrap_clone(
            source_dir="/src", run_name="impl", base_branch="main", step_name="bootstrap"
        )
        assert_step_timed(tool_ctx.timing_log, "bootstrap")

    @pytest.mark.anyio
    async def test_bootstrap_clone_sub_timings(self, tool_ctx, tmp_path):
        mock_mgr = MagicMock()
        mock_mgr.clone_repo.return_value = {
            "clone_path": str(tmp_path),
            "source_dir": "/src",
            "remote_url": "url",
        }
        tool_ctx.clone_mgr = mock_mgr
        tool_ctx.runner.push(_make_result(0, "sha\n", ""))
        result = json.loads(
            await bootstrap_clone(source_dir="/src", run_name="impl", base_branch="main")
        )
        assert "timings" in result
        assert "clone_ms" in result["timings"]
        assert "rev_parse_ms" in result["timings"]


class TestClaimAndResolveIssue:
    @pytest.mark.anyio
    async def test_claim_and_resolve_issue_claimed(self, tool_ctx):
        tool_ctx.github_client = AsyncMock()
        tool_ctx.github_client.fetch_title = AsyncMock(
            return_value={"success": True, "number": 42, "title": "Fix bug", "slug": "fix-bug"}
        )
        tool_ctx.github_client.fetch_issue = AsyncMock(
            return_value={"success": True, "labels": []}
        )
        tool_ctx.github_client.ensure_label = AsyncMock(return_value={"success": True})
        tool_ctx.github_client.swap_labels = AsyncMock(return_value={"success": True})
        result = json.loads(await claim_and_resolve_issue(issue_url="owner/repo#42"))
        assert result["claimed"] is True
        assert result["issue_number"] == 42
        assert result["issue_title"] == "Fix bug"
        assert result["issue_slug"] == "fix-bug"

    @pytest.mark.anyio
    async def test_claim_and_resolve_issue_already_claimed(self, tool_ctx):
        tool_ctx.github_client = AsyncMock()
        tool_ctx.github_client.fetch_title = AsyncMock(
            return_value={"success": True, "number": 42, "title": "Fix bug", "slug": "fix-bug"}
        )
        tool_ctx.github_client.fetch_issue = AsyncMock(
            return_value={
                "success": True,
                "labels": [{"name": "in-progress"}],
            }
        )
        result = json.loads(await claim_and_resolve_issue(issue_url="owner/repo#42"))
        assert result["claimed"] is False
        assert result["issue_number"] == 42
        assert result["issue_title"] == "Fix bug"
        assert result["issue_slug"] == "fix-bug"

    @pytest.mark.anyio
    async def test_claim_and_resolve_issue_reentry(self, tool_ctx):
        tool_ctx.github_client = AsyncMock()
        tool_ctx.github_client.fetch_title = AsyncMock(
            return_value={"success": True, "number": 42, "title": "Fix bug", "slug": "fix-bug"}
        )
        tool_ctx.github_client.fetch_issue = AsyncMock(
            return_value={
                "success": True,
                "labels": [{"name": "in-progress"}],
            }
        )
        result = json.loads(
            await claim_and_resolve_issue(issue_url="owner/repo#42", allow_reentry=True)
        )
        assert result["claimed"] is True
        assert result["reentry"] is True

    @pytest.mark.anyio
    async def test_claim_and_resolve_issue_title_failure(self, tool_ctx):
        tool_ctx.github_client = AsyncMock()
        tool_ctx.github_client.fetch_title = AsyncMock(
            return_value={"success": False, "error": "not found"}
        )
        result = json.loads(await claim_and_resolve_issue(issue_url="owner/repo#42"))
        assert result["success"] is False
        assert "not found" in result["error"]

    @pytest.mark.anyio
    async def test_claim_and_resolve_issue_gate_closed(self, tool_ctx):
        tool_ctx.gate.enabled = False
        result = json.loads(await claim_and_resolve_issue(issue_url="owner/repo#42"))
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_claim_and_resolve_issue_timings(self, tool_ctx):
        tool_ctx.github_client = AsyncMock()
        tool_ctx.github_client.fetch_title = AsyncMock(
            return_value={"success": True, "number": 42, "title": "X", "slug": "x"}
        )
        tool_ctx.github_client.fetch_issue = AsyncMock(
            return_value={"success": True, "labels": []}
        )
        tool_ctx.github_client.ensure_label = AsyncMock(return_value={"success": True})
        tool_ctx.github_client.swap_labels = AsyncMock(return_value={"success": True})
        result = json.loads(await claim_and_resolve_issue(issue_url="owner/repo#42"))
        assert "timings" in result
        assert "fetch_title_ms" in result["timings"]
        assert "claim_ms" in result["timings"]


class TestCreateAndPublishBranch:
    @pytest.mark.anyio
    async def test_create_and_publish_branch_success(self, tool_ctx, tmp_path):
        # ls-remote: empty (branch available)
        tool_ctx.runner.push(_make_result(0, "", ""))
        # branch --show-current
        tool_ctx.runner.push(_make_result(0, "main\n", ""))
        # git checkout -b
        tool_ctx.runner.push(_make_result(0, "", ""))
        mock_mgr = MagicMock()
        mock_mgr.push_to_remote.return_value = {"success": True, "stderr": ""}
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(
            await create_and_publish_branch(
                issue_slug="fix-bug",
                run_name="impl",
                issue_number="42",
                work_dir=str(tmp_path),
                remote_url="https://github.com/o/r.git",
            )
        )
        assert result["merge_target"] == "fix-bug/42"

    @pytest.mark.anyio
    async def test_create_and_publish_branch_collision(self, tool_ctx, tmp_path):
        # ls-remote: branch exists
        tool_ctx.runner.push(_make_result(0, "abc123\trefs/heads/fix-bug/42\n", ""))
        # ls-remote: suffix -2 is free
        tool_ctx.runner.push(_make_result(0, "", ""))
        # branch --show-current
        tool_ctx.runner.push(_make_result(0, "main\n", ""))
        # git checkout -b
        tool_ctx.runner.push(_make_result(0, "", ""))
        mock_mgr = MagicMock()
        mock_mgr.push_to_remote.return_value = {"success": True, "stderr": ""}
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(
            await create_and_publish_branch(
                issue_slug="fix-bug",
                run_name="impl",
                issue_number="42",
                work_dir=str(tmp_path),
                remote_url="https://github.com/o/r.git",
            )
        )
        assert result["merge_target"] == "fix-bug/42-2"

    @pytest.mark.anyio
    async def test_create_and_publish_branch_push_failure(self, tool_ctx, tmp_path):
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "main\n", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))
        mock_mgr = MagicMock()
        mock_mgr.push_to_remote.return_value = {
            "success": False,
            "stderr": "rejected",
            "error_type": "",
        }
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(
            await create_and_publish_branch(
                issue_slug="fix-bug",
                run_name="impl",
                issue_number="42",
                work_dir=str(tmp_path),
                remote_url="https://github.com/o/r.git",
            )
        )
        assert "error" in result

    @pytest.mark.anyio
    async def test_create_and_publish_branch_gate_closed(self, tool_ctx):
        tool_ctx.gate.enabled = False
        result = json.loads(
            await create_and_publish_branch(
                issue_slug="x",
                run_name="impl",
                issue_number="1",
                work_dir="/tmp",
                remote_url="url",
            )
        )
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_create_and_publish_branch_no_issue(self, tool_ctx, tmp_path):
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "main\n", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))
        mock_mgr = MagicMock()
        mock_mgr.push_to_remote.return_value = {"success": True, "stderr": ""}
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(
            await create_and_publish_branch(
                issue_slug="",
                run_name="impl",
                issue_number="",
                work_dir=str(tmp_path),
                remote_url="url",
            )
        )
        # branch name is "impl/YYYYMMDD"
        assert result["merge_target"].startswith("impl/")

    @pytest.mark.anyio
    async def test_create_and_publish_branch_timings(self, tool_ctx, tmp_path):
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "main\n", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))
        mock_mgr = MagicMock()
        mock_mgr.push_to_remote.return_value = {"success": True, "stderr": ""}
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(
            await create_and_publish_branch(
                issue_slug="x",
                run_name="impl",
                issue_number="1",
                work_dir=str(tmp_path),
                remote_url="url",
            )
        )
        assert "timings" in result
        assert "branch_create_ms" in result["timings"]
        assert "push_ms" in result["timings"]


def test_implementation_recipe_valid():
    """Consolidated bootstrap steps pass recipe validation."""
    from pathlib import Path

    from autoskillit.recipe.io import load_recipe
    from autoskillit.recipe.validator import validate_recipe

    recipe_path = (
        Path(__file__).parent.parent.parent
        / "src"
        / "autoskillit"
        / "recipes"
        / "implementation.yaml"
    )
    recipe = load_recipe(recipe_path)
    errors = validate_recipe(recipe)
    assert not errors, f"implementation.yaml failed validation: {errors}"
