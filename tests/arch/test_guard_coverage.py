"""Fail-closed launch guards must retain designated branch coverage."""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_ROOT = Path(__file__).resolve().parents[2]
_FAIL_CLOSED_PREFIXES = ("interactive environment ", "interactive executable ")


@dataclass(frozen=True)
class GuardCoverage:
    source_path: str
    function: str
    message: str
    designated_test_path: str
    designated_test_function: str


FAIL_CLOSED_GUARDS = (
    GuardCoverage(
        "src/autoskillit/execution/backends/claude.py",
        "build_interactive_cmd",
        "interactive environment changed after executable binding",
        "tests/execution/backends/test_claude_startup_readiness.py",
        "test_interactive_cmd_rejects_environment_changed_after_binding",
    ),
    GuardCoverage(
        "src/autoskillit/execution/backends/codex.py",
        "build_interactive_cmd",
        "interactive environment changed after executable binding",
        "tests/execution/backends/test_codex_config_validation.py",
        "test_interactive_cmd_rejects_environment_changed_after_binding",
    ),
    GuardCoverage(
        "src/autoskillit/cli/session/_session_launch.py",
        "prepare_interactive_launch",
        "interactive executable identity changed between probe and launch preparation",
        "tests/cli/test_interactive_cold_launch_medium.py",
        "test_executable_identity_drift_exits_without_spawn",
    ),
    GuardCoverage(
        "src/autoskillit/cli/session/_session_launch.py",
        "_run_interactive_session",
        "interactive executable changed after capability probing",
        "tests/cli/test_session_launch.py",
        "test_managed_launch_rejects_executable_drift_before_spawn",
    ),
    GuardCoverage(
        "src/autoskillit/cli/session/_session_launch.py",
        "_run_interactive_session",
        "interactive executable changed after capability probing",
        "tests/cli/test_interactive_cold_launch_medium.py",
        "test_unmanaged_launch_rejects_executable_drift_before_spawn",
    ),
    GuardCoverage(
        "src/autoskillit/cli/session/_session_cook.py",
        "cook",
        "interactive executable changed after capability probing",
        "tests/cli/test_cook_cold_launch_medium.py",
        "test_cook_rejects_executable_drift_before_spawn",
    ),
)


def _string_literals(node: ast.AST) -> set[str]:
    return {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }


def _guard_messages(node: ast.AST) -> set[str]:
    messages = {value.removeprefix("ERROR: ").strip() for value in _string_literals(node)}
    return {message for message in messages if message.startswith(_FAIL_CLOSED_PREFIXES)}


def _nearest_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
        current = parents.get(current)
    return None


def _production_guards() -> Counter[tuple[str, str, str]]:
    found: Counter[tuple[str, str, str]] = Counter()
    source_root = _ROOT / "src" / "autoskillit"
    for path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        for raise_node in (node for node in ast.walk(tree) if isinstance(node, ast.Raise)):
            branch = parents.get(raise_node)
            while branch is not None and not isinstance(branch, ast.If):
                branch = parents.get(branch)
            if branch is None:
                continue
            if raise_node not in branch.body:
                continue
            messages = _guard_messages(branch)
            function = _nearest_function(raise_node, parents)
            if function is None:
                continue
            rel = path.relative_to(_ROOT).as_posix()
            for message in messages:
                found[(rel, function, message)] += 1
    return found


def _find_test_function(record: GuardCoverage) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    path = _ROOT / record.designated_test_path
    if not path.is_file():
        return None
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == record.designated_test_function
        ),
        None,
    )


def _test_asserts_message(node: ast.FunctionDef | ast.AsyncFunctionDef, message: str) -> bool:
    def contains_message(value: ast.AST) -> bool:
        return any(message in literal for literal in _string_literals(value))

    for call in (child for child in ast.walk(node) if isinstance(child, ast.Call)):
        if isinstance(call.func, ast.Attribute) and call.func.attr == "raises":
            for keyword in call.keywords:
                if keyword.arg == "match" and contains_message(keyword.value):
                    return True
    for assertion in (child for child in ast.walk(node) if isinstance(child, ast.Assert)):
        for comparison in (
            child for child in ast.walk(assertion.test) if isinstance(child, ast.Compare)
        ):
            operands = (comparison.left, *comparison.comparators)
            if any(contains_message(operand) for operand in operands):
                return True
    return False


def test_fail_closed_guard_registry_matches_production() -> None:
    registered = Counter(
        (record.source_path, record.function, record.message) for record in FAIL_CLOSED_GUARDS
    )
    assert _production_guards() == registered


def test_each_fail_closed_guard_has_designated_message_coverage() -> None:
    missing: list[str] = []
    for record in FAIL_CLOSED_GUARDS:
        function = _find_test_function(record)
        if function is None or not _test_asserts_message(function, record.message):
            missing.append(f"{record.designated_test_path}::{record.designated_test_function}")
    assert not missing, "Missing designated fail-closed message assertions: " + ", ".join(missing)
