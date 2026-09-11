"""Bound-consumption provenance guard.

Pins the exact call sites that read ``.page_max_bytes``/``.response_max_bytes``
off an ``output_budget``-shaped config object across the recipe-delivery and
recipe-section-pagination modules. Both values must flow through
``resolve_recipe_section_bound_bytes`` (which delegates to the single-seat
core resolver ``resolve_recipe_section_response_bound``) -- never be read
independently and threaded around it. A future engineer wiring a config
ceiling directly into pagination or delivery logic, bypassing the reconciled
resolver, fails this test instead of silently reintroducing a second,
divergent bound authority.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SCANNED_FILES = (
    tuple(sorted((SRC_ROOT / "server" / "recipe").rglob("_recipe_*.py")))
    + tuple(sorted((SRC_ROOT / "server" / "recipe" / "_recipe_delivery").rglob("*.py")))
    + tuple(sorted((SRC_ROOT / "server" / "tools").rglob("*recipe*.py")))
)
assert _SCANNED_FILES, "no recipe-delivery/pagination source files found for provenance scan"

_BOUND_ATTRS = frozenset({"page_max_bytes", "response_max_bytes"})

# (module path relative to SRC_ROOT, enclosing function, exact unparsed
# attribute-read expression). Every one of these feeds directly into
# resolve_recipe_section_bound_bytes / resolve_recipe_section_response_bound.
_ALLOWED_BOUND_READS: frozenset[tuple[str, str, str]] = frozenset(
    {
        (
            "server/recipe/_recipe_delivery/_finalize.py",
            "finalize_recipe_delivery",
            "response_budget.response_max_bytes",
        ),
        (
            "server/recipe/_recipe_delivery/_finalize.py",
            "finalize_recipe_delivery",
            "response_budget.page_max_bytes",
        ),
        (
            "server/tools/_recipe_section_handler.py",
            "_recipe_section_request_state_factory",
            "tool_ctx.config.output_budget.response_max_bytes",
        ),
        (
            "server/tools/_recipe_section_handler.py",
            "_recipe_section_request_state_factory",
            "tool_ctx.config.output_budget.page_max_bytes",
        ),
    }
)


def _collect_bound_reads(path: Path) -> set[tuple[str, str, str]]:
    """AST-scan one module for ``.page_max_bytes``/``.response_max_bytes`` reads.

    Fingerprints each read by (relative path, enclosing function, exact
    unparsed expression) rather than line number, so unrelated edits
    elsewhere in the same function don't spuriously break this pin.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    rel = str(path.relative_to(SRC_ROOT)).replace("\\", "/")
    found: set[tuple[str, str, str]] = set()
    stack: list[str] = []

    class _Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        def visit_Attribute(self, node: ast.Attribute) -> None:
            if node.attr in _BOUND_ATTRS:
                enclosing = stack[-1] if stack else "<module>"
                found.add((rel, enclosing, ast.unparse(node)))
            self.generic_visit(node)

    _Visitor().visit(tree)
    return found


def test_bound_attribute_reads_are_pinned_to_known_call_sites() -> None:
    """Every ``.page_max_bytes``/``.response_max_bytes`` read in the three
    resolver-adjacent modules must be one of the enumerated feeds into
    ``resolve_recipe_section_bound_bytes``. A new read outside this set means
    a config ceiling is being consumed directly instead of through the
    reconciled resolver.
    """
    found: set[tuple[str, str, str]] = set()
    for path in _SCANNED_FILES:
        found |= _collect_bound_reads(path)

    unexpected = found - _ALLOWED_BOUND_READS
    missing = _ALLOWED_BOUND_READS - found
    assert not unexpected, (
        "new .page_max_bytes/.response_max_bytes read outside the pinned "
        f"resolver-feeding call sites -- route it through "
        f"resolve_recipe_section_bound_bytes instead: {sorted(unexpected)}"
    )
    assert not missing, (
        "a pinned bound-attribute read site is missing -- update "
        f"_ALLOWED_BOUND_READS if this removal was intentional: {sorted(missing)}"
    )


def test_allowed_bound_reads_paths_exist() -> None:
    """Every ``_ALLOWED_BOUND_READS`` path must resolve to a real file under SRC_ROOT.

    ``_ALLOWED_BOUND_READS`` is a hand-maintained registry with no direct
    file-existence check -- its only indirect protection is contingent on
    ``_SCANNED_FILES`` still covering the path. A phantom path here would
    silently exempt a read site that no longer exists from ever being
    exercised by ``test_bound_attribute_reads_are_pinned_to_known_call_sites``.
    """
    phantom = sorted(
        path for path, _func, _expr in _ALLOWED_BOUND_READS if not (SRC_ROOT / path).is_file()
    )
    assert not phantom, f"_ALLOWED_BOUND_READS path(s) do not exist: {phantom}"
