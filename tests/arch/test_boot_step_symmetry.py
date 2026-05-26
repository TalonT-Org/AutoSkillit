"""AST guard: both boot functions must call all required boot steps in the right order."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

LIFESPAN_PATH = Path(__file__).parents[2] / "src" / "autoskillit" / "server" / "_lifespan.py"

_REQUIRED_BOOT_STEPS: list[tuple[str, tuple[str, ...]]] = [
    ("sweep_stale_dispatch_labels", ("_fleet_auto_gate_boot", "_food_truck_auto_gate_boot")),
    ("reap_stale_dispatches_async", ("_fleet_auto_gate_boot", "_food_truck_auto_gate_boot")),
]

_BOOT_FUNCTIONS = ("_fleet_auto_gate_boot", "_food_truck_auto_gate_boot")


def _function_body_contains_symbol(tree: ast.Module, func_name: str, symbol: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == func_name:
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and child.id == symbol:
                    return True
    return False


def _first_symbol_line(func_node: ast.AsyncFunctionDef, symbol: str) -> int | None:
    """Return the line number of the first ast.Name reference to symbol in func_node.body."""
    for stmt in func_node.body:
        for child in ast.walk(stmt):
            if isinstance(child, ast.Name) and child.id == symbol:
                return stmt.lineno
    return None


class TestBootStepSymmetry:
    @pytest.mark.parametrize("symbol,boot_functions", _REQUIRED_BOOT_STEPS)
    def test_boot_functions_contain_required_step(
        self, symbol: str, boot_functions: tuple[str, ...]
    ) -> None:
        assert LIFESPAN_PATH.exists(), f"Production file not found: {LIFESPAN_PATH}"
        tree = ast.parse(LIFESPAN_PATH.read_text())

        missing: list[str] = []
        for func_name in boot_functions:
            if not _function_body_contains_symbol(tree, func_name, symbol):
                missing.append(func_name)

        assert not missing, (
            f"Boot function(s) missing '{symbol}' call: {missing}. "
            f"Both {' and '.join(boot_functions)} must call {symbol} to maintain boot step symmetry."
        )

    def test_reap_before_sweep_in_boot_functions(self) -> None:
        assert LIFESPAN_PATH.exists(), f"Production file not found: {LIFESPAN_PATH}"
        tree = ast.parse(LIFESPAN_PATH.read_text())

        for node in ast.walk(tree):
            if not (isinstance(node, ast.AsyncFunctionDef) and node.name in _BOOT_FUNCTIONS):
                continue

            reap_line = _first_symbol_line(node, "reap_stale_dispatches_async")
            sweep_line = _first_symbol_line(node, "sweep_stale_dispatch_labels")

            assert reap_line is not None, (
                f"{node.name}: 'reap_stale_dispatches_async' not found in function body"
            )
            assert sweep_line is not None, (
                f"{node.name}: 'sweep_stale_dispatch_labels' not found in function body"
            )
            assert reap_line < sweep_line, (
                f"{node.name}: reap_stale_dispatches_async (line {reap_line}) must appear "
                f"before sweep_stale_dispatch_labels (line {sweep_line})"
            )
