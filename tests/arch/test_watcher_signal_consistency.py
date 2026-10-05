"""Watchers use operation leases and child activity as liveness evidence."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.arch._helpers import RACE_WATCHERS_PY

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_CLI_SESSION_PROCESS = Path("src/autoskillit/cli/session/_session_process.py")
_PROCESS_RACE = Path("src/autoskillit/execution/process/_process_race.py")
_PROCESS_MONITOR = Path("src/autoskillit/execution/process/_process_monitor.py")
_PROCESS_INIT = Path("src/autoskillit/execution/process/__init__.py")
_PROCESS_TERMINATION = Path("src/autoskillit/execution/process/_termination.py")
_EXECUTION_ROOT = Path("src/autoskillit/execution")


def _functions_calling_predicate(source_path: Path, predicate: str) -> set[str]:
    """Return names of top-level async functions in source_path that call predicate."""
    tree = ast.parse(source_path.read_text())
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    func = child.func
                    if isinstance(func, ast.Name) and func.id == predicate:
                        result.add(node.name)
                    elif isinstance(func, ast.Attribute) and func.attr == predicate:
                        result.add(node.name)
    return result


def _function(source_path: Path, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    tree = ast.parse(source_path.read_text())
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name
    )


def _called_cursor_names(node: ast.AST) -> set[str]:
    return {
        call.args[0].attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "fold_event_cursor"
        and call.args
        and isinstance(call.args[0], ast.Attribute)
    }


def _calls_trigger_set(node: ast.AST) -> bool:
    return any(
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "trigger"
        and call.func.attr == "set"
        for call in ast.walk(node)
    )


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for call in (child for child in ast.walk(node) if isinstance(child, ast.Call)):
        if isinstance(call.func, ast.Name):
            names.add(call.func.id)
        elif isinstance(call.func, ast.Attribute):
            names.add(call.func.attr)
    return names


def test_liveness_watchers_reach_the_operation_lease_reader() -> None:
    """Watchers use the lease reader directly or through the shared lease helpers."""
    lease_activity = next(
        node
        for node in ast.walk(ast.parse(_PROCESS_MONITOR.read_text()))
        if isinstance(node, ast.ClassDef) and node.name == "_OperationLeaseActivity"
    )
    refresh = next(
        node
        for node in lease_activity.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "refresh"
    )
    active_signals = _function(_PROCESS_MONITOR, "_active_liveness_signals")
    assert "read_active_operation_leases" in _called_names(refresh)
    assert "read_active_operation_leases" in _called_names(active_signals)

    for source_path, function_name in (
        (RACE_WATCHERS_PY, "_watch_child_activity"),
        (RACE_WATCHERS_PY, "_watch_stdout_idle"),
        (_PROCESS_MONITOR, "_session_log_monitor"),
        (_PROCESS_TERMINATION, "_drain_before_escalation"),
    ):
        routes = _called_names(_function(source_path, function_name))
        assert routes & {
            "read_active_operation_leases",
            "_active_liveness_signals",
            "refresh",
        }, f"{function_name} does not reach operation lease liveness"


def test_execution_watchers_have_no_dispatch_marker_dependency() -> None:
    for source_path in _EXECUTION_ROOT.rglob("*.py"):
        source = source_path.read_text(encoding="utf-8")
        assert "_has_active_execution_marker" not in source, source_path
        assert "-in-progress-" not in source, source_path


def test_liveness_consumers_do_not_use_network_connections_as_evidence() -> None:
    consumers = (
        (_PROCESS_MONITOR, "_active_liveness_signals"),
        (_PROCESS_MONITOR, "_stale_suppression_reason"),
        (_PROCESS_MONITOR, "_continue_stale_suppression"),
        (_PROCESS_MONITOR, "_session_log_monitor"),
        (RACE_WATCHERS_PY, "_watch_child_activity"),
        (_PROCESS_TERMINATION, "_drain_before_escalation"),
        (_CLI_SESSION_PROCESS, "_default_activity"),
        (_CLI_SESSION_PROCESS, "_apply_activity_signals"),
    )
    for source_path, function_name in consumers:
        function = _function(source_path, function_name)
        assert "_has_active_api_connection" not in _called_names(function), function_name
        assert all(
            not isinstance(node, ast.Constant) or node.value != "api_connection"
            for node in ast.walk(function)
        ), function_name


_KILL_EXECUTORS_THAT_MUST_CHECK_CHILD_LIVENESS = frozenset(
    {
        "execute_termination_action",
    }
)


@pytest.mark.parametrize("executor", sorted(_KILL_EXECUTORS_THAT_MUST_CHECK_CHILD_LIVENESS))
def test_kill_executor_checks_child_liveness(executor: str) -> None:
    """Kill executors authorized to call async_kill_process_tree must consult
    _has_active_child_processes before killing on the COMPLETED path."""
    callers = _functions_calling_predicate(_PROCESS_TERMINATION, "_has_active_child_processes")
    assert executor in callers, (
        f"{executor} does not call _has_active_child_processes. "
        f"Functions that do: {sorted(callers)}"
    )
    assert executor in _functions_calling_predicate(
        _PROCESS_TERMINATION, "_drain_before_escalation"
    )
    assert "_drain_before_escalation" in _functions_calling_predicate(
        _PROCESS_TERMINATION, "_active_liveness_signals"
    )
    assert "_active_liveness_signals" in _functions_calling_predicate(
        _PROCESS_MONITOR, "_has_active_child_processes"
    )


def test_completion_marker_watchers_do_not_trigger_lifecycle_completion_directly() -> None:
    heartbeat = _function(_PROCESS_RACE, "_watch_heartbeat")
    heartbeat_lifecycle_branch = next(
        node
        for node in heartbeat.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Attribute)
        and node.test.attr == "lifecycle_observation_enabled"
    )
    assert not _calls_trigger_set(ast.Module(body=heartbeat_lifecycle_branch.body))

    session_log = _function(_PROCESS_RACE, "_watch_session_log")
    assert any(
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "acc"
        and call.func.attr == "deposit_session_log_result"
        and any(
            isinstance(argument, ast.Name) and argument.id == "monitor_result"
            for argument in call.args
        )
        for call in ast.walk(session_log)
    )

    deposition = _function(_PROCESS_RACE, "deposit_session_log_result")
    assert isinstance(deposition, ast.FunctionDef)
    for destination, source in (
        ("channel_b_status", "status"),
        ("channel_b_session_id", "session_id"),
        ("channel_b_orphaned_tool_result", "orphaned_tool_result"),
        ("channel_b_cursor", "cursor"),
    ):
        assert any(
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and target.attr == destination
                for target in node.targets
            )
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "monitor_result"
            and node.value.attr == source
            for node in ast.walk(deposition)
        )
    assert any(
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "channel_b_ready"
        and call.func.attr == "set"
        for call in ast.walk(deposition)
    )
    completion_branch = next(
        node
        for node in deposition.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and any(
            isinstance(comparator, ast.Attribute) and comparator.attr == "COMPLETION"
            for comparator in node.test.comparators
        )
    )
    assert isinstance(completion_branch.test, ast.Compare)
    assert isinstance(completion_branch.test.left, ast.Attribute)
    assert isinstance(completion_branch.test.left.value, ast.Name)
    assert completion_branch.test.left.value.id == "monitor_result"
    assert completion_branch.test.left.attr == "status"
    assert not _calls_trigger_set(ast.Module(body=completion_branch.body))
    assert any(
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Attribute)
        and call.func.value.attr == "completion_candidate_event"
        and call.func.attr == "set"
        for call in ast.walk(ast.Module(body=completion_branch.body))
    )
    assert _calls_trigger_set(ast.Module(body=completion_branch.orelse))


def test_completion_eligibility_and_final_fold_consume_both_cursors() -> None:
    eligibility = _function(RACE_WATCHERS_PY, "_watch_completion_eligibility")
    managed_async = _function(_PROCESS_INIT, "run_managed_async")

    assert _called_cursor_names(eligibility) == {"stdout_cursor", "channel_b_cursor"}
    next(
        node
        for node in ast.walk(managed_async)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "lifecycle_observation_enabled"
        and _called_cursor_names(node) == {"stdout_cursor", "channel_b_cursor"}
        and any(
            isinstance(child, ast.Assign)
            and any(
                isinstance(target, ast.Attribute)
                and target.attr == "lifecycle_observation_complete"
                for target in child.targets
            )
            for child in ast.walk(node)
        )
    )


@pytest.mark.parametrize(
    ("source_path", "watcher"),
    [
        (RACE_WATCHERS_PY, "_watch_stdout_idle"),
        (RACE_WATCHERS_PY, "_watch_child_activity"),
        (_PROCESS_MONITOR, "_session_log_monitor"),
    ],
)
def test_timeout_watchers_consume_shared_pending_task_predicate(
    source_path: Path, watcher: str
) -> None:
    function = _function(source_path, watcher)

    arguments = [*function.args.args, *function.args.kwonlyargs]
    assert any(argument.arg == "has_pending_tasks" for argument in arguments)
    predicate_owner = watcher
    if watcher == "_session_log_monitor":
        assert watcher in _functions_calling_predicate(source_path, "_stale_suppression_reason")
        predicate_owner = "_stale_suppression_reason"
    assert predicate_owner in _functions_calling_predicate(source_path, "has_pending_tasks")
