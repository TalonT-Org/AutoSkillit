"""Arch test: every @mcp.tool() decorator must include an ``annotations=`` keyword.

Layer 1 of the three-layer annotation test shield. Uses AST scanning (no import)
so it catches missing annotations before the server is even started.

Follows the pattern in test_doc_counts.py and test_ast_rules.py.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SERVER_DIR = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "server"


def _tools_files() -> list[Path]:
    return sorted(_SERVER_DIR.glob("tools_*.py"))


def _collect_missing_annotations(path: Path) -> list[tuple[str, int]]:
    """Return (func_name, lineno) for each @mcp.tool() decorator missing ``annotations=``."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    missing: list[tuple[str, int]] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            # Match @mcp.tool(...) calls only (bare @mcp.tool with no parens has no keywords)
            if not (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "tool"
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "mcp"
            ):
                continue
            has_annotations = any(kw.arg == "annotations" for kw in dec.keywords)
            if not has_annotations:
                missing.append((node.name, dec.lineno))

    return missing


class TestToolAnnotationCompleteness:
    """Every @mcp.tool() decorator in server/tools_*.py must declare annotations=."""

    def test_all_mcp_tools_have_annotations_keyword(self):
        """AST scan: each @mcp.tool(...) must include the annotations= keyword argument.

        This catches tools that omit readOnlyHint entirely, which causes them to
        have no annotation on the wire even when the middleware is fixed.
        """
        violations: list[str] = []
        for path in _tools_files():
            for func_name, lineno in _collect_missing_annotations(path):
                violations.append(
                    f"{path.name}:{lineno}: {func_name!r} is missing annotations= in @mcp.tool()"
                )

        assert not violations, (
            "The following @mcp.tool() decorators are missing the annotations= keyword.\n"
            "Add annotations={'readOnlyHint': True/False} to each:\n\n"
            + "\n".join(f"  {v}" for v in violations)
        )
