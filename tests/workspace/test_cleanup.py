"""L1 unit tests for workspace/cleanup.py — CleanupResult and directory deletion."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit.config import AutomationConfig, ResetWorkspaceConfig
from autoskillit.core.types import SubprocessResult, TerminationReason
from autoskillit.server.tools_workspace import reset_test_dir, reset_workspace
from autoskillit.workspace import CleanupResult, _delete_directory_contents


def _make_result(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    termination_reason: TerminationReason = TerminationReason.NATURAL_EXIT,
    data_confirmed: bool = True,
):
    """Create a SubprocessResult for mocking run_managed_async."""
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        termination=termination_reason,
        pid=12345,
        data_confirmed=data_confirmed,
    )


reset_test_dir.__test__ = False  # type: ignore[attr-defined]


class TestCleanupResult:
    """CleanupResult dataclass contract."""

    def test_success_property_true_when_no_failures(self):
        """1g: success is True iff failed is empty."""
        result = CleanupResult(deleted=["a", "b"], failed=[], skipped=[])
        assert result.success is True

    def test_success_property_false_when_failures(self):
        """1g: success is False when failed is non-empty."""
        result = CleanupResult(deleted=["a"], failed=[("b", "PermissionError: ...")], skipped=[])
        assert result.success is False

    def test_to_dict_structure(self):
        """to_dict returns well-formed dict with all fields."""
        result = CleanupResult(
            deleted=["a"],
            failed=[("b", "PermissionError: denied")],
            skipped=["c"],
        )
        d = result.to_dict()
        assert d["success"] is False
        assert d["deleted"] == ["a"]
        assert d["failed"] == [{"path": "b", "error": "PermissionError: denied"}]
        assert d["skipped"] == ["c"]


class TestDeleteDirectoryContents:
    """_delete_directory_contents never-raise contract."""

    @pytest.fixture(autouse=True)
    def _setup_ctx(self, tool_ctx):
        """Initialize ToolContext for delete_directory_contents tests."""

    def test_continues_after_permission_error(self, tmp_path):
        """1a: PermissionError on one item does not abort the loop."""
        target = tmp_path / "testdir"
        target.mkdir()
        (target / "dir_a").mkdir()
        (target / "locked_dir").mkdir()
        (target / "file_c.txt").touch()

        # Capture real rmtree before patching
        import shutil

        real_rmtree = shutil.rmtree

        def selective_rmtree(path, *args, **kwargs):
            if Path(path).name == "locked_dir":
                raise PermissionError("Permission denied")
            real_rmtree(path, *args, **kwargs)

        with patch("autoskillit.workspace.cleanup.shutil.rmtree", side_effect=selective_rmtree):
            result = _delete_directory_contents(target)

        assert "dir_a" in result.deleted
        assert "file_c.txt" in result.deleted
        assert any(name == "locked_dir" for name, _ in result.failed)
        assert result.success is False

    def test_file_not_found_treated_as_success(self, tmp_path):
        """1b: FileNotFoundError means item is gone = success."""
        target = tmp_path / "testdir"
        target.mkdir()
        (target / "ghost.txt").touch()

        # Delete the file before the cleanup function processes it
        with patch.object(Path, "unlink", side_effect=FileNotFoundError("gone")):
            with patch.object(Path, "is_dir", return_value=False):
                result = _delete_directory_contents(target)

        assert "ghost.txt" in result.deleted
        assert result.failed == []
        assert result.success is True

    def test_preserves_specified_dirs(self, tmp_path):
        """1c: Preserved dirs are skipped, others deleted."""
        target = tmp_path / "testdir"
        target.mkdir()
        (target / ".cache").mkdir()
        (target / "reports").mkdir()
        (target / "output.txt").touch()
        (target / "temp_dir").mkdir()

        result = _delete_directory_contents(target, preserve={".cache", "reports"})

        assert ".cache" in result.skipped
        assert "reports" in result.skipped
        assert "output.txt" in result.deleted
        assert "temp_dir" in result.deleted
        assert (target / ".cache").exists()
        assert (target / "reports").exists()
        assert not (target / "output.txt").exists()
        assert not (target / "temp_dir").exists()

    def test_all_items_deleted_successfully(self, tmp_path):
        """1d: All succeed with no failures."""
        target = tmp_path / "testdir"
        target.mkdir()
        (target / "a").mkdir()
        (target / "b").touch()
        (target / "c").touch()

        result = _delete_directory_contents(target)

        assert result.success is True
        assert result.failed == []
        assert len(result.deleted) == 3

    @pytest.mark.asyncio
    async def test_reset_test_dir_returns_partial_failure_json(self, tool_ctx, tmp_path):
        """1e: reset_test_dir returns structured JSON on partial failure."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / ".autoskillit-workspace").write_text("# marker\n")
        (workspace / "ok_file").touch()

        mock_result = CleanupResult(
            deleted=["ok_file"],
            failed=[("bad_dir", "PermissionError: denied")],
            skipped=[],
        )
        tool_ctx.workspace_mgr = type(
            "MockWM", (), {"delete_contents": lambda self, d, preserve=None: mock_result}
        )()
        result = json.loads(await reset_test_dir(test_dir=str(workspace), force=False))

        assert result["success"] is False
        assert result["failed"] == [{"path": "bad_dir", "error": "PermissionError: denied"}]
        assert "ok_file" in result["deleted"]

    @pytest.mark.asyncio
    async def test_reset_workspace_returns_partial_failure_json(self, tool_ctx, tmp_path):
        """1f: reset_workspace returns structured JSON on partial failure."""
        tool_ctx.config = AutomationConfig(reset_workspace=ResetWorkspaceConfig(command=["true"]))

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        (workspace / ".autoskillit-workspace").write_text("# marker\n")

        tool_ctx.runner.push(_make_result(0, "", ""))

        mock_result = CleanupResult(
            deleted=["ok_file"],
            failed=[("bad_dir", "PermissionError: denied")],
            skipped=[".cache"],
        )
        tool_ctx.workspace_mgr = type(
            "MockWM", (), {"delete_contents": lambda self, d, preserve=None: mock_result}
        )()
        result = json.loads(await reset_workspace(test_dir=str(workspace)))

        assert result["success"] is False
        assert result["failed"] == [{"path": "bad_dir", "error": "PermissionError: denied"}]
