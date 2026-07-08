"""Structural tests for SERVE_SURFACES frozenset and load_and_validate call-site enforcement.

AST guard: load_and_validate must only be called from _serve_helpers.py within server/tools/.
SERVE_SURFACES membership: all four surfaces registered, no phantom entries.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.small]

_TOOLS_DIR = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "server" / "tools"


def _find_load_and_validate_call_files() -> set[str]:
    """Return basenames of files under server/tools/ that call load_and_validate."""
    offenders: set[str] = set()
    for py_file in _TOOLS_DIR.glob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # Match ctx.recipes.load_and_validate(...) or recipes.load_and_validate(...)
            if isinstance(func, ast.Attribute) and func.attr == "load_and_validate":
                offenders.add(py_file.name)
                break
    return offenders


def test_serve_surfaces_registry_is_exhaustive() -> None:
    """load_and_validate must only be called from _serve_helpers.py in server/tools/.

    tools_fleet_dispatch.py is exempt: its fleet-dispatch preflight call is Part B scope
    (issue #4208 Part B — fleet dispatch resolved_defaults gap). It is intentionally
    excluded from the Part A enforcement perimeter.
    """
    call_files = _find_load_and_validate_call_files()
    # _serve_helpers.py is the canonical call site; tools_fleet_dispatch.py is a
    # known Part B gap (fleet dispatch resolved_defaults — excluded from Part A).
    allowed = {"_serve_helpers.py", "tools_fleet_dispatch.py"}
    violations = call_files - allowed
    assert not violations, (
        f"load_and_validate called directly outside _serve_helpers.py in server/tools/: "
        f"{sorted(violations)}. "
        "All serve surfaces must call serve_recipe() from _serve_helpers.py instead."
    )


def test_serve_surfaces_frozenset_defined() -> None:
    """SERVE_SURFACES must be importable from autoskillit.core and be a frozenset."""
    from autoskillit.core import SERVE_SURFACES

    assert isinstance(SERVE_SURFACES, frozenset), (
        f"SERVE_SURFACES must be a frozenset, got {type(SERVE_SURFACES)}"
    )


def test_serve_surfaces_contains_expected_members() -> None:
    """SERVE_SURFACES must list all four known serve surfaces."""
    from autoskillit.core import SERVE_SURFACES

    expected = {
        "open_kitchen",
        "open_kitchen_deferred_recall",
        "load_recipe",
        "get_recipe",
    }
    assert expected == SERVE_SURFACES, (
        f"SERVE_SURFACES mismatch. "
        f"Missing: {expected - SERVE_SURFACES}. "
        f"Extra: {SERVE_SURFACES - expected}."
    )
