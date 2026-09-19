"""Inventory the exploration authority's operational exception boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_ROOT = Path(__file__).parents[2]
_PATHS = (
    _ROOT / "src/autoskillit/pipeline/exploration_context/_store.py",
    _ROOT / "src/autoskillit/pipeline/exploration_context_durable.py",
)
_ARGUMENT_VALIDATION_RAISES = {
    "_store.py": {133, 135, 179, 207, 209, 211, 264, 271},
    "exploration_context_durable.py": {144},
}


def test_operational_store_raises_use_registered_exception_types() -> None:
    offenders: list[str] = []
    for path in _PATHS:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                continue
            if node.lineno in _ARGUMENT_VALIDATION_RAISES.get(path.name, set()):
                continue
            callee = node.exc.func
            if isinstance(callee, ast.Name) and callee.id in {"RuntimeError", "ValueError"}:
                offenders.append(f"{path.name}:{node.lineno}:{callee.id}")

    assert not offenders, "bare operational exploration errors: " + ", ".join(offenders)
