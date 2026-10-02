"""Hook-runtime identity and containment comparisons canonicalize both operands.

Rule: every call in ``src/autoskillit/hooks/**`` to a path-relation primitive
(``relative_to``, ``is_relative_to``, ``commonpath``, ``commonprefix``,
``relpath``) lives in ``hooks/_runtime/_path_identity.py``, which canonicalizes
both operands, or at a site in ``_ALLOWLISTED_SITES`` whose rationale names where
each operand is canonicalized. A one-sided canonicalization let a hook reached
through the symlinked plugin selector deny every tool call.

The scan matches plain names and attributes, ``from ... import`` of a banned
name under any alias, and ``getattr(x, "<banned>")`` with a constant attribute
name. Calls through a computed attribute name are outside its reach.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoskillit.hook_registry import HOOKS_DIR

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_BANNED = frozenset({"relative_to", "is_relative_to", "commonpath", "commonprefix", "relpath"})
_HELPER = "_runtime/_path_identity.py"
_MIN_RATIONALE_CHARS = 40

SiteKey = tuple[str, str]

_ALLOWLISTED_SITES: dict[SiteKey, str] = {
    ("guards/git_ops_guard.py", "_relative_to"): (
        "target, common and worktree_git are each .resolve()d by _raw_target_mutations "
        "before _classify_raw_write_target receives them, so both operands are canonical."
    ),
    ("formatters/_fmt_response_spill.py", "_response_spill_artifact_is_trusted"): (
        "resolved comes from artifact.resolve(strict=True) inline and project_temp from "
        "_response_temp_root(), which resolves both of its return values."
    ),
}


def _banned_sites(source: str, relpath: str) -> set[SiteKey]:
    """Return ``(relpath, innermost enclosing function)`` for every banned use."""
    sites: set[SiteKey] = set()

    def visit(node: ast.AST, function: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Name) and func.id in _BANNED) or (
                isinstance(func, ast.Attribute) and func.attr in _BANNED
            ):
                sites.add((relpath, function))
            if (
                isinstance(func, ast.Name)
                and func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in _BANNED
            ):
                sites.add((relpath, function))
        if isinstance(node, ast.ImportFrom) and any(alias.name in _BANNED for alias in node.names):
            sites.add((relpath, function))
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(ast.parse(source, filename=relpath), "<module>")
    return sites


def _live_sites() -> set[SiteKey]:
    sites: set[SiteKey] = set()
    for path in sorted(HOOKS_DIR.rglob("*.py")):
        relpath = path.relative_to(HOOKS_DIR).as_posix()
        if relpath == _HELPER or "__pycache__" in Path(relpath).parts:
            continue
        sites |= _banned_sites(path.read_text(encoding="utf-8"), relpath)
    return sites


def test_hook_path_relations_go_through_path_identity() -> None:
    live = _live_sites()
    unlisted = sorted(live - set(_ALLOWLISTED_SITES))
    stale = sorted(set(_ALLOWLISTED_SITES) - live)
    assert not unlisted, (
        "hook path relations must canonicalize both operands through "
        f"hooks/_runtime/_path_identity.py: {unlisted}"
    )
    assert not stale, f"allowlisted path-relation sites no longer exist: {stale}"


def test_allowlist_rationales_are_substantive() -> None:
    thin = sorted(
        key
        for key, rationale in _ALLOWLISTED_SITES.items()
        if len(rationale.strip()) < _MIN_RATIONALE_CHARS
    )
    assert not thin, f"allowlisted sites need a rationale naming both operands: {thin}"


def test_path_identity_helper_exists() -> None:
    assert (HOOKS_DIR / _HELPER).is_file()


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("def f(p, root):\n    return p.relative_to(root)\n", {("x.py", "f")}),
        ("def f(p, root):\n    return p.is_relative_to(root)\n", {("x.py", "f")}),
        ("import os\ndef g(a, b):\n    return os.path.commonpath((a, b))\n", {("x.py", "g")}),
        ("from os.path import relpath as rp\n", {("x.py", "<module>")}),
        ("def h(p):\n    return getattr(p, 'relative_to')\n", {("x.py", "h")}),
        (
            "def outer(t):\n"
            "    def inner(r):\n"
            "        return t.relative_to(r)\n"
            "    return inner\n",
            {("x.py", "inner")},
        ),
        ("def ok(p):\n    return p.resolve()\n", set()),
    ],
)
def test_scanner_detects_every_banned_form(source: str, expected: set[SiteKey]) -> None:
    assert _banned_sites(source, "x.py") == expected
