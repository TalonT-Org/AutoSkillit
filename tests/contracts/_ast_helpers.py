"""Shared AST helpers for call-site extraction tests."""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Iterable
from pathlib import Path


def call_name(node: ast.Call) -> str | None:
    """Return the dotted tail name for one ``ast.Call`` node (Name or Attribute)."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def callers_by_function(
    src_root: Path,
    *,
    symbol: str,
    keys: str = "file+function",
) -> Counter[tuple[str, ...]]:
    """Inventory every call to ``symbol`` keyed by ``(relative_path, enclosing_function)``.

    ``keys`` accepts ``"file+function"`` (default) or ``"function"`` to drop the
    file axis from the counter.
    """
    inventory: Counter[tuple[str, ...]] = Counter()
    for path in src_root.rglob("*.py"):
        relative_path = str(path.relative_to(src_root))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative_path)
        parents = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or call_name(node) != symbol:
                continue
            parent = parents.get(node)
            while parent is not None and not isinstance(
                parent, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                parent = parents.get(parent)
            if parent is None:
                continue
            if keys == "file+function":
                inventory[(relative_path, parent.name)] += 1
            elif keys == "function":
                inventory[(parent.name,)] += 1
            else:
                raise ValueError(f"unknown keys spec: {keys!r}")
    return inventory


def count_callers(src_root: Path, symbol: str) -> int:
    """Return the total number of call sites for ``symbol`` across ``src_root``."""
    return sum(callers_by_function(src_root, symbol=symbol).values())


__all__: Iterable[str] = ("call_name", "callers_by_function", "count_callers")
