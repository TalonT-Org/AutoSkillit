"""Structural guard: conftest.py must not import merge_queue at module level."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_execution_conftest_has_no_merge_queue_imports():
    """conftest.py must not import merge_queue symbols at module level."""
    src = (Path(__file__).parent / "conftest.py").read_text()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = getattr(node, "module", "") or ""
            assert "merge_queue" not in module, (
                f"conftest.py imports from merge_queue at line {node.lineno}; "
                "move these to tests/execution/_merge_queue_helpers.py"
            )
