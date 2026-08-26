"""Arch guard: compacted-frame measurements have one ledger-view owner."""

from __future__ import annotations

import ast
from collections.abc import Iterable

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_CAPACITY_FUNCTIONS = frozenset({"admission_reason", "transition_reason"})


def _capacity_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    function_aliases = set(_CAPACITY_FUNCTIONS)
    sizer_aliases = {"CompactedFrameSizer"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for imported in node.names:
                if imported.name in _CAPACITY_FUNCTIONS:
                    function_aliases.add(imported.asname or imported.name)
                if imported.name == "CompactedFrameSizer":
                    sizer_aliases.add(imported.asname or imported.name)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Attribute):
            if node.value.attr not in {*_CAPACITY_FUNCTIONS, "CompactedFrameSizer"}:
                continue
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                if node.value.attr == "CompactedFrameSizer":
                    sizer_aliases.add(target.id)
                else:
                    function_aliases.add(target.id)
    return function_aliases, sizer_aliases


def _source_trees() -> Iterable[tuple[str, ast.Module]]:
    from autoskillit.core import pkg_root

    source_root = pkg_root()
    for path in sorted(source_root.rglob("*.py")):
        yield path.relative_to(source_root).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def _is_capacity_call(node: ast.Call, function_aliases: set[str]) -> bool:
    if isinstance(node.func, ast.Name):
        return node.func.id in function_aliases
    return isinstance(node.func, ast.Attribute) and node.func.attr in _CAPACITY_FUNCTIONS


def test_capacity_reason_calls_reuse_a_ledger_view_sizer() -> None:
    calls: list[tuple[str, ast.Call]] = []
    for relpath, tree in _source_trees():
        function_aliases, _sizer_aliases = _capacity_aliases(tree)
        calls.extend(
            (relpath, node)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and _is_capacity_call(node, function_aliases)
        )

    assert calls
    for relpath, call in calls:
        keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}
        sizer = keywords.get("sizer")
        assert isinstance(sizer, ast.Attribute), (
            f"{relpath}:{call.lineno} must pass a ledger-view sizer attribute; "
            "fresh sizers and literal caches are not valid owners"
        )


def test_compacted_frame_sizer_is_constructed_only_by_ledger_view() -> None:
    constructions: list[tuple[str, ast.Call, bool]] = []
    for relpath, tree in _source_trees():
        _function_aliases, sizer_aliases = _capacity_aliases(tree)
        ledger_view_init_calls = {
            id(call)
            for class_node in ast.walk(tree)
            if isinstance(class_node, ast.ClassDef) and class_node.name == "LedgerView"
            for method in class_node.body
            if isinstance(method, ast.FunctionDef) and method.name == "__init__"
            for call in ast.walk(method)
            if isinstance(call, ast.Call)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            is_constructor = (
                isinstance(node.func, ast.Name) and node.func.id in sizer_aliases
            ) or (isinstance(node.func, ast.Attribute) and node.func.attr == "CompactedFrameSizer")
            if is_constructor:
                constructions.append((relpath, node, id(node) in ledger_view_init_calls))

    assert [
        (relpath, in_ledger_view_init) for relpath, _call, in_ledger_view_init in constructions
    ] == [("hooks/_capture/_ledger_view.py", True)]
