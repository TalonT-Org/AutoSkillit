"""Quota decision producers must call the shared cumulative fold."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.medium

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
            and (isinstance(call.func, ast.Name) and call.func.id == "effective_quota_block")
            for call in ast.walk(node)
        ):
            found.add(node.name)
    return found


def test_quota_gate_inventory_is_closed() -> None:
    src_root = Path(__file__).parents[2] / "src" / "autoskillit"
    observed = []
    for relative, function_name in _EXPECTED_QUOTA_GATES:
        callers = _functions_calling_fold((src_root / relative).read_text())
        assert function_name in callers
        observed.append((relative, function_name))
    assert tuple(observed) == _EXPECTED_QUOTA_GATES


def test_detector_canary_requires_direct_fold_call() -> None:
    assert _functions_calling_fold(
        "def gate():\n    return effective_quota_block([], account_scope='x', now_epoch=0)\n"
    ) == {"gate"}
    assert _functions_calling_fold("def gate():\n    return None\n") == set()
