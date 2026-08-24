#!/usr/bin/env python3
"""Reject ambient ``Path.home()`` reads at injected-home boundaries.

The registered modules either accept a managed home or sit on a path that must
not independently re-resolve it.  A raw ambient read is permitted only at the
small number of process-boundary entry points listed below.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Mapping
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "autoskillit"

# Module-relative path -> functions that are allowed to resolve the process home.
# Keep this registry deliberately narrow: it protects only modules where an
# injected-root/ambient-root mismatch can redirect AutoSkillit's own state.
AMBIENT_HOME_MODULES: Mapping[str, frozenset[str]] = {
    "core/_active_kitchens.py": frozenset(),
    "cli/install/_plugin_artifact.py": frozenset(),
    "workspace/_install_state.py": frozenset({"_home"}),
    "workspace/_projected_artifact/authority.py": frozenset(),
    "workspace/_projected_artifact/_generation_publication.py": frozenset(),
    "workspace/_projected_artifact/_hook_repair.py": frozenset({"repair_broken_projection_hooks"}),
}


def _build_parent_map(tree: ast.Module) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _enclosing_function(node: ast.AST, parents: Mapping[ast.AST, ast.AST]) -> str | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
        current = parents.get(current)
    return None


def _is_path_home_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "home"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "Path"
    )


def find_ambient_home_violations(src_root: Path = SRC_ROOT) -> list[str]:
    """Return raw ``Path.home()`` calls outside the per-module allowlists."""
    violations: list[str] = []
    for module, allowed_functions in AMBIENT_HOME_MODULES.items():
        path = src_root / module
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = _build_parent_map(tree)
        for node in ast.walk(tree):
            if not _is_path_home_call(node):
                continue
            function_name = _enclosing_function(node, parents)
            if function_name in allowed_functions:
                continue
            location = function_name or "<module>"
            violations.append(
                f"{module}:{node.lineno}: Path.home() in {location}; "
                "pass the managed home explicitly"
            )
    return sorted(violations)


def find_missing_registered_modules(src_root: Path = SRC_ROOT) -> list[str]:
    """Return registered modules that do not exist under *src_root*."""
    return [
        f"{module}: registered ambient-home boundary module does not exist"
        for module in AMBIENT_HOME_MODULES
        if not (src_root / module).is_file()
    ]


def check(src_root: Path = SRC_ROOT) -> list[str]:
    """Return every ambient-home boundary violation under *src_root*."""
    return [
        *find_missing_registered_modules(src_root),
        *find_ambient_home_violations(src_root),
    ]


def main() -> int:
    violations = check()
    if violations:
        print("Ambient home boundary violations found:\n")
        for violation in violations:
            print(f"  {violation}")
        return 1
    print("Registered injected-home boundaries do not re-resolve ambient home.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
