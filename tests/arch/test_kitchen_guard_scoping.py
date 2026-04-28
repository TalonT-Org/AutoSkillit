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


def _function_body_has_any_kitchen_open_patch(func_node: ast.FunctionDef) -> bool:
    """Return True if the function contains monkeypatch.setattr with 'any_kitchen_open'."""
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "setattr"):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and "any_kitchen_open" in str(arg.value):
                return True
    return False


def test_setup_helpers_must_patch_any_kitchen_open() -> None:
    helpers = [
        (_TEST_ROOT / "cli" / "test_update_command.py", "_setup_run_update"),
        (_TEST_ROOT / "cli" / "test_update_checks_prompt.py", "_setup_run_checks"),
    ]
    missing: list[str] = []
    for file_path, func_name in helpers:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                if not _function_body_has_any_kitchen_open_patch(node):
                    missing.append(f"{file_path.name}::{func_name}")
                break
        else:
            missing.append(f"{file_path.name}::{func_name} (function not found)")
    assert not missing, (
        f"Test helpers do not patch any_kitchen_open: {missing}. "
        "Add monkeypatch.setattr('autoskillit.core.any_kitchen_open', lambda **kw: False) "
        "to prevent silent dependency on real $HOME kitchen state."
    )
