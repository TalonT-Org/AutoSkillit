"""Structural guards for ref-coherence gate and base-branch fetch discipline."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]

_SRC = Path(__file__).parents[2] / "src" / "autoskillit"


def test_perform_merge_checks_ref_coherence_before_merge():
    """server/git.py must run rev-parse for coherence between _verify_merge_target and git merge.

    This structural test prevents future regressions where someone removes the
    coherence check. It verifies source-level ordering of the gate.
    """
    src = (_SRC / "server" / "git.py").read_text()
    lines = src.splitlines()

    verify_target_line = next(
        (i for i, ln in enumerate(lines) if "_verify_merge_target" in ln and "await" in ln),
        None,
    )
    assert verify_target_line is not None, "_verify_merge_target call not found in server/git.py"

    rev_parse_coherence_line = next(
        (
            i
            for i, ln in enumerate(lines)
            if i > verify_target_line
            and "rev-parse" in ln
            and "remote" in ln
            and "base_branch" in ln
        ),
        None,
    )
    assert rev_parse_coherence_line is not None, (
        "No rev-parse coherence check found after _verify_merge_target in server/git.py. "
        "The ref-coherence gate is missing."
    )

    git_merge_line = next(
        (
            i
            for i, ln in enumerate(lines)
            if i > rev_parse_coherence_line and '"merge"' in ln and "worktree_branch" in ln
        ),
        None,
    )
    assert git_merge_line is not None, (
        "git merge command not found after ref-coherence gate in server/git.py. "
        f"rev_parse_coherence_line={rev_parse_coherence_line}"
    )

    assert rev_parse_coherence_line < git_merge_line, (
        "ref-coherence rev-parse must appear before git merge command. "
        f"Got rev_parse at line {rev_parse_coherence_line}, merge at line {git_merge_line}."
    )


def _get_run_git_calls_in_function(func_node: ast.FunctionDef) -> list[tuple[int, list[str]]]:
    """Extract (lineno, first_arg_elements) for each run_git call in the function body."""
    results = []
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "run_git"):
            continue
        if not node.args:
            continue
        first_arg = node.args[0]
        if not isinstance(first_arg, ast.List):
            continue
        elements = [
            elt.value
            for elt in first_arg.elts
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        ]
        results.append((node.lineno, elements))
    return results


def test_cmd_rpc_merge_fetches_base_before_rebase():
    """Each function in _cmd_rpc_merge.py that rebases must fetch base_branch first.

    This structural test prevents the stale-base-branch pattern from recurring.
    Only functions that explicitly take a `base_branch` parameter AND perform a
    rebase are checked (these are the functions with the invariant).
    """
    src = (_SRC / "recipe" / "_cmd_rpc_merge.py").read_text()
    tree = ast.parse(src)

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        param_names = [arg.arg for arg in node.args.args]
        if "base_branch" not in param_names:
            continue
        calls = _get_run_git_calls_in_function(node)
        rebase_lines = [
            lineno for lineno, elts in calls if "rebase" in elts and "--abort" not in elts
        ]
        if not rebase_lines:
            continue
        all_fetch_lines = [lineno for lineno, elts in calls if elts[:1] == ["fetch"]]
        for rebase_line in rebase_lines:
            preceding_fetches = [ln for ln in all_fetch_lines if ln < rebase_line]
            if not preceding_fetches:
                offenders.append(
                    f"{node.name}(): rebase at line {rebase_line} has no preceding fetch"
                )

    assert not offenders, (
        "Functions in _cmd_rpc_merge.py rebase without any preceding fetch:\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )
