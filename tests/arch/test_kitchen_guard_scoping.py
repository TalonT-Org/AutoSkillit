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
        # 2-arg string form: setattr("dotted.path.name", value) → name at args[0]
        # 3-arg form: setattr(target, "name", value) → name at args[1]
        name_arg_idx = 0 if len(node.args) == 2 else 1
        if (
            len(node.args) >= 2
            and isinstance(node.args[name_arg_idx], ast.Constant)
            and "any_kitchen_open" in str(node.args[name_arg_idx].value)
        ):
            return True
    return False


_UPDATE_CHECKS_FILE = _SRC_ROOT / "cli" / "_update_checks.py"


def test_run_update_checks_has_command_parameter() -> None:
    """run_update_checks must declare a 'command' parameter."""
    source = _UPDATE_CHECKS_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_UPDATE_CHECKS_FILE))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_update_checks":
            param_names = [arg.arg for arg in node.args.args + node.args.kwonlyargs]
            assert "command" in param_names, (
                f"run_update_checks must have a 'command' parameter, got: {param_names}"
            )
            return
    raise AssertionError("run_update_checks not found in _update_checks.py")


def _find_function(tree: ast.AST, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _if_body_has_any_kitchen_open_call(if_node: ast.If) -> bool:
    for node in ast.walk(if_node):
        if isinstance(node, ast.Call) and _is_any_kitchen_open_call(node):
            return True
    return False


def _if_test_references_kitchen_guarded_commands(if_node: ast.If) -> bool:
    """Return True if the if-node's test references KITCHEN_GUARDED_COMMANDS."""
    return any(
        isinstance(node, ast.Name) and node.id == "KITCHEN_GUARDED_COMMANDS"
        for node in ast.walk(if_node.test)
    )


def test_kitchen_guard_gated_by_kitchen_guarded_commands() -> None:
    """The any_kitchen_open call in run_update_checks must be inside an if block
    whose test references KITCHEN_GUARDED_COMMANDS."""
    source = _UPDATE_CHECKS_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_UPDATE_CHECKS_FILE))
    func = _find_function(tree, "run_update_checks")
    assert func is not None, "run_update_checks not found in _update_checks.py"

    for node in ast.walk(func):
        if not isinstance(node, ast.If):
            continue
        if not _if_test_references_kitchen_guarded_commands(node):
            continue
        if _if_body_has_any_kitchen_open_call(node):
            return

    raise AssertionError(
        "any_kitchen_open call in run_update_checks is not inside an if block "
        "whose test references KITCHEN_GUARDED_COMMANDS. "
        "The guard must be gated: `if command in KITCHEN_GUARDED_COMMANDS: any_kitchen_open(...)`"
    )


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
