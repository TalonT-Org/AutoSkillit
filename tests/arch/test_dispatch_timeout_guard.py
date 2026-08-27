"""AST guard: the dispatch engine must use resolve_dispatch_timeout for all timeout surfaces.

After the #4851 decomposition, ``_run_dispatch`` lives in
``src/autoskillit/fleet/dispatch/_api.py`` and the ``resolve_dispatch_timeout``
call site lives inside the Phase B lineage shard
(``src/autoskillit/fleet/dispatch/_lineage.py``). The substring-match for
``resolve_dispatch_timeout`` is applied to both files to prevent
hardcoded-fallback regressions regardless of which phase adds a new
timeout surface in the future.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_API_PATH = Path(__file__).parents[2] / "src" / "autoskillit" / "fleet" / "dispatch" / "_api.py"
_LINEAGE_PATH = (
    Path(__file__).parents[2] / "src" / "autoskillit" / "fleet" / "dispatch" / "_lineage.py"
)
_RUN_DISPATCH = "_run_dispatch"
_LINEAGE_PREP = "run_lineage_preparation"
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
    ``resolve_dispatch_timeout`` now lives in Phase B (``_lineage.py``); both
    must continue to use the resolved timeout value (no hardcoded fallbacks).
    """
    func_source = _load_function_source(_API_PATH, _RUN_DISPATCH)
    lineage_source = _load_function_source(_LINEAGE_PATH, _LINEAGE_PREP)

    combined = func_source + "\n" + lineage_source
    assert _RESOLVE_SYMBOL in combined, (
        f"Neither '{_RUN_DISPATCH}' nor '{_LINEAGE_PREP}' calls '{_RESOLVE_SYMBOL}'. "
        "All timeout surfaces (prompt build, process kill, session deadline) must "
        "use a single resolved value from resolve_dispatch_timeout."
    )

    assert "or 1800" not in combined, (
        "Dispatch engine contains hardcoded 'or 1800' timeout fallback. "
        "Use resolve_dispatch_timeout instead."
    )

    assert "if timeout_sec else None" not in combined, (
        "Dispatch engine contains falsy 'if timeout_sec else None' timeout check. "
        "Use resolve_dispatch_timeout which correctly handles timeout_sec=0."
    )
