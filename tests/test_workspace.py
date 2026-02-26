"""Tests for autoskillit.workspace module."""

from __future__ import annotations

from pathlib import Path

from autoskillit.workspace import CleanupResult, _delete_directory_contents


class TestWorkspaceModuleExists:
    def test_cleanup_result_importable(self):
        assert CleanupResult is not None

    def test_delete_directory_contents_importable(self):
        assert callable(_delete_directory_contents)


class TestCleanupResult:
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

    def test_file_not_found_treated_as_success(self, tmp_path, monkeypatch):
        (tmp_path / "ghost.txt").write_text("x")

        def raise_fnf(self, *args, **kwargs):
            raise FileNotFoundError("already gone")

        monkeypatch.setattr(Path, "unlink", raise_fnf)
        result = _delete_directory_contents(tmp_path)
        assert "ghost.txt" in result.deleted
        assert result.success
