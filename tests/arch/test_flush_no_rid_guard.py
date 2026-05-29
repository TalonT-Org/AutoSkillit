"""AST guard: no requestId truthiness guard in flush_session_log turn extraction loop.

iter_merged_assistant_turns() now produces synthetic IDs for no-requestId turns,
so all turns have a non-empty request_id. Filtering on _turn.request_id truthiness
inside flush_session_log would silently drop non-Anthropic turns. This guard
prevents regression.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

SRC = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
SESSION_LOG = SRC / "execution" / "session_log.py"


def _find_flush_session_log(tree: ast.AST) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "flush_session_log":
                return node  # type: ignore[return-value]
    return None


def _iter_merged_loop_bodies(func: ast.FunctionDef) -> list[ast.stmt]:
    """Return the body statements of for-loops iterating iter_merged_assistant_turns."""
    bodies: list[ast.stmt] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.For):
            continue
        iter_node = node.iter
        call = None
        if isinstance(iter_node, ast.Call):
            call = iter_node
        if call is None:
            continue
        func_node = call.func
        name = ""
        if isinstance(func_node, ast.Name):
            name = func_node.id
        elif isinstance(func_node, ast.Attribute):
            name = func_node.attr
        if name == "iter_merged_assistant_turns":
            bodies.extend(node.body)
    return bodies


def _has_request_id_truthiness_guard(stmts: list[ast.stmt]) -> list[int]:
    """Return line numbers of if-statements guarding on _turn.request_id truthiness."""
    hits: list[int] = []
    for stmt in stmts:
        if not isinstance(stmt, ast.If):
            continue
        test = stmt.test
        # Match: `if not _turn.request_id:` → UnaryOp(Not, Attribute(request_id))
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            operand = test.operand
            if isinstance(operand, ast.Attribute) and operand.attr == "request_id":
                hits.append(stmt.lineno)
        # Match: `if _turn.request_id:` with continue body — also detect positive guard
        elif isinstance(test, ast.Attribute) and test.attr == "request_id":
            body_has_continue = any(isinstance(s, ast.Continue) for s in stmt.body)
            if body_has_continue:
                hits.append(stmt.lineno)
    return hits


class TestFlushNoRidGuard:
    def test_flush_turn_loop_has_no_request_id_guard(self) -> None:
        tree = ast.parse(SESSION_LOG.read_text(encoding="utf-8"))
        flush_func = _find_flush_session_log(tree)
        assert flush_func is not None, "flush_session_log not found in session_log.py"
        loop_bodies = _iter_merged_loop_bodies(flush_func)
        assert loop_bodies, (
            "No for-loop over iter_merged_assistant_turns found in flush_session_log. "
            "Was the turn extraction loop removed or renamed?"
        )
        hits = _has_request_id_truthiness_guard(loop_bodies)
        assert not hits, (
            "flush_session_log re-introduced a request_id truthiness guard in the "
            "iter_merged_assistant_turns loop. This silently drops non-Anthropic turns.\n"
            "iter_merged_assistant_turns() now assigns synthetic turn-N IDs so all turns "
            "have a non-empty request_id — remove the guard entirely.\n"
            "Offending lines: " + ", ".join(str(ln) for ln in hits)
        )
