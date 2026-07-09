"""Structural guards for process watcher liveness signal consistency."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_CLI_APP = Path("src/autoskillit/cli/app.py")
_PROCESS_RACE = Path("src/autoskillit/execution/process/_process_race.py")
_PROCESS_MONITOR = Path("src/autoskillit/execution/process/_process_monitor.py")

_WATCHERS_THAT_MUST_CHECK_EXECUTION_MARKER = frozenset(
    {
        "_watch_child_activity",
        "_session_log_monitor",
        "_watch_stdout_idle",
    }
)

_WATCHERS_THAT_MUST_USE_SUPERVISOR: dict[str, frozenset[str]] = {
    "_heartbeat": frozenset({"publish_event"}),
    "_watch_stdout_idle": frozenset({"in_flight_under_deadline"}),
    "_session_log_monitor": frozenset({"in_flight_under_deadline"}),
    "_watch_child_activity": frozenset({"operation_deadline_floor"}),
}


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


def _find_function(source_path: Path, fn_name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(source_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == fn_name:
            return node
    raise AssertionError(f"{fn_name} not found in {source_path}")


def _attributes_called_on(function: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> set[str]:
    methods: set[str] = set()
    for child in ast.walk(function):
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and isinstance(child.func.value, ast.Name)
            and child.func.value.id == name
        ):
            methods.add(child.func.attr)
    return methods


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


@pytest.mark.parametrize(
    ("watcher", "required_methods"),
    sorted(_WATCHERS_THAT_MUST_USE_SUPERVISOR.items()),
)
def test_process_watcher_participates_in_liveness_supervisor(
    watcher: str, required_methods: frozenset[str]
) -> None:
    source_path = (
        _PROCESS_MONITOR if watcher in {"_heartbeat", "_session_log_monitor"} else _PROCESS_RACE
    )
    function = _find_function(source_path, watcher)
    args = {arg.arg for arg in function.args.args + function.args.kwonlyargs}
    assert "liveness_supervisor" in args

    called_methods = _attributes_called_on(function, "liveness_supervisor")
    assert required_methods <= called_methods, (
        f"{watcher} does not consult liveness_supervisor via {sorted(required_methods)}. "
        f"Methods called: {sorted(called_methods)}"
    )
