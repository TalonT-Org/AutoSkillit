"""Tests for autoskillit.workspace module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoskillit.workspace import CleanupResult, _delete_directory_contents
from autoskillit.workspace.clone import (
    clone_repo,
    detect_branch,
    detect_source_dir,
    detect_uncommitted_changes,
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

    def test_cb7_calls_detect_branch_when_branch_empty(self, tmp_path) -> None:
        """T_CB7: detect_branch is called with source_dir when branch=""."""
        with patch(
            "autoskillit.workspace.clone.detect_branch", return_value="main"
        ) as mock_detect:
            with patch("autoskillit.workspace.clone.detect_uncommitted_changes", return_value=[]):
                mock_clone = MagicMock()
                mock_clone.returncode = 0
                with patch("subprocess.run", return_value=mock_clone):
                    try:
                        clone_repo(str(tmp_path), "test-run", branch="")
                    except Exception:
                        pass
        mock_detect.assert_called_once_with(str(tmp_path))

    def test_cb8_skips_detect_branch_when_branch_provided(self, tmp_path) -> None:
        """T_CB8: detect_branch is NOT called when branch is explicitly provided."""
        with patch("autoskillit.workspace.clone.detect_branch") as mock_detect:
            with patch("autoskillit.workspace.clone.detect_uncommitted_changes", return_value=[]):
                mock_clone = MagicMock()
                mock_clone.returncode = 0
                with patch("subprocess.run", return_value=mock_clone):
                    try:
                        clone_repo(str(tmp_path), "test-run", branch="feature")
                    except Exception:
                        pass
        mock_detect.assert_not_called()

    def test_cb9_passes_branch_flag_to_git(self, tmp_path) -> None:
        """T_CB9: --branch and branch name appear in the git clone subprocess call."""
        with patch("autoskillit.workspace.clone.detect_uncommitted_changes", return_value=[]):
            mock_clone = MagicMock()
            mock_clone.returncode = 0
            with patch("subprocess.run", return_value=mock_clone) as mock_run:
                try:
                    clone_repo(str(tmp_path), "test-run", branch="dev")
                except Exception:
                    pass
        git_clone_calls = [
            call for call in mock_run.call_args_list if call.args and "clone" in call.args[0]
        ]
        assert git_clone_calls, "git clone was not called"
        cmd = git_clone_calls[0].args[0]
        assert "--branch" in cmd
        assert "dev" in cmd

    def test_cb10_returns_warning_dict_on_uncommitted_changes(self, tmp_path) -> None:
        """T_CB10: uncommitted changes produce warning dict; git clone not called."""
        with patch("autoskillit.workspace.clone.detect_branch", return_value="main"):
            with patch(
                "autoskillit.workspace.clone.detect_uncommitted_changes",
                return_value=[" M file.py"],
            ):
                with patch("subprocess.run") as mock_run:
                    result = clone_repo(str(tmp_path), "test-run")
        assert result["uncommitted_changes"] == "true"
        mock_run.assert_not_called()


class TestDetectBranch:
    def test_cb11_returns_branch_name_on_success(self) -> None:
        """T_CB11: returns branch name when git rev-parse succeeds."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "main\n"
        with patch("subprocess.run", return_value=mock_result):
            assert detect_branch("/any") == "main"

    def test_cb12_returns_empty_string_on_nonzero_returncode(self) -> None:
        """T_CB12: returns "" when git exits non-zero."""
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            assert detect_branch("/any") == ""

    def test_cb13_returns_head_literal_for_detached_state(self) -> None:
        """T_CB13: returns literal 'HEAD' in detached HEAD state; caller treats as no branch."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "HEAD\n"
        with patch("subprocess.run", return_value=mock_result):
            assert detect_branch("/any") == "HEAD"


class TestDetectUncommittedChanges:
    def test_cb14_returns_empty_list_when_clean(self) -> None:
        """T_CB14: returns [] when working tree is clean."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            assert detect_uncommitted_changes("/any") == []

    def test_cb15_returns_changed_file_lines_when_dirty(self) -> None:
        """T_CB15: returns non-empty status lines when changes exist."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = " M file.py\n?? new.txt\n"
        with patch("subprocess.run", return_value=mock_result):
            assert detect_uncommitted_changes("/any") == [" M file.py", "?? new.txt"]

    def test_cb16_returns_empty_list_on_git_failure(self) -> None:
        """T_CB16: returns [] when git exits non-zero."""
        mock_result = MagicMock()
        mock_result.returncode = 128
        with patch("subprocess.run", return_value=mock_result):
            assert detect_uncommitted_changes("/any") == []


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

    def test_t1_failure_returns_boolean_false(self) -> None:
        """T1: push_to_remote failure must return boolean False, not string 'false'."""
        mock_fail = MagicMock()
        mock_fail.returncode = 1
        mock_fail.stdout = ""
        mock_fail.stderr = "no remote"
        with patch("subprocess.run", return_value=mock_fail):
            result = push_to_remote("/clone", "/source", "main")
        assert result["success"] is False, (
            f"Expected boolean False, got {result['success']!r} ({type(result['success']).__name__})"
        )

    def test_t1_success_returns_boolean_true(self) -> None:
        """T1: push_to_remote success must return boolean True, not string 'true'."""
        mock_url = MagicMock()
        mock_url.returncode = 0
        mock_url.stdout = "git@github.com:org/repo.git\n"
        mock_url.stderr = ""
        mock_push = MagicMock()
        mock_push.returncode = 0
        mock_push.stderr = ""
        with patch("subprocess.run", side_effect=[mock_url, mock_push]):
            result = push_to_remote("/clone", "/source", "main")
        assert result["success"] is True, (
            f"Expected boolean True, got {result['success']!r} ({type(result['success']).__name__})"
        )


@pytest.mark.parametrize(
    "fn,args",
    [
        (push_to_remote, ("/clone", "/source", "main")),
    ],
)
def test_t2_tool_success_field_is_boolean(fn, args) -> None:
    """T2: All workspace functions returning {'success': ...} must use bool, not string."""
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = ""
    mock_proc.stderr = "simulated failure"
    with patch("subprocess.run", return_value=mock_proc):
        result = fn(*args)
    if "success" in result:
        assert isinstance(result["success"], bool), (
            f"{fn.__name__} returned success={result['success']!r}, expected bool"
        )
