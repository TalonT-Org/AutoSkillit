"""Tests for run-skill-missing-rate-limit semantic rule against bundled recipes.

This replaces the legacy parity test that asserted ``on_rate_limit ==
on_context_limit`` for ``fix`` / ``merge_gate_fix`` in implementation recipes.
That assertion encoded a wrong invariant (transient and structural failures
should NOT be treated as equivalent — the sous-chef fallback that borrows
on_context_limit for rate-limit errors is the bug being fixed). The new
behavior is the unconditional ``run-skill-missing-rate-limit`` rule that
fires WARNING for any run_skill step missing ``on_rate_limit`` in raw YAML.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe._api_listing import validate_from_path
from autoskillit.recipe.io import all_validated_recipe_paths, load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ALL_PATHS = all_validated_recipe_paths(_PROJECT_ROOT)
_BUNDLED_ONLY = [p for p in _ALL_PATHS if "src/autoskillit/recipes" in str(p)]
assert _BUNDLED_ONLY, "no bundled recipes found"


def _has_run_skill_steps(recipe_path: Path) -> bool:
    """Return True if the recipe has at least one run_skill step in raw YAML.

    Recipes with no run_skill steps cannot trigger the
    run-skill-missing-rate-limit rule, so the test should treat them as
    inherently compliant.
    """
    recipe = load_recipe(recipe_path)
    return any(step.tool == "run_skill" for step in recipe.steps.values())


class TestRateLimitRuleFiring:
    """Verify the new semantic rule fires correctly across bundled recipes."""

    @pytest.mark.parametrize("recipe_path", _BUNDLED_ONLY, ids=lambda p: p.stem)
    def test_rule_does_not_block_validation(self, recipe_path: Path) -> None:
        """The run-skill-missing-rate-limit rule fires WARNING findings for
        any run_skill step missing on_rate_limit in the raw YAML, and stays
        silent once every run_skill step has on_rate_limit. WARNING severity
        must NOT block recipe validation — the allowlist ratchet
        (``_RATE_LIMIT_COMPLIANT_RECIPES``) in
        ``test_bundled_recipes_behavioral_properties`` is the hard gate.

        The :func:`validate_from_path` API returns ``dict[str, Any]``. The
        ``"findings"`` value is ``list[dict[str, str]]`` with keys ``"rule"``,
        ``"severity"`` (lowercase string via ``Severity.value``), ``"step"``,
        ``"message"``. Each item is a plain dict — not a ``RuleFinding``
        dataclass instance.

        Recipes without any run_skill steps (e.g. consolidate-health-reports,
        research-archive, promote-to-main, research-campaign) are inherently
        compliant — the rule has nothing to flag and zero findings is correct.
        """
        from tests.recipe.test_bundled_recipes_behavioral_properties import (
            _RATE_LIMIT_COMPLIANT_RECIPES,
        )

        stem = recipe_path.stem
        if not _has_run_skill_steps(recipe_path):
            # No run_skill steps means the rule has nothing to flag.
            return

        result = validate_from_path(recipe_path)
        rate_limit_warnings = [
            f
            for f in result["findings"]
            if f["rule"] == "run-skill-missing-rate-limit" and f["severity"] == "warning"
        ]
        # The rule's presence/absence depends on whether the recipe is in
        # _RATE_LIMIT_COMPLIANT_RECIPES (set in
        # test_bundled_recipes_behavioral_properties.py). At time of writing,
        # that set is empty (Part B adds compliance), so non-compliant
        # recipes must produce at least one warning.
        if stem in _RATE_LIMIT_COMPLIANT_RECIPES:
            assert not rate_limit_warnings, (
                f"{stem}: expected zero run-skill-missing-rate-limit findings "
                f"for a compliant recipe, got {rate_limit_warnings}"
            )
        else:
            assert rate_limit_warnings, (
                f"{stem}: expected run-skill-missing-rate-limit findings to "
                f"document current non-compliance, got none"
            )
