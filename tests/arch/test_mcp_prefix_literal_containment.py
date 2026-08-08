"""MCP prefix literals must be confined to the single canonical definition module."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoskillit.core import paths

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_CONFINED_LITERALS = frozenset(
    {
        "mcp__autoskillit__",
        "mcp__plugin_autoskillit_autoskillit__",
    }
)

_CANONICAL_MODULE = "core/_plugin_ids.py"


def _string_literals_in_module(path: Path) -> list[tuple[int, str]]:
    """Return (lineno, value) for every string constant in *path*."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return []
    results: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            results.append((node.lineno, node.value))
        elif isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    results.append((part.lineno, part.value))
    return results


def test_mcp_prefix_literals_confined_to_canonical_module() -> None:
    """The string literals for both MCP prefix forms appear only in _plugin_ids.py."""
    src_root = paths.pkg_root()
    violations: list[str] = []
    for py_file in sorted(src_root.rglob("*.py")):
        rel = py_file.relative_to(src_root).as_posix()
        if rel == _CANONICAL_MODULE:
            continue
        for lineno, value in _string_literals_in_module(py_file):
            for literal in _CONFINED_LITERALS:
                if literal in value:
                    violations.append(f"{rel}:{lineno}: contains {literal!r} in {value!r}")
    assert not violations, (
        "MCP prefix literals must appear only in "
        f"{_CANONICAL_MODULE}. Violations:\n" + "\n".join(violations)
    )
