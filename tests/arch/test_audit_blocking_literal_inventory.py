"""Prevent unregistered copies of audit-assessment string collections."""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass

import pytest

from autoskillit.core import AuditAssessment
from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_ASSESSMENT_VALUES = frozenset(member.value for member in AuditAssessment)
_EXPECTED_LITERALS = Counter(
    {
        (
            "core/types/_type_closure_report.py",
            "<module>",
            frozenset(
                {
                    "COVERED",
                    "MISSING",
                    "ODD",
                    "CONFLICT",
                    "UNPRESCRIBED_SUBSTITUTION",
                }
            ),
        ): 1,
        (
            "core/types/_type_closure_report.py",
            "<module>",
            frozenset({"MISSING", "CONFLICT", "UNPRESCRIBED_SUBSTITUTION"}),
        ): 1,
    }
)


@dataclass(frozen=True)
class _AuditLiteralSite:
    path: str
    function: str
    lineno: int
    values: frozenset[str]


@dataclass(frozen=True)
class _UnresolvableAuditLiteral:
    path: str
    function: str
    lineno: int


class _AuditLiteralCollector(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self._path = path
        self._functions: list[str] = []
        self.sites: list[_AuditLiteralSite] = []
        self.unresolvable: list[_UnresolvableAuditLiteral] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._functions.append(node.name)
        self.generic_visit(node)
        self._functions.pop()

    def visit_Set(self, node: ast.Set) -> None:
        self._record(node.elts, node.lineno)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "frozenset"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Set)
        ):
            self._record(node.args[0].elts, node.lineno)
            return
        self.generic_visit(node)

    def _record(self, elements: list[ast.expr], lineno: int) -> None:
        values: set[str] = set()
        has_unresolvable_element = False
        for element in elements:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                if element.value in _ASSESSMENT_VALUES:
                    values.add(element.value)
                continue
            has_unresolvable_element = True
        if not values:
            return
        function = ".".join(self._functions) or "<module>"
        if has_unresolvable_element:
            self.unresolvable.append(_UnresolvableAuditLiteral(self._path, function, lineno))
            return
        if all(
            isinstance(element, ast.Constant)
            and isinstance(element.value, str)
            and element.value in _ASSESSMENT_VALUES
            for element in elements
        ):
            self.sites.append(_AuditLiteralSite(self._path, function, lineno, frozenset(values)))


def _scan_tree(tree: ast.AST, path: str) -> _AuditLiteralCollector:
    collector = _AuditLiteralCollector(path)
    collector.visit(tree)
    return collector


def _source_literals() -> _AuditLiteralCollector:
    collector = _AuditLiteralCollector("<combined>")
    for source_path in sorted(SRC_ROOT.rglob("*.py")):
        relative_path = source_path.relative_to(SRC_ROOT).as_posix()
        scanned = _scan_tree(
            ast.parse(source_path.read_text(encoding="utf-8")),
            relative_path,
        )
        collector.sites.extend(scanned.sites)
        collector.unresolvable.extend(scanned.unresolvable)
    return collector


def _inventory(collector: _AuditLiteralCollector) -> Counter[tuple[str, str, frozenset[str]]]:
    return Counter((site.path, site.function, site.values) for site in collector.sites)


def test_audit_assessment_literal_inventory_is_complete_and_exact() -> None:
    collector = _source_literals()

    assert not collector.unresolvable, collector.unresolvable
    assert _inventory(collector) == _EXPECTED_LITERALS


def test_unresolvable_audit_assessment_literal_fails_closed() -> None:
    collector = _scan_tree(
        ast.parse('def check(label):\n    return frozenset({"MISSING", label})\n'),
        "fixture.py",
    )

    assert collector.unresolvable == [
        _UnresolvableAuditLiteral("fixture.py", "check", 2),
    ]


def test_unregistered_audit_assessment_literal_changes_the_inventory() -> None:
    collector = _scan_tree(
        ast.parse('BLOCKING = {"MISSING", "CONFLICT"}\n'),
        "fixture.py",
    )

    assert _inventory(collector) != _EXPECTED_LITERALS
