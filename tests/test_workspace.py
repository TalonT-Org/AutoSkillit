"""Tests for autoskillit.workspace module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoskillit.workspace import CleanupResult, _delete_directory_contents
from autoskillit.workspace.clone import (
    clone_repo,
    detect_source_dir,
    push_clone_to_origin,
)


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


# ---------------------------------------------------------------------------
# T_DS1–T_DS7: detect_source_dir and clone_repo/push_clone_to_origin additions
# ---------------------------------------------------------------------------


class TestDetectSourceDir:
    def test_ds1_returns_git_toplevel(self) -> None:
        """T_DS1: returns git rev-parse --show-toplevel when returncode=0."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/repo/root\n"
        with patch("subprocess.run", return_value=mock_result):
            assert detect_source_dir("/any/cwd") == "/repo/root"

    def test_ds2_falls_back_on_nonzero_returncode(self) -> None:
        """T_DS2: returns cwd unchanged when git exits non-zero."""
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            assert detect_source_dir("/any/cwd") == "/any/cwd"


class TestCloneRepoDetectAndExpand:
    def test_ds3_calls_detect_source_dir_when_source_dir_empty(self, tmp_path) -> None:
        """T_DS3: clone_repo calls detect_source_dir when source_dir is empty."""
        with patch(
            "autoskillit.workspace.clone.detect_source_dir", return_value=str(tmp_path)
        ) as mock_detect:
            # Also mock subprocess.run to avoid actually running git clone
            mock_clone = MagicMock()
            mock_clone.returncode = 0
            # Use a fake clone path so clone_repo can complete
            with patch("subprocess.run", return_value=mock_clone):
                # tmp_path is a valid dir; runs_parent.mkdir will work
                try:
                    clone_repo("", "test-run")
                except Exception:
                    pass  # git clone may fail with mock; we only care detect was called
            mock_detect.assert_called_once()

    def test_ds4_expands_tilde(self) -> None:
        """T_DS4: clone_repo raises ValueError with 'resolved to' when tilde path doesn't exist."""
        with pytest.raises(ValueError, match="resolved to"):
            clone_repo("~/nonexistent-autoskillit-test-xyz", "test-run")

    def test_ds5_raises_value_error_with_clear_message(self, tmp_path) -> None:
        """T_DS5: non-existent path raises ValueError with 'resolved to' in message."""
        nonexistent = str(tmp_path / "does-not-exist")
        with pytest.raises(ValueError, match="resolved to"):
            clone_repo(nonexistent, "test-run")


class TestPushCloneToOrigin:
    def test_ds6_calls_git_push_when_push_to_remote_true(self) -> None:
        """T_DS6: push_clone_to_origin calls git push when push_to_remote='true'."""
        mock_success = MagicMock()
        mock_success.returncode = 0
        mock_success.stderr = ""

        with patch("subprocess.run", return_value=mock_success) as mock_run:
            result = push_clone_to_origin("/clone", "/source", "main", push_to_remote="true")

        assert result["success"] == "true"
        assert mock_run.call_count == 2
        second_call_args = mock_run.call_args_list[1][0][0]
        assert second_call_args == ["git", "push", "origin", "main"]

    def test_ds7_skips_git_push_when_push_to_remote_false(self) -> None:
        """T_DS7: push_clone_to_origin does not call git push when push_to_remote='false'."""
        mock_success = MagicMock()
        mock_success.returncode = 0
        mock_success.stderr = ""

        with patch("subprocess.run", return_value=mock_success) as mock_run:
            result = push_clone_to_origin("/clone", "/source", "main", push_to_remote="false")

        assert result["success"] == "true"
        assert mock_run.call_count == 1
