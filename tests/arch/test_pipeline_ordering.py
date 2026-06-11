"""Structural enforcement of pipeline ordering in recipe/_api.py:load_and_validate.

The pipeline must run semantic rules AFTER _prune_skipped_steps so that pruned
steps are never seen by semantic rules. This prevents pre-prune semantic
findings from poisoning the validity computation (the root cause of the
codex open_kitchen "unknown structural error" bug — see investigation_from_issue.md).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


_API_PATH = SRC_ROOT / "autoskillit" / "recipe" / "_api.py"


def _find_function_node(tree: ast.Module, func_name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return node
    raise AssertionError(f"Function {func_name!r} not found in source")


def _find_call_line(func_body: list[ast.stmt], func_name: str) -> int:
    """Find the line number of the first call to a top-level function with the given name."""
    for node in ast.walk(ast.Module(body=func_body, type_ignores=[])):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == func_name:
                return node.lineno
    return -1


def test_semantic_rules_run_after_pruning() -> None:
    """AST guard: in load_and_validate(), _prune_skipped_steps must be called
    BEFORE run_semantic_rules (or make_validation_context, which builds the
    context passed to run_semantic_rules).

    This prevents future regressions where someone moves the semantic rules
    block before the pruning block, reintroducing the pre-prune validity bug.
    """
    source = _API_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = _find_function_node(tree, "load_and_validate")

    prune_line = _find_call_line(func.body, "_prune_skipped_steps")
    semantic_line = _find_call_line(func.body, "run_semantic_rules")
    ctx_line = _find_call_line(func.body, "make_validation_context")

    assert prune_line > 0, (
        f"_prune_skipped_steps call not found in load_and_validate() in {_API_PATH}"
    )
    assert semantic_line > 0, (
        f"run_semantic_rules call not found in load_and_validate() in {_API_PATH}"
    )

    assert prune_line < semantic_line, (
        f"_prune_skipped_steps (line {prune_line}) must be called BEFORE "
        f"run_semantic_rules (line {semantic_line}) in load_and_validate(). "
        f"Pre-prune semantic findings poison the validity computation."
    )

    if ctx_line > 0:
        assert prune_line < ctx_line, (
            f"_prune_skipped_steps (line {prune_line}) must be called BEFORE "
            f"make_validation_context (line {ctx_line}) in load_and_validate(). "
            f"make_validation_context builds the recipe snapshot that "
            f"run_semantic_rules sees — it must be built from the post-prune recipe."
        )
