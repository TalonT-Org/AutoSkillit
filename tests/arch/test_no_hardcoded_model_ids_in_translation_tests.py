"""Architectural guard: translation tests must not hardcode alias-resolved model IDs.

Tests must assert against CODEX_MODEL_ALIASES[key] rather than literal alias output
strings. This prevents co-authoring of wrong values: if the alias dict is wrong,
tests that hardcode the same wrong value pass silently.

Passthrough tests (e.g., translate_model("o3") == "o3") are NOT flagged because
they test native model IDs, not alias-resolved values.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def _get_test_model_translation_path() -> Path:
    return Path(__file__).parent.parent / "execution" / "backends" / "test_model_translation.py"


def _get_alias_values() -> frozenset[str]:
    from autoskillit.core.types._type_backend import CODEX_MODEL_ALIASES

    return frozenset(CODEX_MODEL_ALIASES.values())


def _collect_rhs_string_literals_in_assert_eq(tree: ast.AST) -> list[str]:
    """Collect string literals on the RHS of == comparisons inside assert statements."""
    literals: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        test = node.test
        if not isinstance(test, ast.Compare):
            continue
        for op, comparator in zip(test.ops, test.comparators):
            if isinstance(op, ast.Eq) and isinstance(comparator, ast.Constant):
                if isinstance(comparator.value, str):
                    literals.append(comparator.value)
    return literals


def test_no_hardcoded_model_ids_in_translation_tests() -> None:
    alias_values = _get_alias_values()
    path = _get_test_model_translation_path()
    source = path.read_text()
    tree = ast.parse(source)

    rhs_literals = _collect_rhs_string_literals_in_assert_eq(tree)
    violations = [lit for lit in rhs_literals if lit in alias_values]

    assert not violations, (
        f"test_model_translation.py hardcodes alias-resolved model ID(s) in assert "
        f"comparisons: {violations!r}. "
        "Use CODEX_MODEL_ALIASES[key] instead of literal strings so tests track the "
        "source dict rather than cementing stale values."
    )
