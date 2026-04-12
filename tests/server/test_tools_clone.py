"""Tests for autoskillit server clone tools."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.server.tools_clone import (
    batch_cleanup_clones,
    clone_repo,
    push_to_remote,
    register_clone_status,
    remove_clone,
)
from autoskillit.workspace import clone_registry


class TestCloneRepoTool:
    @pytest.mark.anyio
    async def test_returns_gate_error_when_disabled(self, tool_ctx):
        tool_ctx.gate.enabled = False
        result = json.loads(await clone_repo(source_dir="/src", run_name="test"))
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_delegates_to_workspace_clone(self, tool_ctx):
        mock_mgr = MagicMock()
        mock_mgr.clone_repo.return_value = {"clone_path": "/clone/path", "source_dir": "/src"}
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(await clone_repo(source_dir="/src", run_name="myrun"))
        assert result["clone_path"] == "/clone/path"
        assert result["source_dir"] == "/src"

    @pytest.mark.anyio
    async def test_returns_error_on_value_error(self, tool_ctx):
        mock_mgr = MagicMock()
        mock_mgr.clone_repo.side_effect = ValueError("resolved to nonexistent")
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(await clone_repo(source_dir="/bad/path", run_name="run"))
        assert "error" in result
        assert "resolved to" in result["error"]

    @pytest.mark.anyio
    async def test_returns_error_on_runtime_error(self, tool_ctx):
        mock_mgr = MagicMock()
        mock_mgr.clone_repo.side_effect = RuntimeError("git clone failed")
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(await clone_repo(source_dir="/src", run_name="run"))
        assert "error" in result

    @pytest.mark.anyio
    async def test_cb17_forwards_branch_to_clone_manager(self, tool_ctx):
        """T_CB17: branch param is forwarded to the underlying clone_repo call."""
        mock_mgr = MagicMock()
        mock_mgr.clone_repo.return_value = {"clone_path": "/clone/path", "source_dir": "/src"}
        tool_ctx.clone_mgr = mock_mgr
        await clone_repo(source_dir="/src", run_name="r", branch="dev")
        mock_mgr.clone_repo.assert_called_once_with("/src", "r", "dev", "", "")

    @pytest.mark.anyio
    async def test_cb18_forwards_strategy_to_clone_manager(self, tool_ctx):
        """T_CB18: strategy param is forwarded to the underlying clone_repo call."""
        mock_mgr = MagicMock()
        mock_mgr.clone_repo.return_value = {"clone_path": "/clone/path", "source_dir": "/src"}
        tool_ctx.clone_mgr = mock_mgr
        await clone_repo(source_dir="/src", run_name="r", strategy="proceed")
        mock_mgr.clone_repo.assert_called_once_with("/src", "r", "", "proceed", "")

    @pytest.mark.anyio
    async def test_ru3_forwards_remote_url_to_clone_manager(self, tool_ctx):
        """T_RU3: remote_url param is forwarded to the underlying clone_repo call."""
        mock_mgr = MagicMock()
        mock_mgr.clone_repo.return_value = {
            "clone_path": "/clone/path",
            "source_dir": "/src",
            "remote_url": "https://github.com/example/repo.git",
        }
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(
            await clone_repo(
                source_dir="/src",
                run_name="r",
                remote_url="https://github.com/example/repo.git",
            )
        )
        mock_mgr.clone_repo.assert_called_once_with(
            "/src", "r", "", "", "https://github.com/example/repo.git"
        )
        assert result["remote_url"] == "https://github.com/example/repo.git"

    @pytest.mark.anyio
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
    @pytest.mark.anyio
    async def test_returns_gate_error_when_disabled(self, tool_ctx):
        tool_ctx.gate.enabled = False
        result = json.loads(await remove_clone(clone_path="/clone", keep="false"))
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_delegates_to_workspace_clone(self, tool_ctx):
        mock_mgr = MagicMock()
        mock_mgr.remove_clone.return_value = {"removed": "true"}
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(await remove_clone(clone_path="/clone/path", keep="false"))
        assert result["removed"] == "true"

    @pytest.mark.anyio
    async def test_keep_true_passes_through(self, tool_ctx):
        mock_mgr = MagicMock()
        mock_mgr.remove_clone.return_value = {"removed": "false", "reason": "keep=true"}
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(await remove_clone(clone_path="/clone/path", keep="true"))
        assert result["removed"] == "false"

    @pytest.mark.anyio
    async def test_always_routes_success_even_on_partial_failure(self, tool_ctx):
        mock_mgr = MagicMock()
        mock_mgr.remove_clone.return_value = {"removed": "false", "reason": "OSError"}
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(await remove_clone(clone_path="/bad", keep="false"))
        assert "error" not in result


class TestPushToRemoteTool:
    @pytest.mark.anyio
    async def test_returns_gate_error_when_disabled(self, tool_ctx):
        tool_ctx.gate.enabled = False
        result = json.loads(await push_to_remote(clone_path="/c", source_dir="/s", branch="main"))
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_delegates_to_workspace_clone_on_success(self, tool_ctx):
        mock_mgr = MagicMock()
        mock_mgr.push_to_remote.return_value = {"success": "true", "stderr": ""}
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(
            await push_to_remote(clone_path="/clone", source_dir="/src", branch="main")
        )
        assert result["success"] == "true"
        assert "error" not in result

    @pytest.mark.anyio
    async def test_returns_error_key_when_push_fails(self, tool_ctx):
        mock_mgr = MagicMock()
        mock_mgr.push_to_remote.return_value = {"success": False, "stderr": "remote rejected"}
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(
            await push_to_remote(clone_path="/clone", source_dir="/src", branch="main")
        )
        assert "error" in result
        assert "remote rejected" in result["stderr"]

    @pytest.mark.anyio
    async def test_push_to_remote_failure_response_includes_success_false(self, tool_ctx):
        """REQ-C9-01: failure payload must include success=False for on_failure routing."""
        mock_mgr = MagicMock()
        mock_mgr.push_to_remote.return_value = {
            "success": False,
            "stderr": "remote rejected",
            "error_type": "push_rejected",
        }
        tool_ctx.clone_mgr = mock_mgr
        result = json.loads(
            await push_to_remote(clone_path="/clone", source_dir="/src", branch="main")
        )
        assert result.get("success") is False
        assert "error" in result

    @pytest.mark.anyio
    async def test_push_to_remote_mcp_handler_passes_force_true_to_clone_mgr(self, tool_ctx):
        """T3: push_to_remote MCP handler converts force='true' to bool True for clone_mgr."""
        mock_mgr = MagicMock()
        mock_mgr.push_to_remote.return_value = {"success": True, "stderr": ""}
        tool_ctx.clone_mgr = mock_mgr
        await push_to_remote(clone_path="/clone", source_dir="/src", branch="main", force="true")
        _args, kwargs = mock_mgr.push_to_remote.call_args
        assert kwargs.get("force") is True

    @pytest.mark.anyio
    async def test_push_to_remote_mcp_handler_default_force_false(self, tool_ctx):
        """T4: push_to_remote MCP handler defaults to force=False when not supplied."""
        mock_mgr = MagicMock()
        mock_mgr.push_to_remote.return_value = {"success": True, "stderr": ""}
        tool_ctx.clone_mgr = mock_mgr
        await push_to_remote(clone_path="/clone", source_dir="/src", branch="main")
        _args, kwargs = mock_mgr.push_to_remote.call_args
        assert kwargs.get("force") is False


class TestCloneRepoTiming:
    """clone_repo records wall-clock timing when step_name is provided."""

    @pytest.mark.anyio
    async def test_clone_repo_step_name_records_timing(self, tool_ctx):
        mock_mgr = MagicMock()
        mock_mgr.clone_repo.return_value = {"clone_path": "/clone", "source_dir": "/src"}
        tool_ctx.clone_mgr = mock_mgr
        await clone_repo(source_dir="/src", run_name="test", step_name="clone")
        report = tool_ctx.timing_log.get_report()
        assert any(e["step_name"] == "clone" for e in report)

    @pytest.mark.anyio
    async def test_clone_repo_empty_step_name_skips_timing(self, tool_ctx):
        mock_mgr = MagicMock()
        mock_mgr.clone_repo.return_value = {"clone_path": "/clone", "source_dir": "/src"}
        tool_ctx.clone_mgr = mock_mgr
        await clone_repo(source_dir="/src", run_name="test")
        assert tool_ctx.timing_log.get_report() == []


class TestRemoveCloneTiming:
    """remove_clone records wall-clock timing when step_name is provided."""

    @pytest.mark.anyio
    async def test_remove_clone_step_name_records_timing(self, tool_ctx):
        mock_mgr = MagicMock()
        mock_mgr.remove_clone.return_value = {"removed": "true"}
        tool_ctx.clone_mgr = mock_mgr
        await remove_clone(clone_path="/clone", step_name="cleanup")
        report = tool_ctx.timing_log.get_report()
        assert any(e["step_name"] == "cleanup" for e in report)

    @pytest.mark.anyio
    async def test_remove_clone_empty_step_name_skips_timing(self, tool_ctx):
        mock_mgr = MagicMock()
        mock_mgr.remove_clone.return_value = {"removed": "true"}
        tool_ctx.clone_mgr = mock_mgr
        await remove_clone(clone_path="/clone")
        assert tool_ctx.timing_log.get_report() == []


class TestPushToRemoteTiming:
    """push_to_remote records wall-clock timing when step_name is provided."""

    @pytest.mark.anyio
    async def test_push_to_remote_step_name_records_timing(self, tool_ctx):
        mock_mgr = MagicMock()
        mock_mgr.push_to_remote.return_value = {"success": "true", "stderr": ""}
        tool_ctx.clone_mgr = mock_mgr
        await push_to_remote(clone_path="/clone", branch="main", step_name="push")
        report = tool_ctx.timing_log.get_report()
        assert any(e["step_name"] == "push" for e in report)

    @pytest.mark.anyio
    async def test_push_to_remote_empty_step_name_skips_timing(self, tool_ctx):
        mock_mgr = MagicMock()
        mock_mgr.push_to_remote.return_value = {"success": "true", "stderr": ""}
        tool_ctx.clone_mgr = mock_mgr
        await push_to_remote(clone_path="/clone", branch="main")
        assert tool_ctx.timing_log.get_report() == []


class TestRegisterCloneStatusTool:
    @pytest.mark.anyio
    async def test_register_clone_status_success(self, tool_ctx, tmp_path):
        """register_clone_status status='success' writes registry and returns registered=true."""
        tool_ctx.kitchen_id = "kit-test"
        registry_path = str(tmp_path / "registry.json")
        result = json.loads(
            await register_clone_status(
                clone_path="/some/path",
                status="success",
                registry_path=registry_path,
            )
        )
        assert result["registered"] == "true"
        assert "registry_path" in result

    @pytest.mark.anyio
    async def test_register_clone_status_error(self, tool_ctx, tmp_path):
        """register_clone_status status='error' writes registry and returns registered=true."""
        tool_ctx.kitchen_id = "kit-test"
        registry_path = str(tmp_path / "registry.json")
        result = json.loads(
            await register_clone_status(
                clone_path="/some/path",
                status="error",
                registry_path=registry_path,
            )
        )
        assert result["registered"] == "true"
        assert "registry_path" in result

    @pytest.mark.anyio
    async def test_register_clone_status_invalid_status(self, tool_ctx, tmp_path):
        """register_clone_status with status='invalid' returns error without writing."""
        registry_path = str(tmp_path / "registry.json")
        result = json.loads(
            await register_clone_status(
                clone_path="/some/path",
                status="invalid",
                registry_path=registry_path,
            )
        )
        assert "error" in result
        # Registry file must not have been created
        assert not (tmp_path / "registry.json").exists()


class TestBatchCleanupClonesTool:
    @pytest.mark.anyio
    async def test_batch_cleanup_clones_deletes_success_preserves_error(self, tool_ctx, tmp_path):
        """batch_cleanup_clones removes success clones, skips error clones."""
        tool_ctx.kitchen_id = "kit-test"
        registry_path = str(tmp_path / "registry.json")
        success_path = str(tmp_path / "success_clone")
        error_path = str(tmp_path / "error_clone")

        # Pre-populate registry with one success and one error entry
        clone_registry.register_clone(success_path, "success", "kit-test", registry_path)
        clone_registry.register_clone(error_path, "error", "kit-test", registry_path)

        # Mock clone_mgr so remove_clone reports success for the success clone
        mock_mgr = MagicMock()
        mock_mgr.remove_clone.return_value = {"removed": "true"}
        tool_ctx.clone_mgr = mock_mgr

        result = json.loads(await batch_cleanup_clones(registry_path=registry_path))

        assert success_path in result["deleted"]
        assert error_path in result["preserved"]
        assert result["delete_failures"] == []
        mock_mgr.remove_clone.assert_called_once_with(success_path, "false")

    @pytest.mark.anyio
    async def test_batch_cleanup_clones_empty_registry(self, tool_ctx, tmp_path):
        """batch_cleanup_clones with missing registry returns deleted=[], preserved=[]."""
        tool_ctx.kitchen_id = "kit-test"
        registry_path = str(tmp_path / "nonexistent.json")
        result = json.loads(await batch_cleanup_clones(registry_path=registry_path))
        assert result == {"deleted": [], "delete_failures": [], "preserved": []}

    @pytest.mark.anyio
    async def test_batch_cleanup_clones_nonexistent_path_does_not_raise(self, tool_ctx, tmp_path):
        """batch_cleanup_clones reports failure gracefully when a success clone path is gone."""
        tool_ctx.kitchen_id = "kit-test"
        registry_path = str(tmp_path / "registry.json")
        missing_path = str(tmp_path / "gone_clone")

        # Register a success clone whose directory does not exist on disk
        clone_registry.register_clone(missing_path, "success", "kit-test", registry_path)

        # Mock clone_mgr to report removal failure (path not found)
        mock_mgr = MagicMock()
        mock_mgr.remove_clone.return_value = {"removed": "false", "reason": "not found"}
        tool_ctx.clone_mgr = mock_mgr

        result = json.loads(await batch_cleanup_clones(registry_path=registry_path))

        assert result["delete_failures"] != []
        assert result["deleted"] == []
        # Confirm no exception was raised (we received a well-formed result dict)
        assert "error" not in result


# ---------------------------------------------------------------------------
# New tests: T12–T19 (owner-scoping feature for server tools)
# ---------------------------------------------------------------------------


class TestRegisterCloneStatusOwner:
    """T12–T13: register_clone_status propagates kitchen_id as owner."""

    @pytest.mark.anyio
    async def test_register_clone_status_propagates_kitchen_id_as_owner(self, tool_ctx, tmp_path):
        """T12 — register_clone_status writes entry with owner == kitchen_id."""
        tool_ctx.kitchen_id = "kit-xyz"
        reg = str(tmp_path / "registry.json")
        result = json.loads(
            await register_clone_status(clone_path="/c", status="success", registry_path=reg)
        )
        assert result["registered"] == "true"

        data = json.loads(Path(reg).read_text())
        assert len(data["clones"]) == 1
        assert data["clones"][0]["owner"] == "kit-xyz"

    @pytest.mark.anyio
    async def test_register_clone_status_rejects_when_kitchen_id_empty(self, tool_ctx, tmp_path):
        """T13 — register_clone_status returns registered=false when kitchen_id is empty."""
        tool_ctx.kitchen_id = ""
        reg = str(tmp_path / "registry.json")
        result = json.loads(
            await register_clone_status(clone_path="/c", status="success", registry_path=reg)
        )
        assert result["registered"] == "false"
        assert "kitchen_id" in result["reason"] or "kitchen" in result["reason"]
        assert not Path(reg).exists()


class TestBatchCleanupClonesOwner:
    """T14–T19: batch_cleanup_clones owner-scoping and escape hatch."""

    @pytest.mark.anyio
    async def test_batch_cleanup_clones_default_scopes_to_current_kitchen_id(
        self, tool_ctx, tmp_path
    ):
        """T14 — default call only removes current kitchen's clones."""
        reg = str(tmp_path / "registry.json")
        clone_registry.register_clone("/clone-A", "success", "kit-A", reg)
        clone_registry.register_clone("/clone-B", "success", "kit-B", reg)

        tool_ctx.kitchen_id = "kit-A"
        mock_mgr = MagicMock()
        mock_mgr.remove_clone.return_value = {"removed": "true"}
        tool_ctx.clone_mgr = mock_mgr

        result = json.loads(await batch_cleanup_clones(registry_path=reg))

        assert "/clone-A" in result["deleted"]
        assert "/clone-B" not in result["deleted"]
        mock_mgr.remove_clone.assert_called_once_with("/clone-A", "false")

    @pytest.mark.anyio
    async def test_batch_cleanup_clones_all_owners_true_removes_every_success(
        self, tool_ctx, tmp_path
    ):
        """T15 — all_owners='true' escape hatch removes all success entries."""
        reg = str(tmp_path / "registry.json")
        clone_registry.register_clone("/clone-A", "success", "kit-A", reg)
        clone_registry.register_clone("/clone-B", "success", "kit-B", reg)

        tool_ctx.kitchen_id = "kit-A"
        mock_mgr = MagicMock()
        mock_mgr.remove_clone.return_value = {"removed": "true"}
        tool_ctx.clone_mgr = mock_mgr

        result = json.loads(await batch_cleanup_clones(registry_path=reg, all_owners="true"))

        deleted = result["deleted"]
        assert "/clone-A" in deleted
        assert "/clone-B" in deleted

    @pytest.mark.anyio
    async def test_batch_cleanup_clones_empty_kitchen_id_and_all_owners_false_returns_error(
        self, tool_ctx, tmp_path
    ):
        """T16 — empty kitchen_id with default all_owners='false' returns error."""
        tool_ctx.kitchen_id = ""
        reg = str(tmp_path / "registry.json")
        mock_mgr = MagicMock()
        tool_ctx.clone_mgr = mock_mgr

        result = json.loads(await batch_cleanup_clones(registry_path=reg))

        assert "error" in result
        assert "kitchen_id" in result["error"] or "kitchen" in result["error"]
        mock_mgr.remove_clone.assert_not_called()

    @pytest.mark.anyio
    async def test_batch_cleanup_clones_empty_kitchen_id_with_all_owners_true_succeeds(
        self, tool_ctx, tmp_path
    ):
        """T17 — escape hatch works even when kitchen_id is empty (legacy recovery)."""

        reg_path = tmp_path / "registry.json"
        reg_path.write_text(json.dumps({"clones": [{"path": "/legacy", "status": "success"}]}))

        tool_ctx.kitchen_id = ""
        mock_mgr = MagicMock()
        mock_mgr.remove_clone.return_value = {"removed": "true"}
        tool_ctx.clone_mgr = mock_mgr

        result = json.loads(
            await batch_cleanup_clones(registry_path=str(reg_path), all_owners="true")
        )

        assert "/legacy" in result["deleted"]
        mock_mgr.remove_clone.assert_called_once_with("/legacy", "false")

    @pytest.mark.anyio
    async def test_batch_cleanup_clones_invalid_all_owners_literal_treated_as_false(
        self, tool_ctx, tmp_path
    ):
        """T18 — all_owners='yes' is not the escape hatch; scoped behaviour applies."""
        reg = str(tmp_path / "registry.json")
        clone_registry.register_clone("/clone-A", "success", "kit-A", reg)
        clone_registry.register_clone("/clone-B", "success", "kit-B", reg)

        tool_ctx.kitchen_id = "kit-A"
        mock_mgr = MagicMock()
        mock_mgr.remove_clone.return_value = {"removed": "true"}
        tool_ctx.clone_mgr = mock_mgr

        result = json.loads(await batch_cleanup_clones(registry_path=reg, all_owners="yes"))

        assert "/clone-A" in result["deleted"]
        assert "/clone-B" not in result["deleted"]

    @pytest.mark.anyio
    async def test_two_kitchens_register_and_cleanup_isolated(self, tool_ctx, tmp_path):
        """T19 — kitchen A's cleanup does not touch kitchen B's registry entry."""
        reg = str(tmp_path / "registry.json")

        # Register directly via L1 to represent two independent sessions without
        # mutating a shared ToolContext mid-test (a ToolContext represents one session).
        clone_registry.register_clone("/clone-1", "success", "kit-1", reg)
        clone_registry.register_clone("/clone-2", "success", "kit-2", reg)

        # Session 1 cleans up via the MCP tool
        tool_ctx.kitchen_id = "kit-1"
        mock_mgr = MagicMock()
        mock_mgr.remove_clone.return_value = {"removed": "true"}
        tool_ctx.clone_mgr = mock_mgr

        result = json.loads(await batch_cleanup_clones(registry_path=reg))

        # Only kit-1's clone is removed
        assert "/clone-1" in result["deleted"]
        assert "/clone-2" not in result["deleted"]
        mock_mgr.remove_clone.assert_called_once_with("/clone-1", "false")

        # kit-2's entry is still on disk
        data = json.loads(Path(reg).read_text())
        remaining = {e["path"]: e.get("owner") for e in data["clones"]}
        assert "/clone-2" in remaining
        assert remaining["/clone-2"] == "kit-2"
