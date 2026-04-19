"""Enforce pytestmark layer markers on all in-scope test files."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parent.parent

LAYER_DIRECTORIES: dict[str, str] = {
    "core": "core",
    "config": "config",
    "pipeline": "pipeline",
    "execution": "execution",
    "workspace": "workspace",
    "recipe": "recipe",
    "migration": "migration",
    "franchise": "franchise",
    "server": "server",
    "cli": "cli",
}


def _extract_layer_marker(path: Path) -> str | None:
    """Parse a test file's AST and return the layer marker value, or None."""
    tree = ast.parse(path.read_text())
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "pytestmark":
                # pytestmark = [pytest.mark.layer("x"), ...]
                if isinstance(node.value, ast.List):
                    for elt in node.value.elts:
                        val = _marker_layer_arg(elt)
                        if val is not None:
                            return val
                # pytestmark = pytest.mark.layer("x")  (bare, no list)
                val = _marker_layer_arg(node.value)
                if val is not None:
                    return val
    return None


def _marker_layer_arg(node: ast.expr) -> str | None:
    """If node is pytest.mark.layer("x"), return "x"."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    # pytest.mark.layer(...)
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "layer"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "mark"
    ):
        if node.args and isinstance(node.args[0], ast.Constant):
            return node.args[0].value
    return None


@pytest.mark.parametrize(
    "directory",
    sorted(LAYER_DIRECTORIES),
    ids=sorted(LAYER_DIRECTORIES),
)
def test_all_test_files_have_correct_layer_marker(directory: str) -> None:
    """Every test_*.py in an in-scope directory has pytestmark with correct layer."""
    expected = LAYER_DIRECTORIES[directory]
    dir_path = TESTS_ROOT / directory
    test_files = sorted(dir_path.glob("test_*.py"))
    assert test_files, f"No test files found in {dir_path}"

    missing: list[str] = []
    wrong: list[tuple[str, str]] = []

    for tf in test_files:
        layer = _extract_layer_marker(tf)
        if layer is None:
            missing.append(tf.name)
        elif layer != expected:
            wrong.append((tf.name, layer))

    errors: list[str] = []
    if missing:
        errors.append(f"Missing layer marker: {missing}")
    if wrong:
        errors.append(f"Wrong layer marker: {wrong}")
    assert not errors, "\n".join(errors)


def test_layer_directories_matches_conftest() -> None:
    """LAYER_DIRECTORIES keys must match _LAYER_DIRS in conftest.py."""
    from tests.conftest import _LAYER_DIRS

    assert set(LAYER_DIRECTORIES.keys()) == _LAYER_DIRS, (
        f"LAYER_DIRECTORIES keys {set(LAYER_DIRECTORIES.keys())} != "
        f"conftest _LAYER_DIRS {_LAYER_DIRS}"
    )


def test_layer_marker_registered_in_pyproject() -> None:
    """The 'layer' marker is registered in pyproject.toml to avoid warnings."""
    import tomllib

    pyproject = TESTS_ROOT.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    markers = data["tool"]["pytest"]["ini_options"]["markers"]
    assert any(m.startswith("layer") for m in markers), (
        "layer marker not registered in pyproject.toml [tool.pytest.ini_options].markers"
    )
