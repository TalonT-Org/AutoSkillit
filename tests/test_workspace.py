"""Tests for autoskillit.workspace module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoskillit.workspace import CleanupResult, _delete_directory_contents
from autoskillit.workspace.clone import (
    clone_repo,
    detect_source_dir,
    merge_feature_branch,
    push_to_remote,
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
# T_DS1–T_DS7: detect_source_dir and clone_repo/push_to_remote additions
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


class TestPushToRemote:
    def test_ds6_push_to_remote_calls_get_url_then_push(self) -> None:
        """T_DS6: push_to_remote calls git remote get-url origin then git push <url> <branch>."""
        mock_url = MagicMock()
        mock_url.returncode = 0
        mock_url.stdout = "git@github.com:org/repo.git\n"
        mock_url.stderr = ""

        mock_push = MagicMock()
        mock_push.returncode = 0
        mock_push.stderr = ""

        with patch("subprocess.run", side_effect=[mock_url, mock_push]) as mock_run:
            result = push_to_remote("/clone", "/source", "main")

        assert result == {"success": "true", "stderr": ""}
        # First call: git remote get-url origin from source_dir
        first_call = mock_run.call_args_list[0]
        assert first_call[0][0] == ["git", "remote", "get-url", "origin"]
        assert first_call[1]["cwd"] == "/source"
        # Second call: git push <url> <branch> from clone_path
        second_call = mock_run.call_args_list[1]
        assert second_call[0][0] == ["git", "push", "git@github.com:org/repo.git", "main"]
        assert second_call[1]["cwd"] == "/clone"

    def test_ds7_push_to_remote_fails_when_no_origin(self) -> None:
        """T_DS7: push_to_remote returns error when git remote get-url fails, no push attempted."""
        mock_fail = MagicMock()
        mock_fail.returncode = 128
        mock_fail.stdout = ""
        mock_fail.stderr = "error: No such remote 'origin'"

        with patch("subprocess.run", return_value=mock_fail) as mock_run:
            result = push_to_remote("/clone", "/source", "main")

        assert result["success"] == "false"
        assert "origin" in result["stderr"]
        assert mock_run.call_count == 1  # no push attempted


# ---------------------------------------------------------------------------
# T_FB1–T_FB6: clone_repo feature branch creation and merge_feature_branch
# ---------------------------------------------------------------------------


class TestCloneRepoFeatureBranch:
    def test_fb1_creates_feature_branch_when_prefix_set(self, tmp_path) -> None:
        """T_FB1: clone_repo with feature_branch_prefix creates and checks out the branch."""
        mock_clone = MagicMock()
        mock_clone.returncode = 0
        mock_clone.stdout = ""
        mock_clone.stderr = ""
        mock_checkout = MagicMock()
        mock_checkout.returncode = 0
        mock_checkout.stdout = ""
        mock_checkout.stderr = ""
        with patch("subprocess.run", side_effect=[mock_clone, mock_checkout]) as mock_run:
            result = clone_repo(str(tmp_path), "run", feature_branch_prefix="impl")
        feature_branch = result["feature_branch"]
        assert feature_branch.startswith("impl-run-")
        checkout_call = mock_run.call_args_list[1]
        assert checkout_call[0][0] == ["git", "checkout", "-b", feature_branch]

    def test_fb2_no_feature_branch_when_prefix_empty(self, tmp_path) -> None:
        """T_FB2: default (empty prefix) does not create a feature branch."""
        mock_clone = MagicMock()
        mock_clone.returncode = 0
        mock_clone.stdout = ""
        mock_clone.stderr = ""
        with patch("subprocess.run", return_value=mock_clone) as mock_run:
            result = clone_repo(str(tmp_path), "run")  # no prefix
        assert result["feature_branch"] == ""
        # Only the git clone call; no checkout -b
        assert mock_run.call_count == 1

    def test_fb3_feature_branch_matches_timestamp_pattern(self, tmp_path) -> None:
        """T_FB3: feature branch name matches pattern impl-{run_name}-YYYYMMDD-HHMMSS-usec."""
        import re

        mock_clone = MagicMock()
        mock_clone.returncode = 0
        mock_clone.stdout = ""
        mock_clone.stderr = ""
        mock_checkout = MagicMock()
        mock_checkout.returncode = 0
        mock_checkout.stdout = ""
        mock_checkout.stderr = ""
        with patch("subprocess.run", side_effect=[mock_clone, mock_checkout]):
            result = clone_repo(str(tmp_path), "myrun", feature_branch_prefix="impl")
        assert re.match(r"^impl-myrun-\d{8}-\d{6}-\d+$", result["feature_branch"])


class TestMergeFeatureBranch:
    def test_fb4_succeeds_when_checkout_and_merge_succeed(self) -> None:
        """T_FB4: merge_feature_branch returns success when both git commands succeed."""
        mock_ok = MagicMock()
        mock_ok.returncode = 0
        mock_ok.stdout = ""
        mock_ok.stderr = ""
        with patch("subprocess.run", return_value=mock_ok):
            result = merge_feature_branch("/clone", "impl-run-123", "main")
        assert result == {"success": "true"}

    def test_fb5_fails_when_checkout_fails(self) -> None:
        """T_FB5: merge_feature_branch returns error when git checkout fails."""
        mock_fail = MagicMock()
        mock_fail.returncode = 1
        mock_fail.stdout = ""
        mock_fail.stderr = "pathspec error"
        with patch("subprocess.run", return_value=mock_fail) as mock_run:
            result = merge_feature_branch("/clone", "impl-run-123", "main")
        assert result["success"] == "false"
        assert "error" in result
        assert mock_run.call_count == 1  # merge never attempted

    def test_fb6_fails_when_merge_fails(self) -> None:
        """T_FB6: merge_feature_branch returns error when git merge fails."""
        mock_ok = MagicMock()
        mock_ok.returncode = 0
        mock_ok.stdout = ""
        mock_ok.stderr = ""
        mock_fail = MagicMock()
        mock_fail.returncode = 1
        mock_fail.stdout = ""
        mock_fail.stderr = "conflict"
        with patch("subprocess.run", side_effect=[mock_ok, mock_fail]):
            result = merge_feature_branch("/clone", "impl-run-123", "main")
        assert result["success"] == "false"
        assert "error" in result
