"""Tests for rules_inputs.py structural contracts."""

from __future__ import annotations

import ast
import pathlib


def test_rules_inputs_terminal_targets_match_schema():
    """rules_inputs.py unreachable-step rule uses the same sentinel set as schema."""
    from autoskillit.recipe.schema import _TERMINAL_TARGETS  # noqa: PLC0415

    # Verify schema has the expected sentinels (belt-and-suspenders check).
    assert "done" in _TERMINAL_TARGETS
    assert "escalate" in _TERMINAL_TARGETS

    # Structural check: rules_inputs.py must NOT hardcode sentinel strings via
    # .discard("done") or .discard("escalate"). It must use _TERMINAL_TARGETS instead.
    src_path = (
        pathlib.Path(__file__).parent.parent.parent / "src/autoskillit/recipe/rules_inputs.py"
    )
    src = src_path.read_text()
    tree = ast.parse(src)
    hardcoded_discards = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "discard"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value in ("escalate", "done")
    ]
    assert hardcoded_discards == [], (
        f"rules_inputs.py hardcodes {len(hardcoded_discards)} sentinel string(s) via "
        ".discard(). Use _TERMINAL_TARGETS from schema instead."
    )
