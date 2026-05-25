"""Structural guard: all process watchers must call _has_active_dispatch_marker."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_PROCESS_RACE = Path("src/autoskillit/execution/process/_process_race.py")
_PROCESS_MONITOR = Path("src/autoskillit/execution/process/_process_monitor.py")

_WATCHERS_THAT_MUST_CHECK_DISPATCH_MARKER = frozenset(
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


@pytest.mark.parametrize("watcher", sorted(_WATCHERS_THAT_MUST_CHECK_DISPATCH_MARKER))
def test_watcher_calls_has_active_dispatch_marker(watcher: str) -> None:
    """Each watcher in the set must call _has_active_dispatch_marker."""
    callers_race = _functions_calling_predicate(_PROCESS_RACE, "_has_active_dispatch_marker")
    callers_monitor = _functions_calling_predicate(_PROCESS_MONITOR, "_has_active_dispatch_marker")
    all_callers = callers_race | callers_monitor
    assert watcher in all_callers, (
        f"{watcher} does not call _has_active_dispatch_marker. "
        f"Functions that do: {sorted(all_callers)}"
    )
