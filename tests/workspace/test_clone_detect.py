"""Tests for detect_* helpers and classify_remote_url from autoskillit.workspace._clone_detect (re-exported via autoskillit.workspace.clone)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoskillit.workspace.clone import (
    classify_remote_url,
    detect_branch,
    detect_source_dir,
    detect_uncommitted_changes,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.medium]


class TestDetectSourceDir:
    def test_ds1_returns_git_toplevel(self) -> None:
        """T_DS1: returns git rev-parse --show-toplevel when returncode=0."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/repo/root\n"
        with patch("autoskillit.workspace._clone_detect.subprocess.run", return_value=mock_result):
            assert detect_source_dir("/any/cwd") == "/repo/root"

    def test_ds2_falls_back_on_nonzero_returncode(self) -> None:
        """T_DS2: returns cwd unchanged when git exits non-zero."""
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stdout = ""
        with patch("autoskillit.workspace._clone_detect.subprocess.run", return_value=mock_result):
            assert detect_source_dir("/any/cwd") == "/any/cwd"


class TestDetectBranch:
    def test_cb11_returns_branch_name_on_success(self) -> None:
        """T_CB11: returns branch name when git rev-parse succeeds."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "main\n"
        with patch("autoskillit.workspace._clone_detect.subprocess.run", return_value=mock_result):
            assert detect_branch("/any") == "main"

    def test_cb12_returns_empty_string_on_nonzero_returncode(self) -> None:
        """T_CB12: returns "" when git exits non-zero."""
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stdout = ""
        with patch("autoskillit.workspace._clone_detect.subprocess.run", return_value=mock_result):
            assert detect_branch("/any") == ""

    def test_cb13_returns_head_literal_for_detached_state(self) -> None:
        """T_CB13: returns literal 'HEAD' in detached HEAD state; caller treats as no branch."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "HEAD\n"
        with patch("autoskillit.workspace._clone_detect.subprocess.run", return_value=mock_result):
            assert detect_branch("/any") == "HEAD"


class TestDetectUncommittedChanges:
    def test_cb14_returns_empty_list_when_clean(self) -> None:
        """T_CB14: returns [] when working tree is clean."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        with patch("autoskillit.workspace._clone_detect.subprocess.run", return_value=mock_result):
            assert detect_uncommitted_changes("/any") == []

    def test_cb15_returns_changed_file_lines_when_dirty(self) -> None:
        """T_CB15: returns non-empty status lines when changes exist."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = " M file.py\n?? new.txt\n"
        with patch("autoskillit.workspace._clone_detect.subprocess.run", return_value=mock_result):
            assert detect_uncommitted_changes("/any") == [" M file.py", "?? new.txt"]

    def test_cb16_returns_empty_list_on_git_failure(self) -> None:
        """T_CB16: returns [] when git exits non-zero."""
        mock_result = MagicMock()
        mock_result.returncode = 128
        with patch("autoskillit.workspace._clone_detect.subprocess.run", return_value=mock_result):
            assert detect_uncommitted_changes("/any") == []


class TestClassifyRemoteUrl:
    """classify_remote_url correctly identifies remote URL types."""

    @pytest.mark.parametrize(
        "url,expected_type",
        [
            ("git@github.com:org/repo.git", "network"),
            ("https://github.com/org/repo.git", "network"),
            ("ssh://git@github.com/org/repo.git", "network"),
            ("http://example.com/repo.git", "network"),
            ("git://github.com/org/repo.git", "network"),
            ("file:///tmp/repo.git", "network"),
        ],
    )
    def test_network_urls(self, url: str, expected_type: str) -> None:
        result = classify_remote_url(url)
        assert result == expected_type

    def test_bare_local_path(self, tmp_path: Path) -> None:
        bare = tmp_path / "repo.git"
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
        result = classify_remote_url(str(bare))
        assert result == "bare_local"

    def test_nonbare_local_path(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        result = classify_remote_url(str(repo))
        assert result == "nonbare_local"

    def test_empty_string_returns_none_type(self) -> None:
        result = classify_remote_url("")
        assert result == "none"

    def test_nonexistent_path_returns_unknown(self, tmp_path: Path) -> None:
        result = classify_remote_url(str(tmp_path / "nonexistent"))
        assert result == "unknown"
