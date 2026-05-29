"""Structural guard: make_context() and helpers in _factory.py must not read
AUTOSKILLIT_PRIVATE_ENV_VARS members from os.environ.

These variables are session-scoped internals.  Reading them inside the
composition root promotes them to server-scoped state — a class of
contamination bug that this AST test makes structurally impossible.

The test operates purely on the AST (no runtime execution) so it catches
any future attempt before it can merge.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoskillit.core.types._type_constants_env import AUTOSKILLIT_PRIVATE_ENV_VARS

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_FACTORY_PY = (
    Path(__file__).parent.parent.parent / "src" / "autoskillit" / "server" / "_factory.py"
)


def _extract_private_env_reads(factory_path: Path) -> list[tuple[str, str, int]]:
    """Return list of (function_name, env_var_name, lineno) for private env reads."""
    tree = ast.parse(factory_path.read_text())
    violations: list[tuple[str, str, int]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        fn_name = node.name
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            env_var_name: str | None = None

            if (
                isinstance(child.func, ast.Attribute)
                and child.func.attr == "get"
                and isinstance(child.func.value, ast.Attribute)
                and child.func.value.attr == "environ"
                and isinstance(child.func.value.value, ast.Name)
                and child.func.value.value.id == "os"
                and child.args
                and isinstance(child.args[0], ast.Constant)
                and isinstance(child.args[0].value, str)
            ):
                env_var_name = child.args[0].value

            elif (
                isinstance(child.func, ast.Attribute)
                and child.func.attr == "getenv"
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "os"
                and child.args
                and isinstance(child.args[0], ast.Constant)
                and isinstance(child.args[0].value, str)
            ):
                env_var_name = child.args[0].value

            if env_var_name and env_var_name in AUTOSKILLIT_PRIVATE_ENV_VARS:
                violations.append((fn_name, env_var_name, child.lineno))

    # Subscript (os.environ["KEY"]) is a distinct AST node from Call — needs separate walk

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        fn_name = node.name
        for child in ast.walk(node):
            if not isinstance(child, ast.Subscript):
                continue
            if (
                isinstance(child.value, ast.Attribute)
                and child.value.attr == "environ"
                and isinstance(child.value.value, ast.Name)
                and child.value.value.id == "os"
                and isinstance(child.slice, ast.Constant)
                and isinstance(child.slice.value, str)
                and child.slice.value in AUTOSKILLIT_PRIVATE_ENV_VARS
            ):
                violations.append((fn_name, child.slice.value, child.lineno))

    return violations


def test_factory_functions_do_not_read_private_env_vars() -> None:
    """No function in _factory.py may read a member of AUTOSKILLIT_PRIVATE_ENV_VARS.

    These variables are session-scoped.  Reading them inside make_context() or
    any helper it calls in the same module promotes them to server-scoped state,
    creating permanent contamination that scrubbing mechanisms cannot undo.

    If a legitimate future use case requires reading a private var here, move
    the read to the CLI entry point (cli/app.py) and pass the value explicitly
    as a parameter to make_context().
    """
    violations = _extract_private_env_reads(_FACTORY_PY)
    assert violations == [], (
        "_factory.py reads AUTOSKILLIT_PRIVATE_ENV_VARS members from os.environ:\n"
        + "\n".join(f"  {fn}(): reads {var!r} (line {lineno})" for fn, var, lineno in violations)
        + "\n\nMove env reads to cli/app.py and pass values explicitly to make_context()."
    )
