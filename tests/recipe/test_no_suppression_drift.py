"""Anti-regression: prevent reintroduction of validation suppression dicts."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_conftest_has_no_known_violations_dict() -> None:
    """Guard against reintroduction of validation suppression dicts.

    KNOWN_VIOLATIONS_BY_RECIPE was a temporary migration mechanism.
    Its reintroduction would recreate the test/production divergence.
    """
    conftest = Path(__file__).parent / "conftest.py"
    assert conftest.exists(), f"conftest.py not found at {conftest} — guard cannot run"
    tree = ast.parse(conftest.read_text())
    violation_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and node.targets and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if "VIOLATION" in name.upper():
                violation_names.add(name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            if "VIOLATION" in name.upper():
                violation_names.add(name)
    assert not violation_names, (
        f"Suppression mechanism found in conftest.py: {violation_names}. "
        "The dispatch gate test must mirror production with zero suppression. "
        "Fix the violations instead of suppressing them."
    )
