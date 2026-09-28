"""Symmetric path-identity helpers of the hook runtime."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.hooks._runtime._path_identity import (
    PathIdentityError,
    hooks_relative_key,
    realpath_within,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


@pytest.fixture
def hooks_tree(tmp_path: Path) -> Path:
    hooks_dir = tmp_path / "real" / "hooks"
    (hooks_dir / "guards").mkdir(parents=True)
    (hooks_dir / "guards" / "x.py").write_text("", encoding="utf-8")
    (tmp_path / "current").symlink_to(tmp_path / "real", target_is_directory=True)
    return hooks_dir


def test_key_through_symlinked_hooks_dir(hooks_tree: Path) -> None:
    linked_hooks = hooks_tree.parents[1] / "current" / "hooks"
    assert hooks_relative_key(hooks_tree / "guards" / "x.py", linked_hooks) == "guards/x.py"


def test_key_for_script_reached_through_symlinked_root(hooks_tree: Path) -> None:
    linked_script = hooks_tree.parents[1] / "current" / "hooks" / "guards" / "x.py"
    assert hooks_relative_key(linked_script, hooks_tree) == "guards/x.py"


def test_key_when_both_sides_are_symlinked(hooks_tree: Path) -> None:
    linked_hooks = hooks_tree.parents[1] / "current" / "hooks"
    assert hooks_relative_key(linked_hooks / "guards" / "x.py", linked_hooks) == "guards/x.py"


def test_escaping_symlink_raises(hooks_tree: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("", encoding="utf-8")
    escape = hooks_tree / "guards" / "escape.py"
    escape.symlink_to(outside)
    with pytest.raises(PathIdentityError):
        hooks_relative_key(escape, hooks_tree)


def test_symlink_loop_raises(hooks_tree: Path) -> None:
    loop = hooks_tree / "guards" / "loop.py"
    loop.symlink_to(loop)
    with pytest.raises(PathIdentityError):
        hooks_relative_key(loop, hooks_tree)


def test_path_identity_error_is_a_value_error() -> None:
    assert issubclass(PathIdentityError, ValueError)


def test_realpath_within_matches_commonpath_containment(tmp_path: Path) -> None:
    root = tmp_path / "a" / "b"
    (root / "child").mkdir(parents=True)
    (tmp_path / "a" / "bc").mkdir()
    (tmp_path / "link").symlink_to(root / "child", target_is_directory=True)
    cases = (
        (root, root, True),
        (root / "child", root, True),
        (tmp_path / "a" / "bc", root, False),
        (tmp_path / "link", root, True),
        (root / "missing" / "file.py", root, True),
    )
    for candidate, container, expected in cases:
        assert realpath_within(candidate, container) is expected, candidate
