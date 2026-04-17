"""Enforce pytestmark size markers on in-scope test files."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parent.parent

SIZE_DIRECTORIES: dict[str, str] = {
    "config": "config",
    "core": "core",
    "migration": "migration",
    "pipeline": "pipeline",
}

_VALID_SIZE_MARKERS = {"small", "medium", "large"}


def _extract_size_markers(path: Path) -> list[str]:
    """Parse a test file's AST and return all size marker names found."""
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, OSError) as exc:
        raise type(exc)(f"{path}: {exc}") from exc
    found: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "pytestmark":
                if isinstance(node.value, ast.List):
                    for elt in node.value.elts:
                        name = _marker_name(elt)
                        if name in _VALID_SIZE_MARKERS:
                            found.append(name)
                else:
                    name = _marker_name(node.value)
                    if name and name in _VALID_SIZE_MARKERS:
                        found.append(name)
    return found


def _marker_name(node: ast.expr) -> str | None:
    """If node is pytest.mark.<name>, return <name>."""
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
    ):
        return node.attr
    if isinstance(node, ast.Call):
        return _marker_name(node.func)
    return None


@pytest.mark.parametrize(
    "directory",
    sorted(SIZE_DIRECTORIES),
    ids=sorted(SIZE_DIRECTORIES),
)
def test_all_test_files_have_size_marker(directory: str) -> None:
    """Every test_*.py in an in-scope directory has a size marker."""
    dir_path = TESTS_ROOT / directory
    test_files = sorted(dir_path.glob("test_*.py"))
    assert test_files, f"No test files found in {dir_path}"

    missing: list[str] = []
    for tf in test_files:
        markers = _extract_size_markers(tf)
        if not markers:
            missing.append(tf.name)

    assert not missing, f"Missing size marker (small/medium/large): {missing}"


@pytest.mark.parametrize(
    "directory",
    sorted(SIZE_DIRECTORIES),
    ids=sorted(SIZE_DIRECTORIES),
)
def test_no_conflicting_size_markers(directory: str) -> None:
    """No test file may have more than one size marker."""
    dir_path = TESTS_ROOT / directory
    test_files = sorted(dir_path.glob("test_*.py"))

    conflicts: list[tuple[str, list[str]]] = []
    for tf in test_files:
        markers = _extract_size_markers(tf)
        if len(markers) > 1:
            conflicts.append((tf.name, markers))

    assert not conflicts, f"Conflicting size markers: {conflicts}"


def test_size_markers_registered_in_pyproject() -> None:
    """All three size markers are registered in pyproject.toml."""
    import tomllib

    pyproject = TESTS_ROOT.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    markers = data["tool"]["pytest"]["ini_options"]["markers"]
    marker_names = {m.split(":")[0].strip().split("(")[0] for m in markers}
    for size in ("small", "medium", "large"):
        assert size in marker_names, f"'{size}' marker not registered in pyproject.toml"


def test_size_marker_directories_match_conftest() -> None:
    """SIZE_DIRECTORIES keys must match _SIZE_DIRS in conftest.py."""
    from tests.conftest import _SIZE_DIRS

    assert set(SIZE_DIRECTORIES.keys()) == _SIZE_DIRS, (
        f"SIZE_DIRECTORIES keys {set(SIZE_DIRECTORIES.keys())} != conftest _SIZE_DIRS {_SIZE_DIRS}"
    )
