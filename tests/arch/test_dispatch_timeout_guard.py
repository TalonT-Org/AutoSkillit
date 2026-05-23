"""AST guard: _run_dispatch must use resolve_dispatch_timeout for all timeout surfaces."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_API_PATH = Path(__file__).parents[2] / "src" / "autoskillit" / "fleet" / "_api.py"
_RUN_DISPATCH = "_run_dispatch"
_RESOLVE_SYMBOL = "resolve_dispatch_timeout"


def _collect_function_source(tree: ast.Module, func_name: str) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == func_name:
            return ast.unparse(node)
    return ""


def test_run_dispatch_uses_resolve_dispatch_timeout() -> None:
    """_run_dispatch must call resolve_dispatch_timeout and use its result for all timeout surfaces.

    Prevents future regressions where a new timeout surface is added with its own
    hardcoded fallback.
    """
    assert _API_PATH.exists(), f"Production file not found: {_API_PATH}"
    source = _API_PATH.read_text()
    tree = ast.parse(source)

    func_source = _collect_function_source(tree, _RUN_DISPATCH)
    assert func_source, f"Function '{_RUN_DISPATCH}' not found in {_API_PATH}"

    assert _RESOLVE_SYMBOL in func_source, (
        f"'{_RUN_DISPATCH}' must call '{_RESOLVE_SYMBOL}' but no call was found. "
        "All three timeout surfaces (prompt, process kill, session deadline) must use "
        "a single resolved value from resolve_dispatch_timeout."
    )

    assert "or 1800" not in func_source, (
        f"'{_RUN_DISPATCH}' contains hardcoded 'or 1800' fallback. "
        "Use resolve_dispatch_timeout instead."
    )

    assert "if timeout_sec else None" not in func_source, (
        f"'{_RUN_DISPATCH}' contains falsy 'if timeout_sec else None' timeout check. "
        "Use resolve_dispatch_timeout which correctly handles timeout_sec=0."
    )
