"""Tests for on_rate_limit parity with on_context_limit across bundled recipes."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]


class TestRateLimitParity:
    def test_implementation_fix_steps_have_rate_limit(self) -> None:
        recipes_dir = builtin_recipes_dir()
        for name in ["implementation", "implementation-groups"]:
            recipe = load_recipe(recipes_dir / f"{name}.yaml")
            for step_name in ("fix", "merge_gate_fix"):
                step = recipe.steps.get(step_name)
                assert step is not None, f"{name}: missing step {step_name}"
                if step.on_context_limit:
                    assert step.on_rate_limit is not None, (
                        f"{name}/{step_name}: has on_context_limit but no on_rate_limit"
                    )
                    assert step.on_rate_limit == step.on_context_limit, (
                        f"{name}/{step_name}: on_rate_limit ({step.on_rate_limit}) "
                        f"!= on_context_limit ({step.on_context_limit})"
                    )

    def test_rate_limit_matches_context_limit_target(self) -> None:
        recipes_dir = builtin_recipes_dir()
        for name in ["implementation", "remediation", "implementation-groups"]:
            recipe = load_recipe(recipes_dir / f"{name}.yaml")
            for step_name, step in recipe.steps.items():
                if not step.on_result:
                    continue
                if step.on_rate_limit and step.on_context_limit:
                    assert step.on_rate_limit == step.on_context_limit, (
                        f"{name}/{step_name}: on_rate_limit ({step.on_rate_limit}) "
                        f"!= on_context_limit ({step.on_context_limit})"
                    )
