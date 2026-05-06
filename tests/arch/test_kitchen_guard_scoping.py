"""Architectural enforcement: any_kitchen_open call-site scoping and test helper isolation."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch")]

_SRC_ROOT = Path(__file__).parents[2] / "src" / "autoskillit"
_TEST_ROOT = Path(__file__).parents[2] / "tests"

# File that defines any_kitchen_open — not a call site.
_DEFINITION_FILE = "core/_plugin_cache.py"


def _has_project_path_kwarg(call_node: ast.Call) -> bool:
    return any(kw.arg == "project_path" for kw in call_node.keywords)


def _is_any_kitchen_open_call(call_node: ast.Call) -> bool:
    func = call_node.func
    if isinstance(func, ast.Name):
        return func.id == "any_kitchen_open"
    if isinstance(func, ast.Attribute):
        return func.attr == "any_kitchen_open"
    return False


def test_any_kitchen_open_callers_pass_project_path() -> None:
    """All production callers of any_kitchen_open must pass project_path= to scope the guard."""
    violations: list[str] = []
    for py_file in sorted(_SRC_ROOT.rglob("*.py")):
        rel = py_file.relative_to(_SRC_ROOT)
        if str(rel) == _DEFINITION_FILE:
            continue
        source = py_file.read_text(encoding="utf-8")
        if "any_kitchen_open" not in source:
            continue
        tree = ast.parse(source, filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_any_kitchen_open_call(node):
                if not _has_project_path_kwarg(node):
                    violations.append(f"{rel}:{node.lineno}")
    assert not violations, (
        f"any_kitchen_open called without project_path= at: {violations}. "
        "All production callers must pass project_path to scope the guard to the current project."
    )
