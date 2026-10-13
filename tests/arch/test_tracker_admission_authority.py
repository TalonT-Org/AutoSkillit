"""Pipeline tracker admission has a declared set of writers and callers."""

from __future__ import annotations

import ast
from collections import Counter

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.medium]

_EXPECTED_TRACKER_CALLS = (
    (
        "server/tools/tools_pipeline_tracker/_handlers.py",
        "_handle_init",
        "initialize_manual_tracker",
        1,
    ),
    (
        "server/tools/tools_kitchen/_open_kitchen/_tracker_auto_init.py",
        "_auto_init_pipeline_tracker",
        "initialize_kitchen_tracker",
        1,
    ),
    (
        "server/tools/tools_pipeline_tracker/_handlers.py",
        "mark_step_complete",
        "mutate_tracker",
        1,
    ),
    (
        "server/tools/tools_pipeline_tracker/_handlers.py",
        "mark_step_skipped",
        "mutate_tracker",
        1,
    ),
    (
        "server/tools/tools_pipeline_tracker/_handlers.py",
        "_handle_complete",
        "mark_step_complete",
        1,
    ),
    (
        "server/tools/tools_pipeline_tracker/_handlers.py",
        "_apply_receipt_tracker_outcome",
        "mark_step_complete",
        1,
    ),
    (
        "server/tools/tools_execution/_run_skill_admission.py",
        "_admit_step_guard",
        "mark_step_skipped",
        1,
    ),
    (
        "server/tools/tools_execution/_run_skill_admission.py",
        "_handle_audit_reservation_decision",
        "_begin_run_skill_completion",
        3,
    ),
    (
        "server/tools/tools_execution/_run_skill_dispatch.py",
        "run_skill",
        "_begin_run_skill_completion",
        1,
    ),
    (
        "server/tools/tools_execution/_run_skill_dispatch.py",
        "_resolve_fresh_recipe_step",
        "_check_pipeline_deps",
        1,
    ),
    (
        "server/tools/tools_execution/_run_skill_dispatch.py",
        "run_skill",
        "_check_pipeline_deps",
        1,
    ),
)

_TRACKED_CALLEES = frozenset(callee for _, _, callee, _ in _EXPECTED_TRACKER_CALLS)


def _terminal_callee(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


class _TrackerCallVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.functions: list[str] = []
        self.calls: Counter[tuple[str, str, str]] = Counter()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.functions.append(self.functions[0] if self.functions else node.name)
        self.generic_visit(node)
        self.functions.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Call(self, node: ast.Call) -> None:
        callee = _terminal_callee(node)
        if callee in _TRACKED_CALLEES:
            function = self.functions[0] if self.functions else "<module>"
            self.calls[(self.path, function, callee)] += 1
        self.generic_visit(node)


def _tracker_call_inventory() -> Counter[tuple[str, str, str]]:
    calls: Counter[tuple[str, str, str]] = Counter()
    for path in sorted(SRC_ROOT.rglob("*.py")):
        relative = path.relative_to(SRC_ROOT).as_posix()
        visitor = _TrackerCallVisitor(relative)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        calls.update(visitor.calls)
    return calls


def test_tracker_writer_and_credit_callers_match_the_inventory() -> None:
    expected = Counter(
        {
            (path, function, callee): count
            for path, function, callee, count in _EXPECTED_TRACKER_CALLS
        }
    )
    actual = _tracker_call_inventory()

    assert actual == expected, (
        f"Tracker writer, credit, or gate callers changed.\n"
        f"Missing calls: {dict(expected - actual)}\n"
        f"Unexpected calls: {dict(actual - expected)}"
    )
    seen = {callee for (_, _, callee), count in actual.items() if count}
    assert _TRACKED_CALLEES <= seen


def test_tracker_writers_accept_only_an_admitted_initialization_type() -> None:
    path = SRC_ROOT / "core" / "pipeline" / "pipeline_tracker.py"
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    writers = {
        node.name: node
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"initialize_manual_tracker", "initialize_kitchen_tracker"}
    }

    assert set(writers) == {"initialize_manual_tracker", "initialize_kitchen_tracker"}
    for name, writer in writers.items():
        parameters = writer.args.args
        assert len(parameters) >= 3, f"{name} must receive its tracker initialization"
        annotation = parameters[2].annotation
        assert isinstance(annotation, ast.Name) and annotation.id == "TrackerInitialization"


def test_dependency_gate_reads_step_kind_only_through_the_predicate() -> None:
    path = SRC_ROOT / "server" / "tools" / "_pipeline_deps.py"
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "tool" for node in ast.walk(module)
    )
    assert not any(
        isinstance(node, ast.Name) and node.id == "SKILL_TOOLS" for node in ast.walk(module)
    )
