"""Smoke test for the rules_skill_content compatibility facade.

The bulk of the legacy `tests/recipe/test_rules_skill_content.py` was split
into four per-family test files in `tests/recipe/rules_skills/` as part of
the #4852 decomposition. This file retains only the rule-registry smoke
test that ensures the facade still imports the four sibling rule modules
and registers all 15 rules exactly once.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_rules_skill_content_facade_registers_all_fifteen_rule_names() -> None:
    """Importing the facade must register all 15 SKILL.md semantic rules."""
    import autoskillit.recipe.rules.rules_skill_content  # noqa: F401
    from autoskillit.recipe.registry import _RULE_REGISTRY

    expected_names = {
        "undefined-bash-placeholder",
        "hardcoded-origin-remote",
        "blind-git-add-in-skill",
        "interpreter-mediated-write-in-skill",
        "no-autoskillit-import-in-skill-python-block",
        "posix-char-class-in-skill",
        "grep-bre-alternation-in-skill",
        "output-section-no-markdown-directive",
        "skill-no-issue-comments",
        "transition-boundary-anti-confirmation",
        "executable-field-content-validity",
        "reviews-post-requires-input-flag",
        "source-attribution-directive",
        "graphql-query-requires-shell-invocation",
        "inline-content-in-subagent-prompt",
    }
    registered_names = {r.name for r in _RULE_REGISTRY}
    missing = expected_names - registered_names
    assert not missing, f"Facade failed to register these rule names: {missing}"
