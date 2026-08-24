"""Tests for the enumeration-derived path observation funnel."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from autoskillit.core.fs_observation import observe_path_mode, safe_mtime

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_observe_path_mode_returns_st_mode_for_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "present.txt"
    target.write_text("hello")
    mode = observe_path_mode(target)
    assert mode is not None
    assert stat.S_ISREG(mode)


def test_observe_path_mode_returns_none_for_path_unlinked_before_call(tmp_path: Path) -> None:
    target = tmp_path / "vanished.txt"
    target.write_text("hello")
    target.unlink()
    assert observe_path_mode(target) is None


def test_observe_path_mode_returns_none_when_intermediate_component_becomes_a_file(
    tmp_path: Path,
) -> None:
    """A directory replaced by a regular file mid-walk raises NotADirectoryError,
    never FileNotFoundError — this arm must be pinned explicitly, not merely caught.
    """
    parent = tmp_path / "was_a_dir"
    parent.mkdir()
    candidate = parent / "child.txt"
    candidate.write_text("hello")
    parent_children = list(parent.iterdir())
    assert parent_children == [candidate]

    # Replace the intermediate directory with a regular file, then observe the
    # path that used to be nested inside it.
    import shutil

    shutil.rmtree(parent)
    parent.write_text("no longer a directory")

    with pytest.raises(NotADirectoryError):
        candidate.lstat()
    assert observe_path_mode(candidate) is None


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0,
    reason="requires POSIX permission enforcement as non-root",
)
def test_observe_path_mode_propagates_permission_error(tmp_path: Path) -> None:
    parent = tmp_path / "locked"
    parent.mkdir()
    candidate = parent / "child.txt"
    candidate.write_text("hello")
    os.chmod(parent, 0)
    try:
        with pytest.raises(PermissionError):
            observe_path_mode(candidate)
    finally:
        os.chmod(parent, 0o700)


def test_safe_mtime_returns_mtime_for_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "present.txt"
    target.write_text("hello")
    mtime = safe_mtime(target)
    assert mtime is not None
    assert mtime == pytest.approx(target.stat().st_mtime)


def test_safe_mtime_returns_none_for_path_unlinked_before_call(tmp_path: Path) -> None:
    target = tmp_path / "vanished.txt"
    target.write_text("hello")
    target.unlink()
    assert safe_mtime(target) is None


def test_safe_mtime_returns_none_when_intermediate_component_becomes_a_file(
    tmp_path: Path,
) -> None:
    import shutil

    parent = tmp_path / "was_a_dir"
    parent.mkdir()
    candidate = parent / "child.txt"
    candidate.write_text("hello")
    shutil.rmtree(parent)
    parent.write_text("no longer a directory")

    with pytest.raises(NotADirectoryError):
        os.path.getmtime(candidate)
    assert safe_mtime(candidate) is None
