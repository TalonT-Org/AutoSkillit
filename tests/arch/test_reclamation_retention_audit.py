"""Arch guard: every branch in a declared reclaimer that skips reclaiming a candidate must be
registered in AUDITED_RETENTION_DECISIONS, bidirectionally -- mirrors
tests/infra/test_taskfile_destructive_ops.py's AUDITED_DESTRUCTIVE_TASKFILE_OPS exactly.

A "skip branch" is: any `continue`/`break` statement anywhere in the target function (Python
syntax makes these unambiguous -- they always refer to the nearest enclosing loop), or any
`return` statement that is not nested inside a `for`/`while` loop AND is not the function's
own final top-level statement (which represents normal completion, not a skip). See
tests/_retention_surface.py's module docstring for the reclaimers this scanner does not (yet)
cover, and why.
"""

from __future__ import annotations

import ast

import pytest

from tests._retention_surface import AUDITED_RETENTION_DECISIONS as _REAL_REGISTRY
from tests._retention_surface import RECLAIMER_TARGETS, RetentionDecision, SafetyDecision

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


class _LoopBoundaryFinder(ast.NodeVisitor):
    """Collect the line numbers of continue/break statements and out-of-loop returns within
    one function, without descending into nested function/class defs (those are separate
    reclaimers, audited separately if ever declared).
    """

    def __init__(self, final_stmt: ast.stmt | None) -> None:
        self._loop_depth = 0
        self._final_stmt = final_stmt
        self.skip_linenos: list[int] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass  # do not descend into nested defs

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        pass

    def visit_For(self, node: ast.For) -> None:
        self._loop_depth += 1
        self.generic_visit(node)
        self._loop_depth -= 1

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._loop_depth += 1
        self.generic_visit(node)
        self._loop_depth -= 1

    def visit_While(self, node: ast.While) -> None:
        self._loop_depth += 1
        self.generic_visit(node)
        self._loop_depth -= 1

    def visit_Continue(self, node: ast.Continue) -> None:
        self.skip_linenos.append(node.lineno)

    def visit_Break(self, node: ast.Break) -> None:
        self.skip_linenos.append(node.lineno)

    def visit_Return(self, node: ast.Return) -> None:
        if self._loop_depth > 0:
            return
        if node is self._final_stmt:
            return
        self.skip_linenos.append(node.lineno)


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _scan_skip_linenos(source: str, function_name: str) -> list[int]:
    tree = ast.parse(source)
    fn = _find_function(tree, function_name)
    if fn is None:
        raise AssertionError(f"function {function_name!r} not found")
    final_stmt = fn.body[-1] if fn.body else None
    finder = _LoopBoundaryFinder(final_stmt)
    for stmt in fn.body:
        finder.visit(stmt)
    return sorted(finder.skip_linenos)


def _actual_retention_branches(targets: dict[str, tuple]) -> set[str]:
    actual: set[str] = set()
    for dotted_path, (file_path, function_name) in targets.items():
        source = file_path.read_text()
        for lineno in _scan_skip_linenos(source, function_name):
            actual.add(f"{dotted_path}::L{lineno}")
    return actual


def test_every_retention_branch_is_audited() -> None:
    """Bidirectional: an unregistered skip branch AND a stale registry entry both fail."""
    actual = _actual_retention_branches(RECLAIMER_TARGETS)
    audited = set(_REAL_REGISTRY)
    assert actual == audited, (
        "Reclamation retention-decision registry drift. Every continue/break/skip-all-return "
        "in a declared reclaimer target needs an exact AUDITED_RETENTION_DECISIONS entry. "
        f"Unaudited: {sorted(actual - audited)}; stale entries: {sorted(audited - actual)}"
    )


def test_unregistered_retention_branch_is_caught(tmp_path) -> None:
    """Canary: a synthetic reclaimer with an unregistered skip branch must be reported."""
    module_path = tmp_path / "bad_reclaimer.py"
    module_path.write_text(
        "def _reap_synthetic(candidates):\n"
        "    for candidate in candidates:\n"
        "        if candidate.dead:\n"
        "            continue\n"
        "        reclaim(candidate)\n"
    )
    found = _scan_skip_linenos(module_path.read_text(), "_reap_synthetic")
    # This branch (candidate.dead -> continue) would need a registry entry keyed
    # "<module>::_reap_synthetic::L4"; since no such module is declared in
    # RECLAIMER_TARGETS at all, the scanner's target-declaration step is itself what a real
    # unregistered-module omission would be caught by -- but the per-function skip-detection
    # the audit depends on is proven here: it finds exactly the one skip branch expected.
    assert found == [4]


def test_retention_justifications_declare_an_evidence_class() -> None:
    """Every RetentionDecision names a Revocability; a MONOTONIC entry also names a bound.

    (Enforced structurally by RetentionDecision.__post_init__ at import time too -- this test
    additionally proves the property holds across the live registry, not just per-instance.)
    """
    for key, decision in _REAL_REGISTRY.items():
        assert len(decision.justification.split()) >= 6, f"{key}: justification too short"
        if isinstance(decision, RetentionDecision):
            assert decision.revocability is not None, f"{key}: missing Revocability"
            if decision.revocability.value == "monotonic":
                assert decision.bounded_by, f"{key}: MONOTONIC entry must name a bound"
        else:
            assert isinstance(decision, SafetyDecision), f"{key}: unknown decision shape"
