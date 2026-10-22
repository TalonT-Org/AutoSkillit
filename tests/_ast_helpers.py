"""Shared AST helpers for call-site extraction tests across test packages."""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TypeVar

MODULE_SCOPE = "<module>"
_K = TypeVar("_K")


def count_differences(
    expected: Mapping[_K, int], observed: Mapping[_K, int]
) -> list[tuple[_K, int, int]]:
    """Return mismatched registry keys with their expected and observed counts."""
    return [
        (key, expected.get(key, 0), observed.get(key, 0))
        for key in sorted(expected.keys() | observed.keys(), key=str)
        if expected.get(key, 0) != observed.get(key, 0)
    ]


def call_name(node: ast.Call) -> str | None:
    """Return the dotted tail name for one ``ast.Call`` node (Name or Attribute)."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def scope_qualnames(tree: ast.AST) -> dict[ast.AST, str]:
    """Map nodes to their enclosing named-definition AST chain.

    Scopes are ``ClassDef``/``FunctionDef``/``AsyncFunctionDef`` nodes joined with ``."``
    (``Outer.method``); a def or class node maps to its own qualname, and nodes outside
    every def and class map to ``MODULE_SCOPE``. Definition headers inherit that
    definition's key, including decorators, defaults, annotations and class bases.
    This describes AST ancestry, not Python execution scopes. Keys omit runtime
    ``__qualname__``'s ``<locals>`` component: a nested function is ``outer.inner``.
    """
    owners: dict[ast.AST, str] = {}
    stack: list[tuple[ast.AST, str]] = [(tree, MODULE_SCOPE)]
    while stack:
        node, scope = stack.pop()
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = node.name if scope == MODULE_SCOPE else f"{scope}.{node.name}"
        owners[node] = scope
        stack.extend((child, scope) for child in ast.iter_child_nodes(node))
    return owners


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


__all__: Iterable[str] = (
    "MODULE_SCOPE",
    "call_name",
    "callers_by_function",
    "count_callers",
    "count_differences",
    "scope_qualnames",
)
