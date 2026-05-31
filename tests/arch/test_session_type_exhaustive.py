"""Arch guard: _apply_session_type_visibility must use exhaustive match/assert_never."""

from __future__ import annotations

import ast

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


class _MatchWithAssertNeverVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.has_match = False
        self.has_assert_never = False

    def visit_Match(self, node: ast.Match) -> None:
        self.has_match = True
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == "assert_never":
            self.has_assert_never = True
        self.generic_visit(node)


def test_session_type_dispatch_is_exhaustive():
    """_apply_session_type_visibility must use match/assert_never for exhaustive dispatch."""
    from autoskillit.core import paths

    src_file = paths.pkg_root() / "server" / "_session_type.py"
    assert src_file.exists(), f"File not found: {src_file}"
    tree = ast.parse(src_file.read_text())

    fn_body: list[ast.stmt] | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_apply_session_type_visibility":
            fn_body = node.body
            break

    assert fn_body is not None, "_apply_session_type_visibility not found in _session_type.py"

    fn_tree = ast.Module(body=fn_body, type_ignores=[])
    visitor = _MatchWithAssertNeverVisitor()
    visitor.visit(fn_tree)

    assert visitor.has_match, (
        "_apply_session_type_visibility must use a 'match' statement for session type dispatch. "
        "Replace if/elif chain with match/case/assert_never for exhaustive enum coverage."
    )
    assert visitor.has_assert_never, (
        "_apply_session_type_visibility must call assert_never() in the fallthrough case "
        "to guard against unhandled SessionType members."
    )
