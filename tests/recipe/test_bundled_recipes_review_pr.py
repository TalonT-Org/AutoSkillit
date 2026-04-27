"""Tests for review-PR integration across pipeline recipe variants."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestReviewPrRecipeIntegration:
    @pytest.fixture(
        scope="class",
        params=[
            "implementation.yaml",
            "implementation-groups.yaml",
            "remediation.yaml",
        ],
    )
    def recipe(self, request: pytest.FixtureRequest) -> object:
        return load_recipe(builtin_recipes_dir() / request.param)

    def test_compose_pr_routes_to_extract_pr_number(self, recipe: object) -> None:
        """T_RP1: compose_pr.on_success routes per-recipe to extract_pr_number.

        All queue-aware recipes (implementation, remediation, implementation-groups) insert
        extract_pr_number between compose_pr and review_pr to capture the PR number for
        merge queue support.
        """
        recipe_name = recipe.name  # type: ignore[attr-defined]
        on_success = recipe.steps["compose_pr"].on_success  # type: ignore[attr-defined]
        assert on_success == "extract_pr_number", (
            f"{recipe_name}: compose_pr.on_success must be 'extract_pr_number', got {on_success!r}"
        )

    def test_review_pr_step_exists_and_is_run_skill(self, recipe: object) -> None:
        """T_RP2: review_pr step exists and uses run_skill tool."""
        step = recipe.steps["review_pr"]  # type: ignore[attr-defined]
        assert step.tool == "run_skill"

    def test_review_pr_skipped_when_open_pr_false(self, recipe: object) -> None:
        """T_RP3: review_pr is gated by inputs.open_pr (skip_when_false)."""
        step = recipe.steps["review_pr"]  # type: ignore[attr-defined]
        assert step.skip_when_false == "inputs.open_pr"

    def test_review_pr_routes_to_ci_watch_on_success(self, recipe: object) -> None:
        """T_RP4: review_pr has on_result with catch-all route to check_repo_ci_event."""
        step = recipe.steps["review_pr"]  # type: ignore[attr-defined]
        assert step.on_result is not None
        default_conditions = [
            c for c in step.on_result.conditions if c.when is None or c.when == "true"
        ]
        assert any(c.route == "check_repo_ci_event" for c in default_conditions)

    def test_review_pr_captures_verdict(self, recipe: object) -> None:
        """T_RP4b: review_pr captures the verdict output as review_verdict to avoid clobber."""
        step = recipe.steps["review_pr"]  # type: ignore[attr-defined]
        assert "review_verdict" in step.capture
        assert step.capture["review_verdict"] == "${{ result.verdict }}"

    def test_review_pr_changes_requested_routes_to_resolve_review(self, recipe: object) -> None:
        """T_RP4c: on_result routes changes_requested verdict to resolve_review."""
        step = recipe.steps["review_pr"]  # type: ignore[attr-defined]
        assert step.on_result is not None
        changes_conditions = [
            c for c in step.on_result.conditions if c.when and "changes_requested" in c.when
        ]
        assert any(c.route == "resolve_review" for c in changes_conditions)

    def test_review_pr_routes_to_resolve_review_on_failure(self, recipe: object) -> None:
        """T_RP5: review_pr.on_failure routes to resolve_review."""
        assert recipe.steps["review_pr"].on_failure == "resolve_review"  # type: ignore[attr-defined]

    def test_resolve_review_has_retries(self, recipe: object) -> None:
        """T_RP6: resolve_review has retries=2 matching resolve_ci pattern."""
        assert recipe.steps["resolve_review"].retries == 2  # type: ignore[attr-defined]

    def test_resolve_review_routes_to_re_push_review(self, recipe: object) -> None:
        """T_RP7: resolve_review uses on_result: verdict dispatch routing to re_push_review."""
        step = recipe.steps["resolve_review"]  # type: ignore[attr-defined]
        assert step.on_success is None, (
            "resolve_review must use on_result: verdict dispatch, not unconditional on_success"
        )
        assert step.on_result is not None, (
            "resolve_review must have on_result: block for verdict-gated routing"
        )
        real_fix_routes = [
            c.route for c in step.on_result.conditions if c.when and "real_fix" in c.when
        ]
        assert any("re_push_review" in r for r in real_fix_routes), (
            "resolve_review on_result must route verdict=real_fix to re_push_review"
        )

    def test_re_push_review_routes_to_check_review_loop(self, recipe: object) -> None:
        """T_RP8: re_push_review routes to check_review_loop (bounded retry gate)."""
        assert recipe.steps["re_push_review"].on_success == "check_review_loop"  # type: ignore[attr-defined]

    def test_ci_watch_present(self, recipe: object) -> None:
        """T_RP9: ci_watch step present in all four recipes."""
        assert "ci_watch" in recipe.steps  # type: ignore[attr-defined]

    def test_review_pr_needs_human_has_explicit_route(self, recipe: object) -> None:
        """needs_human must have a dedicated on_result route in every recipe."""
        review_pr_step = recipe.steps["review_pr"]  # type: ignore[attr-defined]
        explicit_conditions = [
            c.when
            for c in review_pr_step.on_result.conditions
            if c.when and "needs_human" in c.when and c.when.strip() != "true"
        ]
        assert len(explicit_conditions) >= 1, (
            "review_pr on_result must have an explicit condition for 'needs_human'. "
            "It must not silently fall through the catch-all."
        )

    def test_resolve_review_step_uses_correct_skill(self, recipe: object) -> None:
        """resolve_review step must invoke /autoskillit:resolve-review in all recipes."""
        resolve_step = recipe.steps["resolve_review"]  # type: ignore[attr-defined]
        skill_cmd = resolve_step.with_args.get("skill_command", "")
        assert "resolve-review" in skill_cmd and "resolve-failures" not in skill_cmd, (
            "resolve_review step must call /autoskillit:resolve-review, "
            f"not resolve-failures. Got: {skill_cmd}"
        )
