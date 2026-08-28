"""Structural enforcement of pipeline ordering in recipe/_api.py:load_and_validate.

The pipeline must run semantic rules AFTER _prune_skipped_steps so that pruned
steps are never seen by semantic rules. This prevents pre-prune semantic
findings from poisoning the validity computation (the root cause of the
codex open_kitchen "unknown structural error" bug — see PR #4059).
"""

from __future__ import annotations

import ast

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


_API_PATH = SRC_ROOT / "recipe" / "_api_orchestration_validate.py"


def _find_function_node(tree: ast.Module, func_name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return node
    raise AssertionError(f"Function {func_name!r} not found in source")


def _find_call_line(func_body: list[ast.stmt], func_name: str, *, last: bool = False) -> int:
    """Find the line number of a call to a function with the given name.

    Accepts both bare ``foo()`` calls (post-decomposition these resolve via
    ``_orch.foo(...)`` for monkeypatch reachability — the trailing attribute
    access is part of the same call site).
    """
    found = -1
    for node in ast.walk(ast.Module(body=func_body, type_ignores=[])):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == func_name:
                if not last:
                    return node.lineno
                found = node.lineno
            elif isinstance(func, ast.Attribute) and func.attr == func_name:
                # Allow ``_orch.<name>()`` form — the orchestrator module
                # attribute is the canonical resolution site for monkeypatch.
                if not last:
                    return node.lineno
                found = node.lineno
    return found


def test_semantic_rules_run_after_pruning() -> None:
    """AST guard: in the orchestrator module, _prune_skipped_steps must be called
    BEFORE run_semantic_rules (or make_validation_context, which builds the
    context passed to run_semantic_rules).

    The pipeline was extracted from ``load_and_validate`` into
    ``_run_validation_pipeline`` by issue #4860; this guard scans the entire
    module to verify the ordering invariant is preserved across the split.
    """
    source = _API_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Scan module-level helpers + load_and_validate (they may delegate to helpers).
    scan_bodies: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scan_bodies.extend(node.body)
    if not scan_bodies:
        func = _find_function_node(tree, "load_and_validate")
        scan_bodies = func.body
    synthetic = ast.Module(body=scan_bodies, type_ignores=[])

    prune_line = _find_call_line(synthetic.body, "_prune_skipped_steps")
    semantic_line = _find_call_line(synthetic.body, "run_semantic_rules", last=True)
    ctx_line = _find_call_line(synthetic.body, "make_validation_context", last=True)

    assert prune_line > 0, (
        f"_prune_skipped_steps call not found in orchestrator module at {_API_PATH}"
    )
    assert semantic_line > 0, (
        f"run_semantic_rules call not found in orchestrator module at {_API_PATH}"
    )

    assert prune_line < semantic_line, (
        f"_prune_skipped_steps (line {prune_line}) must be called BEFORE "
        f"run_semantic_rules (line {semantic_line}) in the orchestrator module. "
        f"Pre-prune semantic findings poison the validity computation."
    )

    if ctx_line > 0:
        assert prune_line < ctx_line, (
            f"_prune_skipped_steps (line {prune_line}) must be called BEFORE "
            f"make_validation_context (line {ctx_line}) in load_and_validate(). "
            f"make_validation_context builds the recipe snapshot that "
            f"run_semantic_rules sees — it must be built from the post-prune recipe."
        )
