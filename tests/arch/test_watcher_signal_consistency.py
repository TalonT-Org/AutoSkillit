"""Structural guard: all process watchers must call _has_active_execution_marker."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_CLI_APP = Path("src/autoskillit/cli/app.py")
_PROCESS_RACE = Path("src/autoskillit/execution/process/_process_race.py")
_PROCESS_MONITOR = Path("src/autoskillit/execution/process/_process_monitor.py")
_PROCESS_INIT = Path("src/autoskillit/execution/process/__init__.py")

_WATCHERS_THAT_MUST_CHECK_EXECUTION_MARKER = frozenset(
    {
        "_watch_child_activity",
        "_session_log_monitor",
        "_watch_stdout_idle",
    }
)


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


@pytest.mark.parametrize("watcher", sorted(_WATCHERS_THAT_MUST_CHECK_EXECUTION_MARKER))
def test_watcher_calls_has_active_execution_marker(watcher: str) -> None:
    """Each watcher in the set must call _has_active_execution_marker."""
    callers_race = _functions_calling_predicate(_PROCESS_RACE, "_has_active_execution_marker")
    callers_monitor = _functions_calling_predicate(
        _PROCESS_MONITOR, "_has_active_execution_marker"
    )
    all_callers = callers_race | callers_monitor
    assert watcher in all_callers, (
        f"{watcher} does not call _has_active_execution_marker. "
        f"Functions that do: {sorted(all_callers)}"
    )


_SIGNAL_GUARD_ACTIVITY_MUST_CHECK_MARKER = frozenset({"is_server_active"})


@pytest.mark.parametrize("fn_name", sorted(_SIGNAL_GUARD_ACTIVITY_MUST_CHECK_MARKER))
def test_signal_guard_activity_check_calls_has_active_execution_marker(fn_name: str) -> None:
    callers = _functions_calling_predicate(_CLI_APP, "_has_active_execution_marker")
    assert fn_name in callers, (
        f"{fn_name} in cli/app.py does not call _has_active_execution_marker. "
        f"Functions that do: {sorted(callers)}"
    )


_KILL_EXECUTORS_THAT_MUST_CHECK_CHILD_LIVENESS = frozenset(
    {
        "execute_termination_action",
    }
)


@pytest.mark.parametrize("executor", sorted(_KILL_EXECUTORS_THAT_MUST_CHECK_CHILD_LIVENESS))
def test_kill_executor_checks_child_liveness(executor: str) -> None:
    """Kill executors authorized to call async_kill_process_tree must consult
    _has_active_child_processes before killing on the COMPLETED path."""
    callers = _functions_calling_predicate(_PROCESS_INIT, "_has_active_child_processes")
    assert executor in callers, (
        f"{executor} does not call _has_active_child_processes. "
        f"Functions that do: {sorted(callers)}"
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
    completion_branch = next(
        node
        for node in session_log.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and any(
            isinstance(comparator, ast.Attribute) and comparator.attr == "COMPLETION"
            for comparator in node.test.comparators
        )
    )
    assert not _calls_trigger_set(ast.Module(body=completion_branch.body))


def test_completion_eligibility_and_final_fold_consume_both_cursors() -> None:
    eligibility = _function(_PROCESS_RACE, "_watch_completion_eligibility")
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
        (_PROCESS_RACE, "_watch_stdout_idle"),
        (_PROCESS_RACE, "_watch_child_activity"),
        (_PROCESS_MONITOR, "_session_log_monitor"),
    ],
)
def test_timeout_watchers_consume_shared_pending_task_predicate(
    source_path: Path, watcher: str
) -> None:
    function = _function(source_path, watcher)

    arguments = [*function.args.args, *function.args.kwonlyargs]
    assert any(argument.arg == "has_pending_tasks" for argument in arguments)
    assert watcher in _functions_calling_predicate(source_path, "has_pending_tasks")
