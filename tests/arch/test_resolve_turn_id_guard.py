"""AST guard: _resolve_turn_id is the sole turn ID resolution point.

No function other than _resolve_turn_id in tool_sequence_analysis.py may
call .get("requestId", ...). Direct requestId access bypasses the provider-
agnostic resolution chain and re-introduces the MiniMax dedup regression.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

SRC = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
TOOL_SEQ = SRC / "core" / "tool_sequence_analysis.py"


def _direct_request_id_get_lines(tree: ast.AST) -> list[tuple[str, int]]:
    """Return (func_name, lineno) for .get("requestId") calls outside _resolve_turn_id."""
    hits: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name == "_resolve_turn_id":
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            func = child.func
            if not (isinstance(func, ast.Attribute) and func.attr == "get"):
                continue
            args = child.args
            if not args:
                continue
            first = args[0]
            if isinstance(first, ast.Constant) and first.value == "requestId":
                hits.append((node.name, child.lineno))
    return hits


class TestResolveTurnIdGuard:
    def test_no_direct_request_id_get_outside_resolver(self) -> None:
        tree = ast.parse(TOOL_SEQ.read_text(encoding="utf-8"))
        hits = _direct_request_id_get_lines(tree)
        assert not hits, (
            'core/tool_sequence_analysis.py contains direct .get("requestId") '
            "calls outside _resolve_turn_id().\n"
            "Use _resolve_turn_id() (called by iter_merged_assistant_turns()) instead.\n"
            "Offending (function, line): " + ", ".join(f"{fn}:{ln}" for fn, ln in hits)
        )
