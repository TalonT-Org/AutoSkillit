"""Tests for autoskillit server clone tools."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from autoskillit.server.tools_clone import clone_repo, push_to_remote, remove_clone


class TestCloneRepoTool:
    @pytest.mark.asyncio
    async def test_returns_gate_error_when_disabled(self, tool_ctx):
        tool_ctx.gate.enabled = False
        result = json.loads(await clone_repo(source_dir="/src", run_name="test"))
        assert result["subtype"] == "gate_error"

    @pytest.mark.asyncio
    async def test_delegates_to_workspace_clone(self, tool_ctx):
        mock_mgr = MagicMock()
        mock_mgr.clone_repo.return_value = {"clone_path": "/clone/path", "source_dir": "/src"}
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(await clone_repo(source_dir="/src", run_name="myrun"))
        assert result["clone_path"] == "/clone/path"
        assert result["source_dir"] == "/src"

    @pytest.mark.asyncio
    async def test_returns_error_on_value_error(self, tool_ctx):
        mock_mgr = MagicMock()
        mock_mgr.clone_repo.side_effect = ValueError("resolved to nonexistent")
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(await clone_repo(source_dir="/bad/path", run_name="run"))
        assert "error" in result
        assert "resolved to" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_error_on_runtime_error(self, tool_ctx):
        mock_mgr = MagicMock()
        mock_mgr.clone_repo.side_effect = RuntimeError("git clone failed")
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(await clone_repo(source_dir="/src", run_name="run"))
        assert "error" in result

    @pytest.mark.asyncio
    async def test_cb17_forwards_branch_to_clone_manager(self, tool_ctx):
        """T_CB17: branch param is forwarded to the underlying clone_repo call."""
        mock_mgr = MagicMock()
        mock_mgr.clone_repo.return_value = {"clone_path": "/clone/path", "source_dir": "/src"}
        tool_ctx.clone_mgr = mock_mgr
        await clone_repo(source_dir="/src", run_name="r", branch="dev")
        mock_mgr.clone_repo.assert_called_once_with("/src", "r", "dev", "")

    @pytest.mark.asyncio
    async def test_cb18_forwards_strategy_to_clone_manager(self, tool_ctx):
        """T_CB18: strategy param is forwarded to the underlying clone_repo call."""
        mock_mgr = MagicMock()
        mock_mgr.clone_repo.return_value = {"clone_path": "/clone/path", "source_dir": "/src"}
        tool_ctx.clone_mgr = mock_mgr
        await clone_repo(source_dir="/src", run_name="r", strategy="proceed")
        mock_mgr.clone_repo.assert_called_once_with("/src", "r", "", "proceed")

    @pytest.mark.asyncio
    async def test_cb19_returns_uncommitted_changes_result_as_json(self, tool_ctx):
        """T_CB19: uncommitted_changes warning dict passes through without 'error' key."""
        uncommitted_result = {
            "uncommitted_changes": "true",
            "source_dir": "/src",
            "branch": "main",
            "changed_files": "M file.py",
            "total_changed": "1",
        }
        mock_mgr = MagicMock()
        mock_mgr.clone_repo.return_value = uncommitted_result
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(await clone_repo(source_dir="/src", run_name="r"))
        assert result["uncommitted_changes"] == "true"
        assert "error" not in result


class TestRemoveCloneTool:
    @pytest.mark.asyncio
    async def test_returns_gate_error_when_disabled(self, tool_ctx):
        tool_ctx.gate.enabled = False
        result = json.loads(await remove_clone(clone_path="/clone", keep="false"))
        assert result["subtype"] == "gate_error"

    @pytest.mark.asyncio
    async def test_delegates_to_workspace_clone(self, tool_ctx):
        mock_mgr = MagicMock()
        mock_mgr.remove_clone.return_value = {"removed": "true"}
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(await remove_clone(clone_path="/clone/path", keep="false"))
        assert result["removed"] == "true"

    @pytest.mark.asyncio
    async def test_keep_true_passes_through(self, tool_ctx):
        mock_mgr = MagicMock()
        mock_mgr.remove_clone.return_value = {"removed": "false", "reason": "keep=true"}
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(await remove_clone(clone_path="/clone/path", keep="true"))
        assert result["removed"] == "false"

    @pytest.mark.asyncio
    async def test_always_routes_success_even_on_partial_failure(self, tool_ctx):
        mock_mgr = MagicMock()
        mock_mgr.remove_clone.return_value = {"removed": "false", "reason": "OSError"}
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(await remove_clone(clone_path="/bad", keep="false"))
        assert "error" not in result


class TestPushToRemoteTool:
    @pytest.mark.asyncio
    async def test_returns_gate_error_when_disabled(self, tool_ctx):
        tool_ctx.gate.enabled = False
        result = json.loads(await push_to_remote(clone_path="/c", source_dir="/s", branch="main"))
        assert result["subtype"] == "gate_error"

    @pytest.mark.asyncio
    async def test_delegates_to_workspace_clone_on_success(self, tool_ctx):
        mock_mgr = MagicMock()
        mock_mgr.push_to_remote.return_value = {"success": "true", "stderr": ""}
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(
            await push_to_remote(clone_path="/clone", source_dir="/src", branch="main")
        )
        assert result["success"] == "true"
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_returns_error_key_when_push_fails(self, tool_ctx):
        mock_mgr = MagicMock()
        mock_mgr.push_to_remote.return_value = {"success": False, "stderr": "remote rejected"}
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(
            await push_to_remote(clone_path="/clone", source_dir="/src", branch="main")
        )
        assert "error" in result
        assert "remote rejected" in result["stderr"]
