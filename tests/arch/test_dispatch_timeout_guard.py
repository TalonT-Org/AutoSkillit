"""AST guard: the dispatch engine must use resolve_dispatch_timeout for all timeout surfaces.

After the #4851 decomposition, ``resolve_dispatch_timeout`` is computed in
Phase B (``src/autoskillit/fleet/dispatch/_lineage.py``) and the resolved
value flows into Phase C (``src/autoskillit/fleet/dispatch/_execution.py``)
where it is consumed by ``run_execution``. Both files are inspected to
prevent hardcoded-fallback regressions regardless of which phase adds a new
timeout surface in the future.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_LINEAGE_PATH = (
    Path(__file__).parents[2] / "src" / "autoskillit" / "fleet" / "dispatch" / "_lineage.py"
)
_EXECUTION_PATH = (
    Path(__file__).parents[2] / "src" / "autoskillit" / "fleet" / "dispatch" / "_execution.py"
)
_LINEAGE_PREP = "run_lineage_preparation"
_EXECUTION = "run_execution"
_RESOLVE_SYMBOL = "resolve_dispatch_timeout"


def _collect_function_source(tree: ast.Module, func_name: str) -> str:
    """Return the function body source with docstrings stripped.

    A bare substring match on the function source would treat any literal
    mention of ``resolve_dispatch_timeout`` (including inside a docstring) as
    a real call site. Strip the docstring so the substring check only fires
    on actual references in code.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == func_name:
            tree_copy = ast.Module(body=list(node.body), type_ignores=[])
            for stmt in tree_copy.body:
                if (
                    isinstance(stmt, ast.Expr)
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)
                ):
                    stmt.value = ast.Constant(value="")  # type: ignore[attr-defined]
            return ast.unparse(tree_copy)
    return ""


def _load_function_source(path: Path, func_name: str) -> str:
    assert path.exists(), f"Production file not found: {path}"
    tree = ast.parse(path.read_text())
    func_source = _collect_function_source(tree, func_name)
    assert func_source, f"Function '{func_name}' not found in {path}"
    return func_source


def test_run_dispatch_uses_resolve_dispatch_timeout() -> None:
    """The dispatch engine must use resolve_dispatch_timeout for all timeout surfaces.

    The decomposition split the legacy ``_run_dispatch`` into per-phase shards.
    ``resolve_dispatch_timeout`` is computed in Phase B (``_lineage.py``) and
    the resolved value flows into Phase C (``_execution.py``). All three
    locations must continue to use the resolved timeout value (no hardcoded
    fallbacks).
    """
    lineage_source = _load_function_source(_LINEAGE_PATH, _LINEAGE_PREP)
    execution_source = _load_function_source(_EXECUTION_PATH, _EXECUTION)

    # Phase B must compute the value; Phase C must consume it (either by
    # referencing the symbol or by accepting the resolved value as a parameter
    # that ``run_execution`` then forwards into ``timeout=`` / deadline).
    combined = lineage_source + "\n" + execution_source
    assert _RESOLVE_SYMBOL in combined, (
        f"'{_LINEAGE_PREP}' must call '{_RESOLVE_SYMBOL}' and "
        f"'{_EXECUTION}' must consume the resolved value via its 'resolved_timeout' "
        "parameter. All timeout surfaces (prompt build, process kill, session "
        "deadline) must use a single resolved value from resolve_dispatch_timeout."
    )

    assert "or 1800" not in combined, (
        "Dispatch engine contains hardcoded 'or 1800' timeout fallback. "
        "Use resolve_dispatch_timeout instead."
    )

    assert "if timeout_sec else None" not in combined, (
        "Dispatch engine contains falsy 'if timeout_sec else None' timeout check. "
        "Use resolve_dispatch_timeout which correctly handles timeout_sec=0."
    )
