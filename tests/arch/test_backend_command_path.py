"""AST tests enforcing ctx.backend usage for command construction in headless path."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_HEADLESS_INIT = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "execution"
    / "headless"
    / "__init__.py"
)


def _collect_imports(tree: ast.Module) -> set[str]:
    """Collect all imported names from a module AST."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.names:
                for alias in node.names:
                    names.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
    return names


_HEADLESS_IMPORTS = _collect_imports(ast.parse(_HEADLESS_INIT.read_text()))


@pytest.mark.parametrize(
    "symbol",
    ["build_food_truck_cmd"],
)
def test_headless_does_not_import_commands_module(symbol: str):
    assert symbol not in _HEADLESS_IMPORTS, (
        f"headless/__init__.py must not import {symbol} from commands — "
        f"use ctx.backend.{symbol}() instead"
    )
