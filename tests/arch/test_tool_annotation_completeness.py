"""AST annotation test shield for MCP tool readOnlyHint semantics.

Layer 1a — AST presence: every @mcp.tool() has annotations= keyword.
Layer 1b — AST value: every annotations= has readOnlyHint=True (no import).

Runtime layers (2-4) live in tests/server/test_tool_annotation_completeness.py.
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
            "Add annotations={'readOnlyHint': True} to each:\n\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_all_annotations_are_readonly_true(self):
        """AST scan: readOnlyHint must be True in every @mcp.tool() decorator.

        Catches the bug at AST level (no server import needed) by inspecting
        the literal value passed to annotations={"readOnlyHint": ...}.
        """
        violations: list[str] = []
        for path in _tools_files():
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for dec in node.decorator_list:
                    if not (
                        isinstance(dec, ast.Call)
                        and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr == "tool"
                        and isinstance(dec.func.value, ast.Name)
                        and dec.func.value.id == "mcp"
                    ):
                        continue
                    for kw in dec.keywords:
                        if kw.arg != "annotations":
                            continue
                        if not isinstance(kw.value, ast.Dict):
                            continue
                        for key, val in zip(kw.value.keys, kw.value.values):
                            if (
                                isinstance(key, ast.Constant)
                                and key.value == "readOnlyHint"
                                and isinstance(val, ast.Constant)
                                and val.value is not True
                            ):
                                violations.append(
                                    f"{path.name}:{dec.lineno}: {node.name!r} "
                                    f"has readOnlyHint={val.value!r} (must be True)"
                                )

        assert not violations, (
            "readOnlyHint must be True for all tools. "
            "All pipelines use independent branches/worktrees.\n\n"
            + "\n".join(f"  {v}" for v in violations)
        )
