"""Shared helpers for structural guard tests (split-guard test files)."""

from __future__ import annotations

import ast
from pathlib import Path


def _has_pytestmark_cli(path: Path) -> bool:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "pytestmark":
                    src = ast.unparse(node.value)
                    return 'layer("cli")' in src or "layer('cli')" in src
    return False
