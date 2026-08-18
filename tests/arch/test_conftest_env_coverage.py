"""Structural guard: root conftest's env-scrubbing fixture must reference the
canonical source-of-truth registry AMBIENT_ENV_DISPOSITIONS programmatically.

The arch test validates the *mechanism* — that _scrub_ambient_env iterates the
named registry — not the individual var names.  It catches regression if
someone replaces the programmatic fixture with hand-written individual fixtures
(the named-registry reference would disappear), and catches import-path drift
if the import is changed without updating the loop (or vice versa).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests._ambient_env_surface import AMBIENT_ENV_DISPOSITIONS

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_CONFTEST_PY = Path(__file__).parent.parent / "conftest.py"
_FIXTURE_NAME = "_scrub_ambient_env"
_REGISTRY_NAME = "AMBIENT_ENV_DISPOSITIONS"


def _find_fixture_func(tree: ast.AST) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == _FIXTURE_NAME:
            return node
    return None


def _imported_names(func: ast.FunctionDef) -> set[str]:
    """Return names imported by any ImportFrom node inside *func*'s body."""
    names: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _for_loop_name_ids(func: ast.FunctionDef) -> set[str]:
    ids: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.For):
            for child in ast.walk(node.iter):
                if isinstance(child, ast.Name):
                    ids.add(child.id)
    return ids


def test_scrub_ambient_env_fixture_exists() -> None:
    """_scrub_ambient_env autouse fixture must be present in tests/conftest.py."""
    tree = ast.parse(_CONFTEST_PY.read_text(), filename=str(_CONFTEST_PY))
    func = _find_fixture_func(tree)
    assert func is not None, (
        f"{_FIXTURE_NAME!r} fixture not found in {_CONFTEST_PY}. "
        "The programmatic env-scrubbing fixture may have been removed or renamed."
    )


def test_scrub_ambient_env_imports_registry_constant() -> None:
    """_scrub_ambient_env must import AMBIENT_ENV_DISPOSITIONS by name."""
    tree = ast.parse(_CONFTEST_PY.read_text(), filename=str(_CONFTEST_PY))
    func = _find_fixture_func(tree)
    assert func is not None, f"{_FIXTURE_NAME!r} not found in conftest"
    imported = _imported_names(func)
    assert _REGISTRY_NAME in imported, (
        f"{_FIXTURE_NAME} does not import {_REGISTRY_NAME!r}. "
        f"The fixture must iterate {_REGISTRY_NAME} programmatically."
    )


def test_scrub_ambient_env_for_loop_references_registry() -> None:
    """_scrub_ambient_env for loop iterator must reference the registry."""
    tree = ast.parse(_CONFTEST_PY.read_text(), filename=str(_CONFTEST_PY))
    func = _find_fixture_func(tree)
    assert func is not None, f"{_FIXTURE_NAME!r} not found in conftest"
    loop_ids = _for_loop_name_ids(func)
    assert _REGISTRY_NAME in loop_ids, (
        f"{_FIXTURE_NAME} for loop does not reference {_REGISTRY_NAME!r}. "
        f"The loop must iterate over {_REGISTRY_NAME}."
    )


def test_scrub_ambient_env_registry_is_nonempty() -> None:
    """AMBIENT_ENV_DISPOSITIONS must be importable from its canonical path and non-empty."""
    assert len(AMBIENT_ENV_DISPOSITIONS) > 0, f"{_REGISTRY_NAME} is empty"
