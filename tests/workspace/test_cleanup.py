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

    def test_success_when_no_failures(self):
        r = CleanupResult(deleted=["a", "b"], failed=[], skipped=[])
        assert r.success is True

    def test_failure_when_any_failed(self):
        r = CleanupResult(deleted=[], failed=[("x", "err")], skipped=[])
        assert r.success is False

    def test_to_dict(self):
        r = CleanupResult(deleted=["a"], failed=[("b", "OSError")], skipped=["c"])
        d = r.to_dict()
        assert d["success"] is False
        assert d["deleted"] == ["a"]
        assert d["failed"] == [{"path": "b", "error": "OSError"}]
        assert d["skipped"] == ["c"]

    def test_to_dict_success_case(self):
        r = CleanupResult(deleted=["a", "b"], failed=[], skipped=[])
        d = r.to_dict()
        assert d["success"] is True
        assert d["failed"] == []

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

    def test_deletes_files(self, tmp_path):
        (tmp_path / "file.txt").write_text("x")
        result = _delete_directory_contents(tmp_path)
        assert "file.txt" in result.deleted
        assert result.success

    def test_deletes_subdirectory(self, tmp_path):
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "inner.txt").write_text("y")
        result = _delete_directory_contents(tmp_path)
        assert "subdir" in result.deleted
        assert result.success

    def test_preserves_named_items(self, tmp_path):
        (tmp_path / "keep.txt").write_text("x")
        (tmp_path / "remove.txt").write_text("x")
        result = _delete_directory_contents(tmp_path, preserve={"keep.txt"})
        assert "keep.txt" in result.skipped
        assert "remove.txt" in result.deleted

    def test_preserve_none_deletes_all(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        result = _delete_directory_contents(tmp_path, preserve=None)
        assert len(result.deleted) == 2
        assert result.success

    def test_empty_directory_succeeds(self, tmp_path):
        result = _delete_directory_contents(tmp_path)
        assert result.success
        assert result.deleted == []

    def test_multiple_files_all_deleted(self, tmp_path):
        for i in range(3):
            (tmp_path / f"file{i}.txt").write_text(str(i))
        result = _delete_directory_contents(tmp_path)
        assert len(result.deleted) == 3
        assert result.success
        assert list(tmp_path.iterdir()) == []
