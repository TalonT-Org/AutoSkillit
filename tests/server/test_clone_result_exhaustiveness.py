"""Structural test to enforce exhaustive CloneResult gate handling."""

from __future__ import annotations

import ast
import inspect

import pytest

from autoskillit.core.types import CloneGateUncommitted, CloneGateUnpublished
from autoskillit.server.tools.tools_clone import _require_clone_success

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]

GATE_DISCRIMINANTS: dict[type, str] = {
    CloneGateUncommitted: "uncommitted_changes",
    CloneGateUnpublished: "unpublished_branch",
}


def test_require_clone_success_handles_all_gate_variants():
    """Every gate variant's discriminant key must be checked."""
    source = inspect.getsource(_require_clone_success)
    tree = ast.parse(source)
    checked_keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for op in node.ops:
                if isinstance(op, ast.In):
                    if isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
                        checked_keys.add(node.left.value)

    expected = set(GATE_DISCRIMINANTS.values())
    missing = expected - checked_keys
    assert not missing, (
        f"_require_clone_success does not check for gate discriminant keys: {missing}. "
        f"A new CloneResult gate variant was added without updating the helper."
    )
