"""Symmetric path-identity helpers of the hook runtime."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import destination_location as core_destination_location
from autoskillit.hooks._runtime._path_identity import (
    PathIdentityError,
    destination_location,
    destination_within,
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


def test_destination_location_resolves_parent_but_not_final_component(tmp_path: Path) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    assert destination_location(linked_parent / "entry") == real_parent / "entry"


def test_destination_location_preserves_dangling_final_symlink(tmp_path: Path) -> None:
    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "missing")

    assert destination_location(dangling) == dangling


def test_destination_location_handles_root_destination() -> None:
    assert destination_location(Path("/")) == Path("/")


@pytest.mark.parametrize("name", ["entry", "final-link", "..", "."])
def test_destination_location_core_and_hook_conformance(tmp_path: Path, name: str) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    (real_parent / "final-link").symlink_to(tmp_path / "missing")
    destination = linked_parent / name
    expected = tmp_path if name == ".." else linked_parent if name == "." else real_parent / name

    for resolve_location in (destination_location, core_destination_location):
        assert resolve_location(destination) == expected


def test_destination_within_uses_physical_parent_and_preserves_final_link(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (allowed / "parent").symlink_to(outside, target_is_directory=True)
    (allowed / "link").symlink_to(outside / "target")
    assert destination_within(allowed / "link", allowed)
    assert not destination_within(allowed / "parent" / "entry", allowed)
    assert not destination_within(allowed / "..", allowed)
    assert not destination_within(tmp_path / "allowed-neighbor" / "entry", allowed)
