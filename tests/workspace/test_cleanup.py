"""L1 unit tests for workspace/cleanup.py — CleanupResult and directory deletion."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from autoskillit.workspace import CleanupResult, _delete_directory_contents


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

    def test_default_construction(self):
        r = CleanupResult()
        assert r.success is True
        assert r.deleted == []
        assert r.failed == []
        assert r.skipped == []


class TestDeleteDirectoryContents:
    """_delete_directory_contents never-raise contract."""

    def test_continues_after_permission_error(self, tmp_path):
        """1a: PermissionError on one item does not abort the loop."""
        target = tmp_path / "testdir"
        target.mkdir()
        (target / "dir_a").mkdir()
        (target / "locked_dir").mkdir()
        (target / "file_c.txt").touch()

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

    def test_empty_directory_succeeds(self, tmp_path):
        result = _delete_directory_contents(tmp_path)
        assert result.success
        assert result.deleted == []
