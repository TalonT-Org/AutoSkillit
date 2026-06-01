"""Regression guard: _dag_ops must not eagerly import networkx at module level."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import autoskillit.planner._dag_ops as _dag_ops_mod

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def test_dag_ops_no_toplevel_networkx_import():
    """Top-level 'import networkx' in _dag_ops.py would add 17 MB per xdist worker."""
    src_file = Path(_dag_ops_mod.__file__)
    tree = ast.parse(src_file.read_text())
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "networkx", (
                    f"Top-level 'import networkx' found at line {node.lineno}; "
                    "must be a function-level import inside find_sccs()"
                )
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "networkx":
            assert False, (
                f"Top-level 'from {node.module} import ...' found at line {node.lineno}; "
                "networkx must only be imported inside find_sccs()"
            )
