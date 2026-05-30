"""AST guard: interactive launch sites must call assert_interactive_ordering.

Ensures that _session_launch.py and _cook.py both call
assert_interactive_ordering after build_interactive_cmd and before
the subprocess invocation, preventing silent ordering regressions.
"""

from __future__ import annotations

import ast

import pytest

from autoskillit.core import paths

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_GATE_NAME = "assert_interactive_ordering"


def _has_call(tree: ast.AST, name: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == name:
                return True
            if isinstance(func, ast.Attribute) and func.attr == name:
                return True
    return False


def _session_launch_source() -> str:
    return (paths.pkg_root() / "cli" / "session" / "_session_launch.py").read_text()


def _cook_source() -> str:
    return (paths.pkg_root() / "cli" / "session" / "_cook.py").read_text()


def test_session_launch_calls_assert_interactive_ordering():
    source = _session_launch_source()
    tree = ast.parse(source)
    assert _has_call(tree, _GATE_NAME), (
        f"_session_launch.py does not call {_GATE_NAME}(). "
        "Interactive launch sites must validate CmdSpec ordering before "
        "passing to subprocess."
    )


def test_cook_calls_assert_interactive_ordering():
    source = _cook_source()
    tree = ast.parse(source)
    assert _has_call(tree, _GATE_NAME), (
        f"_cook.py does not call {_GATE_NAME}(). "
        "Interactive launch sites must validate CmdSpec ordering before "
        "passing to subprocess."
    )


def test_session_launch_imports_assert_interactive_ordering():
    source = _session_launch_source()
    tree = ast.parse(source)
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.asname or alias.name)
    assert _GATE_NAME in imported_names, (
        f"_session_launch.py does not import {_GATE_NAME}. "
        "It must be imported and called before subprocess invocation."
    )


def test_cook_imports_assert_interactive_ordering():
    source = _cook_source()
    tree = ast.parse(source)
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.asname or alias.name)
    assert _GATE_NAME in imported_names, (
        f"_cook.py does not import {_GATE_NAME}. "
        "It must be imported and called before subprocess invocation."
    )
