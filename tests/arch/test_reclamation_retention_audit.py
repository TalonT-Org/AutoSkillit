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
from collections.abc import Iterable, Mapping
from pathlib import Path

import pytest

from tests._retention_surface import (
    ACKNOWLEDGED_NON_RECLAIMERS,
    RECLAIMER_TARGETS,
    REPO_ROOT,
    ReclaimerTarget,
    Recurrence,
    RetentionDecision,
    SafetyDecision,
    _validate_safety_decisions,
)
from tests._retention_surface import AUDITED_RETENTION_DECISIONS as _REAL_REGISTRY

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


def _qualified_functions(
    body: list[ast.stmt],
    *,
    prefix: str = "",
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[f"{prefix}{node.name}"] = node
        elif isinstance(node, ast.ClassDef):
            functions.update(_qualified_functions(node.body, prefix=f"{prefix}{node.name}."))
    return functions


def _find_function(
    tree: ast.Module,
    qualified_name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    return _qualified_functions(tree.body).get(qualified_name)


def _scan_skip_linenos(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[int]:
    final_stmt = fn.body[-1] if fn.body else None
    finder = _LoopBoundaryFinder(final_stmt)
    for stmt in fn.body:
        finder.visit(stmt)
    return sorted(finder.skip_linenos)


def _source_path(target: ReclaimerTarget) -> Path:
    return REPO_ROOT / target[0]


def find_missing_registered_functions(
    targets: Iterable[ReclaimerTarget],
) -> list[str]:
    """Return target identities whose qualified definition no longer resolves exactly once."""
    missing: list[str] = []
    for module_path, qualified_name in targets:
        tree = ast.parse(_source_path((module_path, qualified_name)).read_text())
        if _find_function(tree, qualified_name) is None:
            missing.append(f"{module_path}::{qualified_name}")
    return missing


def _actual_retention_branches(targets: Iterable[ReclaimerTarget]) -> set[str]:
    actual: set[str] = set()
    for module_path, qualified_name in targets:
        tree = ast.parse(_source_path((module_path, qualified_name)).read_text())
        fn = _find_function(tree, qualified_name)
        if fn is None:
            continue
        for lineno in _scan_skip_linenos(fn):
            actual.add(f"{module_path}::{qualified_name}::L{lineno}")
    return actual


_LIFECYCLE_CALLS = frozenset(
    {
        "unlink",
        "rmtree",
        "rename",
        "enqueue_retirement",
        "try_reclaim",
        "flush_session_log",
    }
)
_RECLAIMER_VERBS = (
    "prune",
    "reap",
    "cleanup",
    "recover",
    "repair",
    "sweep",
    "remove",
    "enqueue",
    "reclaim",
    "reconcile",
    "retention",
)
_FILESYSTEM_CANDIDATE_CALLS = frozenset({"iterdir", "glob", "rglob"})


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _contains_lifecycle_disposition(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node)
        if name is None:
            continue
        if name in _LIFECYCLE_CALLS:
            return True
    return False


def _iterates_filesystem_candidates(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _FILESYSTEM_CANDIDATE_CALLS
        for node in ast.walk(fn)
    )


def _is_reclaimer_shaped(
    qualified_name: str,
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    has_verb = any(verb in qualified_name.lower() for verb in _RECLAIMER_VERBS)
    return _contains_lifecycle_disposition(fn) and (
        _iterates_filesystem_candidates(fn) or has_verb
    )


def _discover_reclaimer_functions(sources: Mapping[str, str]) -> set[ReclaimerTarget]:
    """Discover source-level lifecycle reclaimers and their filesystem-candidate providers."""
    functions: dict[ReclaimerTarget, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for module_path, source in sources.items():
        for qualified_name, fn in _qualified_functions(ast.parse(source).body).items():
            functions[(module_path, qualified_name)] = fn

    discovered = {
        target for target, fn in functions.items() if _is_reclaimer_shaped(target[1], fn)
    }
    # Some policy-owning reclaimers delegate their final mutation to a helper or callback.
    # Keep those reviewed targets in the source-derived partition when their exact qualified
    # definition is present; this also makes coordinated target/decision-row removal detectable.
    discovered.update(set(functions) & RECLAIMER_TARGETS)
    called_provider_names = {
        name
        for target in discovered
        for node in ast.walk(functions[target])
        if isinstance(node, ast.Call)
        if (name := _called_name(node)) is not None
    }
    discovered.update(
        target
        for target, fn in functions.items()
        if "." not in target[1]
        and target[1] in called_provider_names
        and _iterates_filesystem_candidates(fn)
    )
    return discovered


def _repository_reclaimer_sources() -> dict[str, str]:
    source_paths = [
        *sorted((REPO_ROOT / "src" / "autoskillit").rglob("*.py")),
        REPO_ROOT / "scripts" / "pytest_tmp_lifecycle.py",
    ]
    return {path.relative_to(REPO_ROOT).as_posix(): path.read_text() for path in source_paths}


def _target_partition_errors(
    discovered: set[ReclaimerTarget],
    targets: frozenset[ReclaimerTarget],
    acknowledged: Mapping[ReclaimerTarget, str],
) -> list[str]:
    declared = targets | set(acknowledged)
    errors = []
    if overlap := targets & set(acknowledged):
        errors.append(f"target/acknowledgement overlap: {sorted(overlap)}")
    if missing := discovered - declared:
        errors.append(f"unclassified discovered reclaimers: {sorted(missing)}")
    if stale := declared - discovered:
        errors.append(f"stale targets or acknowledgements: {sorted(stale)}")
    for target, rationale in acknowledged.items():
        if len("".join(rationale.split())) < 40:
            errors.append(f"{target}: acknowledgement needs a substantive rationale")
    return errors


def test_every_retention_branch_is_audited() -> None:
    """Bidirectional: an unregistered skip branch AND a stale registry entry both fail."""
    actual = _actual_retention_branches(RECLAIMER_TARGETS)
    audited = set(_REAL_REGISTRY)
    assert actual == audited, (
        "Reclamation retention-decision registry drift. Every continue/break/skip-all-return "
        "in a declared reclaimer target needs an exact AUDITED_RETENTION_DECISIONS entry. "
        f"Unaudited: {sorted(actual - audited)}; stale entries: {sorted(audited - actual)}"
    )


def test_registered_reclaimer_targets_resolve_once() -> None:
    """Qualified target names cannot silently drift to a same-named method."""
    assert find_missing_registered_functions(RECLAIMER_TARGETS) == []


def test_discovered_reclaimers_are_registered_or_acknowledged() -> None:
    """The lifecycle scan leaves no new reclaimer outside a reviewed partition."""
    errors = _target_partition_errors(
        _discover_reclaimer_functions(_repository_reclaimer_sources()),
        RECLAIMER_TARGETS,
        ACKNOWLEDGED_NON_RECLAIMERS,
    )
    assert errors == []


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
    fn = _find_function(ast.parse(module_path.read_text()), "_reap_synthetic")
    assert fn is not None
    found = _scan_skip_linenos(fn)
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
    assert _validate_safety_decisions(_REAL_REGISTRY) == []
    for key, decision in _REAL_REGISTRY.items():
        assert len(decision.justification.split()) >= 6, f"{key}: justification too short"
        if isinstance(decision, RetentionDecision):
            assert decision.revocability is not None, f"{key}: missing Revocability"
            if decision.revocability.value == "monotonic":
                assert decision.bounded_by, f"{key}: MONOTONIC entry must name a bound"
        else:
            assert isinstance(decision, SafetyDecision), f"{key}: unknown decision shape"
            assert decision.recurrence is not None, f"{key}: missing recurrence"
            if decision.recurrence is Recurrence.RECURS_UNTIL_INPUT_CHANGES:
                assert len("".join((decision.converges_by or "").split())) >= 40, (
                    f"{key}: recurring decision needs substantive converges_by"
                )


def test_safety_decision_rejects_unbounded_recurrence() -> None:
    """A warn-and-skip branch cannot claim indefinite recurrence without an exit condition."""
    with pytest.raises(ValueError, match="substantive convergence reason"):
        SafetyDecision(
            justification=(
                "skips this candidate whenever validation fails during lifecycle inspection"
            ),
            recurrence=Recurrence.RECURS_UNTIL_INPUT_CHANGES,
        )


def test_missing_safety_recurrence_is_caught() -> None:
    """Canary: the validator reports a registry entry that omits the new axis."""
    incomplete = SafetyDecision(
        justification="skips this candidate whenever validation fails during lifecycle inspection",
        recurrence=None,  # type: ignore[arg-type]
    )
    assert _validate_safety_decisions({"synthetic::branch1": incomplete}) == [
        "synthetic::branch1: SafetyDecision is missing recurrence"
    ]


def test_injected_reclaimer_is_not_silently_unclassified() -> None:
    """Canary: source discovery catches a lifecycle reclaimer absent from both partitions."""
    sources = {
        "synthetic.py": (
            "def prune_orphans(root):\n    for path in root.iterdir():\n        path.unlink()\n"
        )
    }
    errors = _target_partition_errors(
        _discover_reclaimer_functions(sources),
        frozenset(),
        {},
    )
    assert errors == ["unclassified discovered reclaimers: [('synthetic.py', 'prune_orphans')]"]


def test_removing_target_and_its_decision_rows_is_caught_by_discovery() -> None:
    """Canary: target-list completeness survives a coordinated target/row deletion."""
    target = (
        "src/autoskillit/execution/_session_log_recovery.py",
        "recover_crashed_sessions",
    )
    reduced_targets = RECLAIMER_TARGETS - {target}
    reduced_registry = {
        key: decision
        for key, decision in _REAL_REGISTRY.items()
        if not key.startswith("src/autoskillit/execution/_session_log_recovery.py::")
    }
    assert _actual_retention_branches(reduced_targets) == set(reduced_registry)
    errors = _target_partition_errors(
        _discover_reclaimer_functions(_repository_reclaimer_sources()),
        reduced_targets,
        ACKNOWLEDGED_NON_RECLAIMERS,
    )
    assert errors == [
        "unclassified discovered reclaimers: "
        "[('src/autoskillit/execution/_session_log_recovery.py', 'recover_crashed_sessions')]"
    ]
