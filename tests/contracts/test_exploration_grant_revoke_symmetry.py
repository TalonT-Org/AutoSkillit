"""Contract: exploration tag grants pair with a revoke on every failure path (#4684 Fix E).

For every ``await ctx.enable_components(tags=...)`` (a grant) in
tools_exploration.py, the enclosing function must also reach an
``await ctx.disable_components(tags=...)`` call — the symmetric revoke.
Before this fix, ``enable_exploration`` granted via ``enable_components``
but revoked only via ``store.cleanup_session`` (which releases the
in-memory *lease*, a resource distinct from the FastMCP visibility *tag*
``disable_components`` controls) — leaving the tag visible with no live
lease behind it if a later step failed after the grant succeeded.

Also asserts the two durable exploration-authority writers
(``bind_launch``, ``bind_session_scoped_durable``) are registered in
DURABLE_ARTIFACT_WRITERS (see core/types/_type_constants.py) — the
mechanical half of "both write 0600 HMAC-signed authority files whose
lifetime exceeds the writing process" per the plan's Fix E.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoskillit.core.types._type_constants import DURABLE_ARTIFACT_WRITERS

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_SRC = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "autoskillit"
    / "server"
    / "tools"
    / "tools_exploration.py"
)


def _calls_ctx_component_method(node: ast.AST, method: str) -> bool:
    """True iff `node` is an `await ctx.<method>(tags=...)` (or bare) call."""
    call = node.value if isinstance(node, ast.Await) else node
    return (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == method
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "ctx"
    )


def _function_calls(func: ast.FunctionDef | ast.AsyncFunctionDef, method: str) -> bool:
    return any(_calls_ctx_component_method(node, method) for node in ast.walk(func))


def test_every_enable_components_has_a_disable_components_in_the_same_function() -> None:
    tree = ast.parse(_SRC.read_text(encoding="utf-8"), filename=str(_SRC))
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _function_calls(node, "enable_components"):
            continue
        if not _function_calls(node, "disable_components"):
            violations.append(
                f"{node.name} (line {node.lineno}) calls ctx.enable_components(...) "
                "with no reachable ctx.disable_components(...) — grant/revoke asymmetry"
            )
    assert not violations, violations


def test_durable_artifact_writers_complete() -> None:
    registered = {w.writer for w in DURABLE_ARTIFACT_WRITERS}
    required = {
        "autoskillit.pipeline.exploration_context:OwnerBoundExplorationContextStore.bind_launch",
        "autoskillit.pipeline.exploration_context_durable:bind_session_scoped_durable",
    }
    missing = required - registered
    assert not missing, (
        f"Durable exploration-authority writer(s) not registered in "
        f"DURABLE_ARTIFACT_WRITERS: {missing}"
    )
