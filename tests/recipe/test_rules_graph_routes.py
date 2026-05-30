"""Tests for recipe/rules/rules_graph_routes.py semantic rules."""

from __future__ import annotations

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(
        name="test-rules-graph-routes",
        description="Test recipe for rules_graph_routes rules.",
        version="0.2.0",
        kitchen_rules=["test"],
        steps=steps,
    )


class TestRateLimitRouteMissing:
    """rate-limit-route-missing: on_context_limit without on_rate_limit fires a WARNING."""

    def test_RRLM1_skill_step_on_context_limit_no_on_rate_limit_fires_warning(self) -> None:
        """RRLM1: run_skill step with on_context_limit but no on_rate_limit → WARNING."""
        recipe = _make_recipe(
            {
                "impl": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:make-plan spec.md"},
                    on_context_limit="handle_ctx",
                ),
                "handle_ctx": RecipeStep(action="stop", message="Context limit."),
            }
        )
        findings = run_semantic_rules(recipe)
        warnings = [f for f in findings if f.severity == Severity.WARNING]
        assert any(
            f.rule == "rate-limit-route-missing" and f.step_name == "impl" for f in warnings
        ), (
            f"Expected rate-limit-route-missing WARNING on 'impl'."
            f" Got: {[f.rule for f in warnings]}"
        )

    def test_RRLM2_skill_step_both_routes_no_finding(self) -> None:
        """RRLM2: run_skill step with on_context_limit AND on_rate_limit → rule does not fire."""
        recipe = _make_recipe(
            {
                "impl": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:make-plan spec.md"},
                    on_context_limit="handle_ctx",
                    on_rate_limit="handle_rate",
                ),
                "handle_ctx": RecipeStep(action="stop", message="Context limit."),
                "handle_rate": RecipeStep(action="stop", message="Rate limit."),
            }
        )
        findings = run_semantic_rules(recipe)
        assert not any(f.rule == "rate-limit-route-missing" for f in findings)

    def test_RRLM3_non_skill_tool_exempt(self) -> None:
        """RRLM3: non-run_skill tool is exempt from rate-limit-route-missing."""
        recipe = _make_recipe(
            {
                "check": RecipeStep(
                    tool="run_cmd",
                    with_args={"cmd": "echo test"},
                    on_context_limit="handle_ctx",
                ),
                "handle_ctx": RecipeStep(action="stop", message="Done."),
            }
        )
        findings = run_semantic_rules(recipe)
        assert not any(f.rule == "rate-limit-route-missing" for f in findings)
