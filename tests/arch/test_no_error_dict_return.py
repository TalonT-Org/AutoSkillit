"""AST guard: load_and_validate must never return a dict containing an 'error' key.

Errors flow via exceptions (ProcessStaleError, RecipeNotFoundError).
Returning {"error": ...} re-introduces the silent-drop bug that callers
cannot reliably detect at the type level.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

SRC = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
RECIPE_API = SRC / "recipe" / "_api.py"


def _find_function_node(
    tree: ast.Module, name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _has_error_key_in_dict(node: ast.Dict) -> bool:
    for key in node.keys:
        if isinstance(key, ast.Constant) and key.value == "error":
            return True
    return False


def test_load_and_validate_has_no_error_dict_return():
    """No return statement in load_and_validate may contain a dict literal with 'error' key."""
    tree = ast.parse(RECIPE_API.read_text())
    func = _find_function_node(tree, "load_and_validate")
    assert func is not None, "load_and_validate function not found in recipe/_api.py"

    violations: list[int] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Return) and node.value is not None:
            for child in ast.walk(node.value):
                if isinstance(child, ast.Dict) and _has_error_key_in_dict(child):
                    violations.append(node.lineno)

    assert violations == [], (
        f"load_and_validate has return statements with 'error' dict keys at lines {violations}. "
        "Use ProcessStaleError or RecipeNotFoundError exceptions instead."
    )
