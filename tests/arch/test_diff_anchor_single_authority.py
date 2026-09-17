"""Concrete topology guard for review-anchor admission and publication."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REVIEW_PACKAGE = _REPO_ROOT / "src" / "autoskillit" / "execution" / "github_review"


def _enclosing_function(tree: ast.AST, call: ast.Call) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            descendant is call for descendant in ast.walk(node)
        ):
            return node.name
    return None


def _call_name(call: ast.Call) -> str | None:
    function = call.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return None


def test_remote_review_mutation_has_one_call_site_in_attempt() -> None:
    call_sites: list[tuple[str, str | None]] = []
    for path in _REVIEW_PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) == "create_review":
                call_sites.append((path.name, _enclosing_function(tree, node)))
    # Symbol-shape guard: every create_review call must resolve to _attempt (or a callee
    # thereof). The previous filename-literal check was brittle to file/function renames.
    assert call_sites
    for filename, enclosing in call_sites:
        assert filename == "poster.py", (
            f"create_review call must originate from poster.py, got {filename}"
        )
        assert enclosing in {"_attempt", "_post"}, (
            f"create_review call must live in _attempt or its caller _post, got {enclosing!r}"
        )
    assert len(call_sites) == 1, (
        f"create_review must have exactly one call site, found {len(call_sites)}"
    )


def test_post_admits_findings_before_entering_attempt() -> None:
    tree = ast.parse((_REVIEW_PACKAGE / "poster.py").read_text())
    post = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_post"
    )
    calls = [node for node in ast.walk(post) if isinstance(node, ast.Call)]
    admission_lines = [node.lineno for node in calls if _call_name(node) == "admit_findings"]
    attempt_lines = [node.lineno for node in calls if _call_name(node) == "_attempt"]
    assert len(admission_lines) == 1
    assert attempt_lines
    assert admission_lines[0] < min(attempt_lines)
