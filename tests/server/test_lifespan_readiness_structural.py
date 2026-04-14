"""AST structural guard for _autoskillit_lifespan readiness invariants.

Fails RED if:
  A) the first statement in _autoskillit_lifespan is not a try: block
  B) the try: body contains no call to a sentinel/readiness write helper
  C) any logger call uses a retired readiness token as its message

These are static structure tests — no subprocess or import of the module under
test. They enforce architectural rules that were previously only expressed in
comments, making regression by accident impossible.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoskillit.core._type_constants import RETIRED_READINESS_TOKENS

_LIFESPAN_PATH = (
    Path(__file__).parent.parent.parent / "src" / "autoskillit" / "server" / "_lifespan.py"
)


def _find_lifespan_func(tree: ast.Module) -> ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_autoskillit_lifespan":
            return node
    pytest.fail("_autoskillit_lifespan not found in _lifespan.py")


def _call_func_name(node: ast.Call) -> str:
    """Return a dotted name string for the function being called."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return f"{_expr_name(func.value)}.{func.attr}"
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _expr_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_expr_name(node.value)}.{node.attr}"
    return ""


def _first_arg_str(node: ast.Call) -> str | None:
    """Return the string value of the first positional argument if it's a constant."""
    if node.args and isinstance(node.args[0], ast.Constant):
        return str(node.args[0].value)
    return None


def _first_real_stmt(body: list) -> ast.stmt | None:
    """Return the first non-docstring statement in a function body.

    A leading docstring is an ast.Expr whose value is an ast.Constant string.
    It is not executable code, so tests that guard against "code before try:"
    must skip it.
    """
    for stmt in body:
        if (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        ):
            continue  # skip leading docstring
        return stmt
    return None


class TestLifespanReadinessStructural:
    def setup_method(self):
        source = _LIFESPAN_PATH.read_text(encoding="utf-8")
        self.tree = ast.parse(source)
        self.func = _find_lifespan_func(self.tree)

    def test_first_statement_is_try(self):
        """Assertion A: first real statement of _autoskillit_lifespan must be try:."""
        body = self.func.body
        assert body, "_autoskillit_lifespan has an empty body"
        first_stmt = _first_real_stmt(body)
        assert first_stmt is not None, "_autoskillit_lifespan has no non-docstring statements"
        assert isinstance(first_stmt, (ast.Try, ast.TryStar)), (
            f"Expected the first statement of _autoskillit_lifespan to be a try: block, "
            f"got {type(first_stmt).__name__}. "
            "Readiness sentinel must be written inside try: to ensure cleanup runs in finally:."
        )

    def test_try_body_contains_sentinel_call(self):
        """Assertion B: try: body must call a sentinel/readiness write helper."""
        body = self.func.body
        first_stmt = _first_real_stmt(body)
        assert first_stmt is not None and isinstance(first_stmt, (ast.Try, ast.TryStar)), (
            "Pre-condition failed: first real stmt is not try: (see test_first_statement_is_try)"
        )
        try_node = first_stmt
        sentinel_calls = []
        for node in ast.walk(try_node):
            if isinstance(node, ast.Call):
                name = _call_func_name(node)
                if "sentinel" in name.lower() or "readiness" in name.lower():
                    sentinel_calls.append(name)
        assert sentinel_calls, (
            "No call to a sentinel/readiness write helper found inside the try: block of "
            "_autoskillit_lifespan. The readiness sentinel must be written before yield "
            "so tests can synchronize without polling log lines."
        )

    def test_no_retired_token_logger_calls(self):
        """Assertion C: no logger call uses a retired readiness token as first arg."""
        bad_calls: list[str] = []
        for node in ast.walk(self.func):
            if not isinstance(node, ast.Call):
                continue
            name = _call_func_name(node)
            if not name.startswith("logger."):
                continue
            first_arg = _first_arg_str(node)
            if first_arg in RETIRED_READINESS_TOKENS:
                bad_calls.append(
                    f"logger call with retired token {first_arg!r} at col {node.col_offset}"
                )
        assert not bad_calls, (
            "Found logger call(s) using retired readiness tokens in _autoskillit_lifespan:\n"
            + "\n".join(f"  {c}" for c in bad_calls)
            + f"\nRetired tokens: {sorted(RETIRED_READINESS_TOKENS)}"
        )
