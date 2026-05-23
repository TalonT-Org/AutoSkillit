"""AST guard: both boot functions must call sweep_stale_dispatch_labels."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

LIFESPAN_PATH = Path(__file__).parents[2] / "src" / "autoskillit" / "server" / "_lifespan.py"

_SWEEP_SYMBOL = "sweep_stale_dispatch_labels"
_BOOT_FUNCTIONS = ("_fleet_auto_gate_boot", "_food_truck_auto_gate_boot")


def _function_body_contains_symbol(tree: ast.Module, func_name: str, symbol: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == func_name:
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and child.id == symbol:
                    return True
    return False


class TestBootStepSymmetry:
    def test_boot_functions_both_run_label_sweep(self) -> None:
        assert LIFESPAN_PATH.exists(), f"Production file not found: {LIFESPAN_PATH}"
        tree = ast.parse(LIFESPAN_PATH.read_text())

        missing: list[str] = []
        for func_name in _BOOT_FUNCTIONS:
            if not _function_body_contains_symbol(tree, func_name, _SWEEP_SYMBOL):
                missing.append(func_name)

        assert not missing, (
            f"Boot function(s) missing '{_SWEEP_SYMBOL}' call: {missing}. "
            "Both _fleet_auto_gate_boot and _food_truck_auto_gate_boot must call "
            "sweep_stale_dispatch_labels to maintain boot step symmetry."
        )
