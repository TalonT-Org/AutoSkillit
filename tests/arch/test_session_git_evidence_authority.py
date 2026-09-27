"""Architectural guards for launch-cwd and session Git evidence ownership."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import get_type_hints

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
_EXECUTE_PATH = _SRC_ROOT / "execution" / "headless" / "_headless_execute.py"
_HEADLESS_GIT_PATH = _SRC_ROOT / "execution" / "headless" / "_headless_git.py"

_LAUNCH_SCOPED_CWD_CONSUMERS: dict[str, str] = {
    "derive_exclude_prefix": "pre-spawn write-scope derivation",
    "is_path_under_exclude": "pre-spawn write-scope exclusion",
    "<compare>": "launch-cwd comparison in pre-spawn scope",
    "_resolve_skill_temp_dir": "pre-spawn skill temp-directory selection",
    "is_git_worktree": "launch checkout classification",
    "snapshot_clone_state": "clone contamination snapshot",
    "is_git_main_checkout": "launch main-checkout validation",
    "validate_pre_session_index": "pre-session index validation",
    "check_and_revert_clone_contamination": "clone contamination recovery",
    "_capture_pre_session_git_state": "session evidence input",
    "_build_skill_result": "launch-boundary path validation",
    "_attempt_contract_nudge": "same-process retry cwd",
    "collect_and_project_child_outcomes": "native child log lookup",
    "build_terminal_flush_kwargs": "persisted launch cwd",
}


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _source_order_calls(function: ast.AST, name: str) -> list[ast.Call]:
    return sorted(
        (
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and _call_name(node) == name
        ),
        key=lambda node: (node.lineno, node.col_offset),
    )


def _find_function(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    match = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        ),
        None,
    )
    assert match is not None, f"{name} not found"
    return match


def _assigned_name(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str | None:
    current = node
    while current in parents:
        parent = parents[current]
        if isinstance(parent, (ast.Assign, ast.AnnAssign)):
            targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
            return next((target.id for target in targets if isinstance(target, ast.Name)), None)
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return None
        current = parent
    return None


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next((keyword.value for keyword in call.keywords if keyword.arg == name), None)


def _parent_map(root: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(root) for child in ast.iter_child_nodes(node)}


def _has_cwd_parameter(node: ast.AST) -> bool:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return False
    args = node.args
    parameters = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    if args.vararg is not None:
        parameters.append(args.vararg)
    if args.kwarg is not None:
        parameters.append(args.kwarg)
    return any(parameter.arg == "cwd" for parameter in parameters)


def _nested_cwd_shadowers(function: ast.AST) -> list[str]:
    shadowers: list[str] = []
    for node in ast.walk(function):
        if node is function or not _has_cwd_parameter(node):
            continue
        shadowers.append(getattr(node, "name", "<lambda>"))
    return shadowers


def _is_path_wrapper(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Name) and call.func.id == "Path"


def _enclosing_cwd_consumer(node: ast.Name, parents: dict[ast.AST, ast.AST]) -> str | None:
    current: ast.AST = node
    while current in parents:
        parent = parents[current]
        if isinstance(parent, ast.Call):
            if not _is_path_wrapper(parent):
                return _call_name(parent)
        elif isinstance(parent, ast.Compare):
            return "<compare>"
        current = parent
    return None


def _launch_cwd_consumers(
    function: ast.AST, parents: dict[ast.AST, ast.AST]
) -> tuple[set[str], list[int]]:
    consumers: set[str] = set()
    unclassified: list[int] = []
    for node in ast.walk(function):
        if not (
            isinstance(node, ast.Name) and node.id == "cwd" and isinstance(node.ctx, ast.Load)
        ):
            continue
        key = _enclosing_cwd_consumer(node, parents)
        if key is None:
            unclassified.append(node.lineno)
        else:
            consumers.add(key)
    return consumers, unclassified


def test_execute_classifies_every_launch_cwd_use() -> None:
    tree = ast.parse(_EXECUTE_PATH.read_text(encoding="utf-8"), filename=str(_EXECUTE_PATH))
    function = _find_function(tree, "_execute_claude_headless")
    parents = _parent_map(function)

    shadowed = _nested_cwd_shadowers(function)
    assert not shadowed, f"nested scope shadows launch cwd: {shadowed}"

    consumers, unclassified = _launch_cwd_consumers(function, parents)
    assert not unclassified, (
        f"launch cwd is aliased or otherwise unclassified at lines {unclassified}"
    )
    assert consumers == set(_LAUNCH_SCOPED_CWD_CONSUMERS), (
        "launch-cwd consumer registry differs from _execute_claude_headless: "
        f"unregistered={sorted(consumers - set(_LAUNCH_SCOPED_CWD_CONSUMERS))}, "
        f"stale={sorted(set(_LAUNCH_SCOPED_CWD_CONSUMERS) - consumers)}"
    )


def test_measurement_functions_accept_evidence_types() -> None:
    import autoskillit.execution.headless._headless_git as headless_git
    import autoskillit.execution.headless._headless_helpers as headless_helpers

    for name, function in vars(headless_git).items():
        if not callable(function) or not name.startswith(("_observe_", "_compute_", "_detect_")):
            continue
        hints = get_type_hints(function)
        parameters = list(inspect.signature(function).parameters)
        assert parameters, f"{name} must receive evidence state"
        assert hints.get(parameters[0]) in {
            headless_git.PreSessionGitState,
            headless_git.SessionGitEvidence,
        }, f"{name} first parameter must be a Git evidence type"
        for parameter in parameters:
            if parameter in {"cwd", "path"}:
                assert hints.get(parameter) not in {str, Path}, (
                    f"{name} must not accept raw {parameter} for measurement"
                )

    metrics = headless_helpers._compute_post_session_metrics
    metrics_parameters = list(inspect.signature(metrics).parameters)
    assert len(metrics_parameters) == 1
    assert get_type_hints(metrics)[metrics_parameters[0]] is headless_git.SessionGitEvidence


def test_executor_observes_once_and_threads_one_evidence_object() -> None:
    tree = ast.parse(_EXECUTE_PATH.read_text(encoding="utf-8"), filename=str(_EXECUTE_PATH))
    function = _find_function(tree, "_execute_claude_headless")
    parents = _parent_map(function)
    observe = _source_order_calls(function, "_observe_session_git_evidence")
    build = _source_order_calls(function, "_build_skill_result")
    guard = _source_order_calls(function, "check_and_revert_clone_contamination")
    metrics = _source_order_calls(function, "_compute_post_session_metrics")

    assert len(observe) == len(build) == len(guard) == len(metrics) == 1
    assert (observe[0].lineno, observe[0].col_offset) < (build[0].lineno, build[0].col_offset)
    evidence_name = _assigned_name(observe[0], parents)
    assert evidence_name is not None, "evidence observation result must have one named owner"

    git_writes = _keyword(build[0], "git_writes_detected")
    assert isinstance(git_writes, ast.Attribute)
    assert git_writes.attr == "git_writes_detected"
    assert isinstance(git_writes.value, ast.Name) and git_writes.value.id == evidence_name
    assert _keyword(build[0], "parsed_session") is not None

    new_worktrees = _keyword(guard[0], "new_worktrees")
    assert isinstance(new_worktrees, ast.Attribute)
    assert new_worktrees.attr == "new_worktrees"
    assert isinstance(new_worktrees.value, ast.Name) and new_worktrees.value.id == evidence_name

    assert len(metrics[0].args) == 1
    assert isinstance(metrics[0].args[0], ast.Name)
    assert metrics[0].args[0].id == evidence_name


def test_git_evidence_values_are_constructed_only_by_the_authority() -> None:
    constructors = {"EvidenceWorktree", "SessionGitEvidence", "PreSessionGitState"}
    offenders: list[str] = []
    for path in _SRC_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id in constructors and path != _HEADLESS_GIT_PATH:
                offenders.append(f"{path.relative_to(_SRC_ROOT)}:{node.lineno}:{node.func.id}")
    assert not offenders, "Git evidence values constructed outside authority: " + ", ".join(
        offenders
    )


def test_headless_git_commands_have_one_owner() -> None:
    headless_dir = _SRC_ROOT / "execution" / "headless"
    owners: set[Path] = set()
    for path in headless_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
                first = node.elts[0]
                if isinstance(first, ast.Constant) and first.value == "git":
                    owners.add(path)
    assert owners == {_HEADLESS_GIT_PATH}, f"Git argv construction found in: {sorted(owners)}"
