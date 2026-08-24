"""Standing invariant: the exploration store-exception dispatch is derived,
not hand-written (#4756 Part C).

Before this part, tools_exploration.py had three hand-maintained parallel
lists mapping OwnerBoundExplorationContextStore's nested exceptions to
ExplorationFailureCode: six nested exception classes, a six-member literal
except-tuple re-raising them past an inner handler, and six separate outer
except clauses. Nothing kept them in sync — INVALID_SOURCE_IDENTITY drifted
out of the test matrix for months without any guard catching it. This test
forbids the except-tuple lists from re-forming: every except clause whose
type tuple could name an OwnerBoundExplorationContextStore exception must
resolve through EXPLORATION_STORE_FAILURE_CODES, not enumerate the
attributes literally.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SRC = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "autoskillit"
    / "server"
    / "tools"
    / "tools_exploration.py"
)


def _is_store_attribute(node: ast.expr) -> bool:
    """True if *node* is OwnerBoundExplorationContextStore.<Name>."""
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "OwnerBoundExplorationContextStore"
    )


def _literal_store_exception_except_clauses(tree: ast.AST) -> list[ast.ExceptHandler]:
    """Every except clause whose type tuple literally enumerates
    OwnerBoundExplorationContextStore attributes, rather than resolving
    through a Name/Call such as ``tuple(EXPLORATION_STORE_FAILURE_CODES)``."""
    found: list[ast.ExceptHandler] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or node.type is None:
            continue
        candidates = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
        if any(_is_store_attribute(candidate) for candidate in candidates):
            found.append(node)
    return found


def test_no_literal_store_exception_except_tuple() -> None:
    tree = ast.parse(_SRC.read_text(encoding="utf-8"), filename=str(_SRC))
    violations = _literal_store_exception_except_clauses(tree)
    assert not violations, (
        "except clause(s) literally enumerate OwnerBoundExplorationContextStore "
        "attributes instead of resolving through EXPLORATION_STORE_FAILURE_CODES "
        "— re-forms the three-list drift #4756 Part C removed, at line(s): "
        f"{[node.lineno for node in violations]}"
    )


def test_guard_fixture_is_live() -> None:
    """Prove the detector fires on the exact shape it forbids, not just on
    today's (already-derived) source — the discipline
    test_distinct_layers_extraction applies to its own logic
    (tests/arch/test_rectify_blast_radius_guard.py)."""
    literal = ast.parse(
        "try:\n"
        "    pass\n"
        "except (\n"
        "    OwnerBoundExplorationContextStore.SnapshotStale,\n"
        "    OwnerBoundExplorationContextStore.StoreClosed,\n"
        "):\n"
        "    pass\n"
    ).body[0]
    derived = ast.parse(
        "try:\n    pass\nexcept tuple(EXPLORATION_STORE_FAILURE_CODES):\n    pass\n"
    ).body[0]
    assert isinstance(literal, ast.Try)
    assert isinstance(derived, ast.Try)
    assert _literal_store_exception_except_clauses(literal) == literal.handlers
    assert _literal_store_exception_except_clauses(derived) == []
