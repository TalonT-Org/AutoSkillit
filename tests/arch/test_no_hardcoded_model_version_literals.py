"""Guard model-version literals in tests against unrelated pinning.

Tests must assert against the backend alias registries rather than literal alias output
strings. This prevents co-authoring of wrong values: if an alias dict is wrong,
tests that hardcode the same wrong value pass silently.

Passthrough tests (e.g., translate_model("o3") == "o3") are NOT flagged because
they test native model IDs, not alias-resolved values.

Tests that legitimately pin model-version literals (catalog fixtures, alias
round-trip tests) opt in via ``@pytest.mark.model_contract``. The allowlist is
discovered at pytest collection time, so adding a new model-contract test does
not require updating this guard.

Scope: this guard walks only pytest-collected test files (``test_*.py``), not
fixture modules, ``conftest.py``, or ``__init__.py``. Fixture files that pin
model-version literals (e.g., ``tests/execution/backends/_codex_fixtures.py``)
are not directly checkable but are implicitly blessed through the
``model_contract`` marker on their consumer tests.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small, pytest.mark.model_contract]

_TESTS_ROOT = Path(__file__).parent.parent
_MODEL_VERSION_RE = re.compile(r"gpt-[0-9]+(?:\.[0-9]+)?(?:-[a-z]+)?")


def _is_pytest_collected(path: Path) -> bool:
    """Match pytest's default collection pattern (test_*.py)."""
    return path.name.startswith("test_") and path.suffix == ".py"


def _collect_marked_model_contract_paths(request: pytest.FixtureRequest) -> set[Path]:
    """Return absolute paths of every test file containing a @pytest.mark.model_contract test."""
    marked: set[Path] = set()
    for item in request.session.items:
        if item.get_closest_marker("model_contract") is None:
            continue
        fspath = getattr(item, "fspath", None) or getattr(item, "path", None)
        if fspath is None:
            continue
        marked.add(Path(str(fspath)).resolve())
    return marked


def test_gpt_version_literals_are_limited_to_model_contract_tests(
    request: pytest.FixtureRequest,
) -> None:
    allowed_paths = _collect_marked_model_contract_paths(request)
    violations: dict[str, list[str]] = {}
    for path in _TESTS_ROOT.rglob("*.py"):
        if not _is_pytest_collected(path):
            continue
        if path.resolve() in allowed_paths:
            continue
        literals = set(_MODEL_VERSION_RE.findall(path.read_text()))
        if literals:
            violations[str(path.relative_to(_TESTS_ROOT))] = sorted(literals)
    assert not violations, f"Unrelated tests pin GPT model versions: {violations}"


def _get_test_model_translation_path() -> Path:
    return Path(__file__).parent.parent / "execution" / "backends" / "test_model_translation.py"


def _get_alias_values() -> frozenset[str]:
    from autoskillit.core.types._type_backend import CLAUDE_MODEL_ALIASES, CODEX_MODEL_ALIASES

    registries = (CLAUDE_MODEL_ALIASES, CODEX_MODEL_ALIASES)
    return frozenset(
        value for aliases in registries for key, value in aliases.items() if value != key
    )


def _collect_rhs_string_literals_in_assert_eq(tree: ast.AST) -> list[str]:
    """Collect string literals on the RHS of == comparisons inside assert statements.

    Passthrough patterns — where the same literal appears in both the LHS call
    argument and the RHS — are excluded. These test that a native model ID passes
    through unchanged and are not alias-resolution assertions.
    """
    literals: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        test = node.test
        if not isinstance(test, ast.Compare):
            continue
        lhs_literals = {
            n.value
            for n in ast.walk(test.left)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
        for op, comparator in zip(test.ops, test.comparators):
            if isinstance(op, ast.Eq) and isinstance(comparator, ast.Constant):
                if isinstance(comparator.value, str):
                    # Skip passthrough tests: same literal in both argument and expected value
                    if comparator.value not in lhs_literals:
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
        "Use the backend alias registry instead of literal strings so tests track the "
        "source mapping rather than cementing stale values."
    )
