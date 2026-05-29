"""Structural guard: root conftest's env-clearing fixture must reference the
canonical source-of-truth sets AUTOSKILLIT_PRIVATE_ENV_VARS and
_HEADLESS_EXCLUSIVE_VARS programmatically.

The arch test validates the *mechanism* — that _clear_private_env iterates the
named constant sets — not the individual var names.  It catches regression if
someone replaces the programmatic fixture with hand-written individual fixtures
(the named-set references would disappear), and catches import-path drift if
one import is changed but not the other.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS
from autoskillit.execution.commands import _HEADLESS_EXCLUSIVE_VARS

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_CONFTEST_PY = Path(__file__).parent.parent / "conftest.py"
_FIXTURE_NAME = "_clear_private_env"
_PRIVATE_VARS_NAME = "AUTOSKILLIT_PRIVATE_ENV_VARS"
_EXCLUSIVE_VARS_NAME = "_HEADLESS_EXCLUSIVE_VARS"


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


def test_clear_private_env_fixture_exists() -> None:
    """_clear_private_env autouse fixture must be present in tests/conftest.py."""
    tree = ast.parse(_CONFTEST_PY.read_text(), filename=str(_CONFTEST_PY))
    func = _find_fixture_func(tree)
    assert func is not None, (
        f"{_FIXTURE_NAME!r} fixture not found in {_CONFTEST_PY}. "
        "The programmatic env-clearing fixture may have been removed or renamed."
    )


def test_clear_private_env_imports_private_vars_constant() -> None:
    """_clear_private_env must import AUTOSKILLIT_PRIVATE_ENV_VARS by name."""
    tree = ast.parse(_CONFTEST_PY.read_text(), filename=str(_CONFTEST_PY))
    func = _find_fixture_func(tree)
    assert func is not None, f"{_FIXTURE_NAME!r} not found in conftest"
    imported = _imported_names(func)
    assert _PRIVATE_VARS_NAME in imported, (
        f"{_FIXTURE_NAME} does not import {_PRIVATE_VARS_NAME!r}. "
        "The fixture must iterate AUTOSKILLIT_PRIVATE_ENV_VARS programmatically."
    )


def test_clear_private_env_imports_headless_exclusive_vars_constant() -> None:
    """_clear_private_env must import _HEADLESS_EXCLUSIVE_VARS by name."""
    tree = ast.parse(_CONFTEST_PY.read_text(), filename=str(_CONFTEST_PY))
    func = _find_fixture_func(tree)
    assert func is not None, f"{_FIXTURE_NAME!r} not found in conftest"
    imported = _imported_names(func)
    assert _EXCLUSIVE_VARS_NAME in imported, (
        f"{_FIXTURE_NAME} does not import {_EXCLUSIVE_VARS_NAME!r}. "
        "The fixture must iterate _HEADLESS_EXCLUSIVE_VARS programmatically."
    )


def test_clear_private_env_for_loop_references_both_sets() -> None:
    """_clear_private_env for loop iterator must reference both named sets."""
    tree = ast.parse(_CONFTEST_PY.read_text(), filename=str(_CONFTEST_PY))
    func = _find_fixture_func(tree)
    assert func is not None, f"{_FIXTURE_NAME!r} not found in conftest"
    loop_ids = _for_loop_name_ids(func)
    assert _PRIVATE_VARS_NAME in loop_ids, (
        f"{_FIXTURE_NAME} for loop does not reference {_PRIVATE_VARS_NAME!r}. "
        "The loop must iterate over the union of both constant sets."
    )
    assert _EXCLUSIVE_VARS_NAME in loop_ids, (
        f"{_FIXTURE_NAME} for loop does not reference {_EXCLUSIVE_VARS_NAME!r}. "
        "The loop must iterate over the union of both constant sets."
    )


def test_coverage_parity_private_env_vars() -> None:
    """Both env-var sets are non-empty and importable from their canonical paths."""
    assert AUTOSKILLIT_PRIVATE_ENV_VARS, "AUTOSKILLIT_PRIVATE_ENV_VARS is empty"
    assert _HEADLESS_EXCLUSIVE_VARS, "_HEADLESS_EXCLUSIVE_VARS is empty"
