"""Shared fixtures and helpers for tests/recipe/rules_merge/.

Keeps per-family test files lean by centralizing:
- ``EXPECTED_RULE_NAMES`` — the canonical set of nine merge-prefix rules.
- ``registered_merge_rule_names()`` — filter ``_RULE_REGISTRY`` for
  merge-prefix names, returning a set.
- ``build_recipe(steps_dict)`` — thin wrapper around the recipe conftest
  builder that creates a ``Recipe`` from YAML-loadable step dicts.
- ``rule_names_from(recipe)`` — run all semantic rules on a recipe and
  return the set of rule names that fired, sorted into a list.

The registry list lives at ``autoskillit.recipe.registry._RULE_REGISTRY``;
we touch it directly because no public alias is exposed.
"""

from __future__ import annotations

from autoskillit.recipe.registry import _RULE_REGISTRY, run_semantic_rules
from autoskillit.recipe.schema import Recipe
from tests.recipe.conftest import _make_workflow

EXPECTED_RULE_NAMES: tuple[str, ...] = (
    "merge-routing-incomplete",
    "merge-routing-cross-site-consistency",
    "merge-failure-skill-domain-mismatch",
    "merge-fix-cycle-without-iteration-guard",
    "gh-pr-merge-silent-success-routing",
    "merge-without-commit-guard",
    "release-issue-on-unconfirmed-merge",
    "merge-enrollment-auto-consistency",
    "merge-site-push-symmetry",
)


def registered_merge_rule_names() -> set[str]:
    """Return the set of merge-prefix rule names currently registered."""
    merge_prefixes = ("merge-", "gh-pr-merge-", "release-issue-")
    return {rule.name for rule in _RULE_REGISTRY if rule.name.startswith(merge_prefixes)}


def build_recipe(steps: dict[str, dict]) -> Recipe:
    """Build a Recipe from a YAML-loadable step dict.

    Pass-through to ``tests.recipe.conftest._make_workflow`` so per-family
    tests use the same parsing logic the rest of ``tests/recipe/`` uses.
    """
    return _make_workflow(steps)


def rule_names_from(recipe: Recipe) -> set[str]:
    """Run all registered rules on ``recipe`` and return the set of rule names that fired."""
    findings = run_semantic_rules(recipe)
    return {f.rule for f in findings}


def assert_rule_fires(recipe: Recipe, *, rule_name: str) -> None:
    """Assert that running all rules on ``recipe`` produces a finding from ``rule_name``."""
    fired = rule_names_from(recipe)
    assert rule_name in fired, (
        f"Expected rule {rule_name!r} to fire on recipe but it did not. "
        f"Rules that fired: {sorted(fired)}."
    )


def assert_rule_does_not_fire(recipe: Recipe, *, rule_name: str) -> None:
    """Assert that running all rules on ``recipe`` produces NO finding from ``rule_name``."""
    fired = rule_names_from(recipe)
    assert rule_name not in fired, f"Expected rule {rule_name!r} to NOT fire but it did."
