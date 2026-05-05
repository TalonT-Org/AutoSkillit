"""AST guard: no inline requestId dedup in session_log.py or tool_sequence_analysis.py.

Both files previously contained independent first-occurrence-wins dedup logic using
a local `seen_request_ids` set. The dedup is now centralised in
`iter_merged_assistant_turns()`. This guard prevents regression.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
SESSION_LOG = SRC / "execution" / "session_log.py"
TOOL_SEQ = SRC / "core" / "tool_sequence_analysis.py"


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
            "execution/session_log.py re-introduced an inline requestId dedup set.\n"
            "Use iter_merged_assistant_turns() instead.\n"
            "Offending lines: " + ", ".join(str(ln) for ln in hits)
        )

    def test_tool_sequence_analysis_has_no_seen_request_ids_variable(self) -> None:
        tree = ast.parse(TOOL_SEQ.read_text(encoding="utf-8"))
        hits = _function_scoped_names(tree, "seen_request_ids")
        assert not hits, (
            "core/tool_sequence_analysis.py re-introduced an inline requestId dedup set.\n"
            "Use iter_merged_assistant_turns() instead.\n"
            "Offending lines: " + ", ".join(str(ln) for ln in hits)
        )
