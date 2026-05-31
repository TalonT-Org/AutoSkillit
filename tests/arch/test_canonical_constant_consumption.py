"""Architectural invariant: every env-var-set constant must have a production consumer."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_ENV_CANONICAL_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*(?:_ENV_FORWARD_VARS|_REQUIRED_ENV)$")


def _find_env_set_constants(constants_file: Path) -> list[str]:
    """Find all module-level names matching env-var-set patterns in the constants file."""
    tree = ast.parse(constants_file.read_text())
    names: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if _ENV_CANONICAL_PATTERN.match(node.target.id):
                names.append(node.target.id)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and _ENV_CANONICAL_PATTERN.match(target.id):
                    names.append(target.id)
    return names


def _has_production_import(src_root: Path, constant_name: str, definition_file: Path) -> bool:
    """Check if any production file (excluding the definition) imports the constant."""
    for py_file in src_root.rglob("*.py"):
        if py_file == definition_file:
            continue
        if "test" in py_file.name:
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    actual_name = alias.asname if alias.asname else alias.name
                    if actual_name == constant_name or alias.name == constant_name:
                        return True
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == constant_name:
                        return True
    return False


def test_env_forward_constants_have_production_consumer() -> None:
    """Every env-var-set constant must be imported by at least one production module."""
    from autoskillit.core import paths

    src_root = paths.pkg_root()
    constants_file = src_root / "core" / "types" / "_type_constants_env.py"
    constants = _find_env_set_constants(constants_file)
    assert constants, "No env-var-set constants found — test premise broken"

    unconsumed = [
        name for name in constants if not _has_production_import(src_root, name, constants_file)
    ]
    assert not unconsumed, (
        f"Env-var-set constants (*_ENV_FORWARD_VARS / *_REQUIRED_ENV) with zero production "
        f"consumers: {unconsumed}. Each env-var-set constant must be imported and consumed "
        f"by production code to prevent dead-canonical-constant drift."
    )
