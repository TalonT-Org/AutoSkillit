"""Quota decision producers must call the shared cumulative fold."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.medium

# Gate functions must reach effective_quota_block through the shared helper
# (decide_quota_block in quota_constraints.py) so that the cumulative fold
# logic lives in exactly one place. Direct calls to effective_quota_block from
# any gate file are also accepted as a transitional case but the helper is the
# preferred path.
_DECISION_HELPERS = frozenset({"decide_quota_block", "effective_quota_block"})

_EXPECTED_QUOTA_GATES = (
    ("execution/quota.py", "check_and_sleep_if_needed"),
    ("hooks/guards/quota_guard.py", "quota_guard_decision"),
    ("hooks/quota_post_hook.py", "quota_post_decision"),
)


def _functions_calling_fold(source: str) -> set[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id in _DECISION_HELPERS
            for call in ast.walk(node)
        ):
            found.add(node.name)
    return found


def _all_callers_of_fold(source: str) -> set[tuple[str, str]]:
    """Return (file_relpath, function_name) for every function that calls a fold helper."""
    tree = ast.parse(source)
    found: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id in _DECISION_HELPERS
            for call in ast.walk(node)
        ):
            found.add(("", node.name))
    return found


def test_quota_gate_inventory_is_closed() -> None:
    src_root = Path(__file__).parents[2] / "src" / "autoskillit"
    observed = []
    for relative, function_name in _EXPECTED_QUOTA_GATES:
        callers = _functions_calling_fold((src_root / relative).read_text())
        assert function_name in callers, (
            f"expected {function_name} in {relative} to call a fold helper"
        )
        observed.append((relative, function_name))
    assert tuple(observed) == _EXPECTED_QUOTA_GATES


def test_no_unexpected_quota_gate_callers() -> None:
    """No function outside the expected gate set may call the fold helper."""
    src_root = Path(__file__).parents[2] / "src" / "autoskillit"
    expected_callers = {relative + "::" + name for relative, name in _EXPECTED_QUOTA_GATES}
    # Scan every .py under execution/ and hooks/ for callers of the fold helpers.
    mismatch: list[str] = []
    for root_dir in ("execution", "hooks"):
        for path in (src_root / root_dir).rglob("*.py"):
            if path.name == "__init__.py":
                continue
            callers = _all_callers_of_fold(path.read_text())
            for _, function_name in callers:
                rel = path.relative_to(src_root).as_posix()
                identifier = f"{rel}::{function_name}"
                if identifier not in expected_callers:
                    # Allow calls inside quota_constraints.py — it owns the helpers.
                    if path.name == "quota_constraints.py":
                        continue
                    mismatch.append(identifier)
    assert not mismatch, "Unexpected callers of the quota fold helper detected: " + ", ".join(
        sorted(mismatch)
    )


def test_detector_canary_requires_direct_fold_call() -> None:
    assert _functions_calling_fold(
        "def gate():\n    return decide_quota_block('cache', account_scope='x', "
        "read_cache=lambda *_: None, cache_max_age=0, now_epoch=0)\n"
    ) == {"gate"}
    assert _functions_calling_fold("def gate():\n    return None\n") == set()
