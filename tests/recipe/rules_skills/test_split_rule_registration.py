"""Rule-registration tests for the #4852 rules_skill_content split.

Verifies that the four sibling rule modules, when imported via the facade,
register all 15 expected rule names exactly once. Duplicate registration
across the four files would inflate the rule registry and break consumers
that look up rules by name.
"""

from __future__ import annotations

import pytest

from tests.recipe.rules_skills._helpers import EXPECTED_RULE_NAMES

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_all_fifteen_rule_names_in_registry() -> None:
    """All 15 rule names must be registered after importing the facade."""
    import autoskillit.recipe.rules.rules_skill_content  # noqa: F401
    from autoskillit.recipe.registry import _RULE_REGISTRY

    registered_names = {r.name for r in _RULE_REGISTRY}
    missing = [n for n in EXPECTED_RULE_NAMES if n not in registered_names]
    assert not missing, f"Missing from rule registry: {missing}"


def test_each_rule_registered_exactly_once() -> None:
    """Each rule name appears exactly once in _RULE_REGISTRY (no duplicates)."""
    import autoskillit.recipe.rules.rules_skill_content  # noqa: F401
    from autoskillit.recipe.registry import _RULE_REGISTRY

    registered_names = [r.name for r in _RULE_REGISTRY]
    for name in EXPECTED_RULE_NAMES:
        count = registered_names.count(name)
        assert count == 1, (
            f"Rule {name!r} registered {count} times (expected exactly 1) — "
            "duplicate registration indicates a sibling module imported twice"
        )
