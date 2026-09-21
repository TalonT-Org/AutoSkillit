"""AST guard: no inline requestId dedup outside the parent-turn authority.

Both files previously contained independent first-occurrence-wins dedup logic using
a local `seen_request_ids` set. The dedup key resolution is centralised in
`_resolve_turn_id()` (called by `iter_merged_assistant_turns()`) in
`_parent_assistant_turns.py`. This guard prevents regression.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

SRC = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
SESSION_LOG = SRC / "execution" / "session_log" / "session_log.py"
TURN_AUTHORITY = SRC / "_parent_assistant_turns.py"


def _function_scoped_names(tree: ast.AST, name: str) -> list[int]:
    """Return line numbers of assignments to `name` inside function bodies."""
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    if isinstance(target, ast.Name) and target.id == name:
                        hits.append(child.lineno)
            elif isinstance(child, ast.AnnAssign):
                if isinstance(child.target, ast.Name) and child.target.id == name:
                    hits.append(child.lineno)
    return hits


class TestNoInlineJsonlRequestIdDedup:
    def test_session_log_has_no_seen_request_ids_variable(self) -> None:
        tree = ast.parse(SESSION_LOG.read_text(encoding="utf-8"))
        hits = _function_scoped_names(tree, "seen_request_ids")
        assert not hits, (
            "execution/session_log/session_log.py re-introduced an inline requestId dedup set.\n"
            "Use _resolve_turn_id() in _parent_assistant_turns.py instead.\n"
            "Offending lines: " + ", ".join(str(ln) for ln in hits)
        )

    def test_parent_turn_authority_has_no_seen_request_ids_variable(self) -> None:
        tree = ast.parse(TURN_AUTHORITY.read_text(encoding="utf-8"))
        hits = _function_scoped_names(tree, "seen_request_ids")
        assert not hits, (
            "_parent_assistant_turns.py re-introduced an inline requestId dedup set.\n"
            "Use _resolve_turn_id() (called by iter_merged_assistant_turns()) instead.\n"
            "Offending lines: " + ", ".join(str(ln) for ln in hits)
        )
