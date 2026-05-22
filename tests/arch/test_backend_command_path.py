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


def test_run_headless_core_does_not_import_commands_module():
    source = _HEADLESS_INIT.read_text()
    tree = ast.parse(source)
    imports = _collect_imports(tree)
    assert "build_skill_session_cmd" not in imports, (
        "headless/__init__.py must not import build_skill_session_cmd from commands — "
        "use ctx.backend.build_skill_session_cmd() instead"
    )


def test_dispatch_food_truck_does_not_import_commands_module():
    source = _HEADLESS_INIT.read_text()
    tree = ast.parse(source)
    imports = _collect_imports(tree)
    assert "build_food_truck_cmd" not in imports, (
        "headless/__init__.py must not import build_food_truck_cmd from commands — "
        "use ctx.backend.build_food_truck_cmd() instead"
    )
