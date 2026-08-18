"""AST guard: both boot functions must call all required boot steps in the right order."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

LIFESPAN_PKG = Path(__file__).parents[2] / "src" / "autoskillit" / "server" / "_lifespan"
KITCHEN_PKG = (
    Path(__file__).parents[2] / "src" / "autoskillit" / "server" / "tools" / "tools_kitchen"
)

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
    ("DISPATCH_ID_ENV_VAR", ("_food_truck_auto_gate_boot",)),
    (
        "sweep_orphaned_tethers_async",
        ("_fleet_auto_gate_boot", "_food_truck_auto_gate_boot", "_skill_auto_gate_boot"),
    ),
    (
        "_reap_self_excluded_codex_and_daemon_orphans",
        ("_fleet_auto_gate_boot", "_food_truck_auto_gate_boot"),
    ),
]

_BOOT_STEP_ORDERING: list[tuple[str, str, tuple[str, ...]]] = [
    (
        "reap_stale_dispatches_async",
        "sweep_stale_dispatch_labels",
        ("_fleet_auto_gate_boot", "_food_truck_auto_gate_boot"),
    ),
]


def _references_symbol(node: ast.expr, symbol: str) -> bool:
    """True if node is a bare ``symbol`` reference or a ``pkg.symbol`` attribute access.

    The decomposed packages route several facade-patched symbols through a
    self-import (``from autoskillit.server.tools import tools_kitchen as _tk_pkg``)
    and call them as ``_tk_pkg.symbol(...)`` instead of a bare name, so both
    forms count as the boot step being present.
    """
    if isinstance(node, ast.Name):
        return node.id == symbol
    return isinstance(node, ast.Attribute) and node.attr == symbol


def _function_body_contains_symbol(tree: ast.Module, func_name: str, symbol: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == func_name:
            for child in ast.walk(node):
                if isinstance(child, (ast.Name, ast.Attribute)) and _references_symbol(
                    child, symbol
                ):
                    return True
    return False


def _first_symbol_line(func_node: ast.AsyncFunctionDef, symbol: str) -> int | None:
    """Return the line number of the first bare-name or pkg.symbol reference in func_node.body."""
    for stmt in func_node.body:
        for child in ast.walk(stmt):
            if isinstance(child, (ast.Name, ast.Attribute)) and _references_symbol(child, symbol):
                return stmt.lineno
    return None


class TestBootStepSymmetry:
    @pytest.mark.parametrize("symbol,boot_functions", _REQUIRED_BOOT_STEPS)
    def test_boot_functions_contain_required_step(
        self, symbol: str, boot_functions: tuple[str, ...]
    ) -> None:
        assert LIFESPAN_PKG.exists(), f"Production package not found: {LIFESPAN_PKG}"
        source = ""
        for py in sorted(LIFESPAN_PKG.rglob("*.py")):
            source += py.read_text(encoding="utf-8")
        tree = ast.parse(source)

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
        assert LIFESPAN_PKG.exists(), f"Production package not found: {LIFESPAN_PKG}"
        source = ""
        for py in sorted(LIFESPAN_PKG.rglob("*.py")):
            source += py.read_text(encoding="utf-8")
        tree = ast.parse(source)

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

    def test_open_kitchen_handler_calls_reaper(self) -> None:
        """AST guard: _open_kitchen_handler must call reap_stale_dispatches_async.

        Interactive sessions need the reaper at kitchen-open time since they
        never go through _fleet_auto_gate_boot or _food_truck_auto_gate_boot.
        """
        assert KITCHEN_PKG.exists(), f"Production package not found: {KITCHEN_PKG}"
        source = ""
        for py in sorted(KITCHEN_PKG.rglob("*.py")):
            source += py.read_text(encoding="utf-8")
        tree = ast.parse(source)
        assert _function_body_contains_symbol(
            tree, "_open_kitchen_handler", "reap_stale_dispatches_async"
        ), (
            "_open_kitchen_handler must call reap_stale_dispatches_async "
            "to provide dispatch recovery for interactive sessions"
        )

    def test_open_kitchen_handler_calls_tether_sweep(self) -> None:
        """AST guard: _open_kitchen_handler must sweep orphaned tethers.

        Interactive sessions never go through the lifespan boot gates, so
        open_kitchen is the only chokepoint they hit — same rationale as the
        stale-dispatch reap requirement above.
        """
        assert KITCHEN_PKG.exists(), f"Production package not found: {KITCHEN_PKG}"
        source = ""
        for py in sorted(KITCHEN_PKG.rglob("*.py")):
            source += py.read_text(encoding="utf-8")
        tree = ast.parse(source)
        assert _function_body_contains_symbol(
            tree, "_open_kitchen_handler", "sweep_orphaned_tethers_async"
        ), "_open_kitchen_handler must call sweep_orphaned_tethers_async"
