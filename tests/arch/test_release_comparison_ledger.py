"""Bidirectional ledger for PEP 440 comparisons in production code.

Commit/SHA comparisons are deliberately not inferred here. A proposed predicate that
required two SHA-looking attributes matched no production sites: real SHA comparisons use
locals and ``dict.get()`` results, including unrelated GitHub plumbing. Release questions
are instead protected by the hard authority rule below, while this ledger exhaustively
tracks comparisons derived from ``Version``/``parse`` calls and their bound names.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

import pytest

from autoskillit.core import paths

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_LEDGER_PATH = Path(__file__).parent / "release_comparison_ledger.txt"
_AUTHORITY_PATH = "core/_release_identity.py"
_RELEASE_QUESTIONS = frozenset({"update-available", "upgrade-advanced"})
_EXPECTED_RELEASE_AUTHORITIES = {
    (_AUTHORITY_PATH, "advance_verdict"): {"upgrade-advanced"},
    (_AUTHORITY_PATH, "update_available"): {"update-available"},
}
_RECORD_RE = re.compile(r"^[a-z0-9_/]+\.py::(?:[A-Za-z_][A-Za-z0-9_]*|<module>)::[a-z][a-z0-9-]*$")


def _read_ledger() -> list[str]:
    return [
        stripped
        for line in _LEDGER_PATH.read_text(encoding="utf-8").splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    ]


def _is_version_call(node: ast.AST | None) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"Version", "parse"}
    )


def _assigned_version_names(node: ast.AST) -> set[str]:
    if not isinstance(node, (ast.Assign, ast.AnnAssign)) or not _is_version_call(node.value):
        return set()
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return {target.id for target in targets if isinstance(target, ast.Name)}


class _ScopeNodes(ast.NodeVisitor):
    """Collect nodes in one lexical scope without entering nested scopes."""

    def __init__(self) -> None:
        self.assignments: list[ast.Assign | ast.AnnAssign] = []
        self.comparisons: list[ast.Compare] = []

    def visit_Assign(self, node: ast.Assign) -> None:
        self.assignments.append(node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.assignments.append(node)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        self.comparisons.append(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        del node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        del node

    def visit_Lambda(self, node: ast.Lambda) -> None:
        del node

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        del node


def _collect_scope(statements: list[ast.stmt]) -> _ScopeNodes:
    collector = _ScopeNodes()
    for statement in statements:
        collector.visit(statement)
    return collector


def _version_comparison_lines(
    scope: _ScopeNodes,
    *,
    module_names: set[str],
) -> tuple[int, ...]:
    lines: list[int] = []
    for comparison in scope.comparisons:
        local_names = set(module_names)
        for assignment in scope.assignments:
            if assignment.lineno < comparison.lineno:
                local_names.update(_assigned_version_names(assignment))
        operands = (comparison.left, *comparison.comparators)
        if any(
            _is_version_call(operand)
            or (isinstance(operand, ast.Name) and operand.id in local_names)
            for operand in operands
        ):
            lines.append(comparison.lineno)
    return tuple(lines)


def _discover_sites() -> dict[tuple[str, str], tuple[int, ...]]:
    discovered: dict[tuple[str, str], list[int]] = defaultdict(list)
    source_root = paths.pkg_root()
    for source_path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        relative_path = source_path.relative_to(source_root).as_posix()
        module_scope = _collect_scope(tree.body)
        module_names = set().union(
            *(_assigned_version_names(node) for node in module_scope.assignments)
        )
        module_lines = _version_comparison_lines(module_scope, module_names=module_names)
        discovered[(relative_path, "<module>")].extend(module_lines)

        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            scope = _collect_scope(function.body)
            lines = _version_comparison_lines(scope, module_names=module_names)
            discovered[(relative_path, function.name)].extend(lines)
    return {site: tuple(sorted(set(lines))) for site, lines in discovered.items() if lines}


def _ledger_sites(lines: list[str]) -> dict[tuple[str, str], set[str]]:
    sites: dict[tuple[str, str], set[str]] = defaultdict(set)
    for line in lines:
        path, function, question = line.split("::")
        sites[(path, function)].add(question)
    return dict(sites)


_LEDGER_LINES = _read_ledger()
_LEDGER_SITES = _ledger_sites(_LEDGER_LINES)
_DISCOVERED_SITES = _discover_sites()


def test_every_release_comparison_is_registered() -> None:
    missing = sorted(set(_DISCOVERED_SITES) - set(_LEDGER_SITES))
    details = [
        f"{path}:{','.join(map(str, _DISCOVERED_SITES[(path, function)]))}::{function}"
        for path, function in missing
    ]
    assert not details, (
        "Version-derived comparisons are missing from release_comparison_ledger.txt: "
        f"{details}. Register the question each site answers."
    )


def test_every_ledger_entry_has_a_release_comparison() -> None:
    stale = sorted(set(_LEDGER_SITES) - set(_DISCOVERED_SITES))
    assert not stale, (
        "Release comparison ledger entries no longer have a matching AST site: "
        f"{stale}. Remove or update the ledger entry with the code change."
    )


def test_release_questions_have_one_core_authority() -> None:
    violations = sorted(
        f"{path}::{function}::{question}"
        for (path, function), questions in _LEDGER_SITES.items()
        for question in questions
        if question in _RELEASE_QUESTIONS and path != _AUTHORITY_PATH
    )
    assert not violations, (
        "update-available and upgrade-advanced comparisons may only live in "
        f"{_AUTHORITY_PATH}: {violations}"
    )
    assert {
        site: questions
        for site, questions in _LEDGER_SITES.items()
        if questions & _RELEASE_QUESTIONS
    } == _EXPECTED_RELEASE_AUTHORITIES


def test_opaque_release_identity_keys_are_never_version_parsed() -> None:
    violations: list[str] = []
    source_root = paths.pkg_root()
    for source_path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if not _is_version_call(node):
                continue
            assert isinstance(node, ast.Call)
            if any(
                isinstance(argument, ast.Call)
                and isinstance(argument.func, ast.Attribute)
                and argument.func.attr == "key"
                for argument in node.args
            ):
                relative = source_path.relative_to(source_root).as_posix()
                violations.append(f"{relative}:{node.lineno}")
    assert not violations, (
        f"ReleaseIdentity.key() is opaque and must never be passed to Version/parse: {violations}"
    )


@pytest.mark.parametrize("function_name", ("update_available", "advance_verdict"))
def test_release_policy_dispatch_is_exhaustive(function_name: str) -> None:
    source_path = paths.pkg_root() / _AUTHORITY_PATH
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        ),
        None,
    )
    assert function is not None, f"{function_name} is missing from {_AUTHORITY_PATH}"
    has_channel_match = any(
        isinstance(node, ast.Match)
        and isinstance(node.subject, ast.Name)
        and node.subject.id == "channel"
        for node in ast.walk(function)
    )
    has_assert_never = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "assert_never"
        for node in ast.walk(function)
    )
    assert has_channel_match, f"{function_name} must match exhaustively on ReleaseChannel"
    assert has_assert_never, f"{function_name} must close its match with assert_never()"


def test_release_comparison_ledger_is_sorted_deduplicated_and_well_formed() -> None:
    assert len(_LEDGER_LINES) == len(set(_LEDGER_LINES)), (
        "release_comparison_ledger.txt contains duplicate records"
    )
    assert _LEDGER_LINES == sorted(_LEDGER_LINES), (
        "release_comparison_ledger.txt records must be sorted"
    )
    malformed = [line for line in _LEDGER_LINES if _RECORD_RE.fullmatch(line) is None]
    assert not malformed, f"Malformed release comparison ledger records: {malformed}"
