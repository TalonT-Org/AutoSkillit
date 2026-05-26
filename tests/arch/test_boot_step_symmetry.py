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
    (
        "register_active_kitchen",
        ("_fleet_auto_gate_boot", "_food_truck_auto_gate_boot", "_skill_auto_gate_boot"),
    ),
    (
        "_write_hook_config",
        ("_fleet_auto_gate_boot", "_food_truck_auto_gate_boot", "_skill_auto_gate_boot"),
    ),
    (
        "_prime_quota_cache",
        ("_fleet_auto_gate_boot", "_food_truck_auto_gate_boot", "_skill_auto_gate_boot"),
    ),
]

_BOOT_STEP_ORDERING: list[tuple[str, str, tuple[str, ...]]] = [
    (
        "reap_stale_dispatches_async",
        "sweep_stale_dispatch_labels",
        ("_fleet_auto_gate_boot", "_food_truck_auto_gate_boot"),
    ),
]


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
            f"Both {' and '.join(boot_functions)} must call {symbol} "
            "to maintain boot step symmetry."
        )

    @pytest.mark.parametrize("before_symbol,after_symbol,boot_functions", _BOOT_STEP_ORDERING)
    def test_boot_step_ordering(
        self, before_symbol: str, after_symbol: str, boot_functions: tuple[str, ...]
    ) -> None:
        assert LIFESPAN_PATH.exists(), f"Production file not found: {LIFESPAN_PATH}"
        tree = ast.parse(LIFESPAN_PATH.read_text())

        for node in ast.walk(tree):
            if not (isinstance(node, ast.AsyncFunctionDef) and node.name in boot_functions):
                continue

            before_lineno = _first_symbol_line(node, before_symbol)
            after_lineno = _first_symbol_line(node, after_symbol)

            assert before_lineno is not None, (
                f"{node.name}: '{before_symbol}' not found in function body"
            )
            assert after_lineno is not None, (
                f"{node.name}: '{after_symbol}' not found in function body"
            )
            assert before_lineno < after_lineno, (
                f"{node.name}: {before_symbol} (line {before_lineno}) must appear "
                f"before {after_symbol} (line {after_lineno})"
            )
