"""Tests for LoC capture helpers in execution.headless.

T-GIT-1..T-GIT-4: unit tests for _parse_numstat and _compute_loc_changed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _parse_numstat(numstat_output: str):
    from autoskillit.execution.headless import _parse_numstat

    return _parse_numstat(numstat_output)


def _compute_loc_changed(cwd: str, pre_sha: str):
    from autoskillit.execution.headless import _compute_loc_changed

    return _compute_loc_changed(cwd, pre_sha)


def _capture_git_head_sha(cwd: str):
    from autoskillit.execution.headless import _capture_git_head_sha

    return _capture_git_head_sha(cwd)


# T-GIT-1
def test_compute_loc_changed_parses_numstat_output():
    """_parse_numstat correctly sums numstat lines: insertions, deletions."""
    numstat_output = "10\t5\tfile.py\n3\t1\tsrc/foo.py\n"
    result = _parse_numstat(numstat_output)
    assert result == (13, 6)


# T-GIT-2
def test_compute_loc_changed_returns_zero_on_subprocess_error(tmp_path: Path):
    """When git subprocess fails (non-git dir), loc capture returns (0, 0)."""
    result = _compute_loc_changed(str(tmp_path), "abc1234")
    assert result == (0, 0)


# T-GIT-3
def test_compute_loc_changed_handles_empty_diff():
    """Empty numstat output (no changes) → (0, 0)."""
    assert _parse_numstat("") == (0, 0)


# T-GIT-4
def test_compute_loc_changed_handles_binary_files():
    """Binary file lines (-\t-\tbinary.png) are ignored without error."""
    numstat_output = "-\t-\timage.png\n5\t2\tfile.py\n"
    assert _parse_numstat(numstat_output) == (5, 2)


def test_capture_git_head_sha_returns_empty_on_non_git_dir(tmp_path: Path):
    """_capture_git_head_sha returns '' when directory is not a git repo."""
    result = _capture_git_head_sha(str(tmp_path))
    assert result == ""


def test_capture_git_head_sha_returns_empty_string_on_exception():
    """_capture_git_head_sha returns '' on any subprocess exception."""
    with patch("subprocess.run", side_effect=OSError("mocked error")):
        result = _capture_git_head_sha("/any/path")
    assert result == ""


def test_compute_loc_changed_returns_zero_for_empty_pre_sha(tmp_path: Path):
    """_compute_loc_changed returns (0, 0) when pre_sha is empty."""
    result = _compute_loc_changed(str(tmp_path), "")
    assert result == (0, 0)
