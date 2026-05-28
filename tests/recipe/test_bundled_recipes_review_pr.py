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

    def test_compose_pr_routes_to_guard_pr_url(self, recipe: object) -> None:
        """T_RP1: compose_pr.on_result gates on pr_url and routes to guard_pr_url.

        All queue-aware recipes (implementation, remediation, implementation-groups) insert
        guard_pr_url between compose_pr and extract_pr_number to handle graceful degradation
        when compose_pr emits an empty pr_url.
        """
        recipe_name = recipe.name  # type: ignore[attr-defined]
        step = recipe.steps["compose_pr"]  # type: ignore[attr-defined]
        assert step.on_result is not None, (
            f"{recipe_name}: compose_pr must have on_result for conditional gating"
        )
        truthy_cond = step.on_result.conditions[0]  # type: ignore[attr-defined]
        assert truthy_cond.when == "${{ result.pr_url }}", (  # type: ignore[attr-defined]
            f"{recipe_name}: compose_pr on_result[0] must gate on result.pr_url"
        )
        assert truthy_cond.route == "guard_pr_url", (  # type: ignore[attr-defined]
            f"{recipe_name}: compose_pr on_result[0] must route to guard_pr_url"
            " when pr_url is truthy"
        )
        else_cond = step.on_result.conditions[1]  # type: ignore[attr-defined]
        assert else_cond.route == "release_issue_failure", (  # type: ignore[attr-defined]
            f"{recipe_name}: compose_pr on_result else case must route to release_issue_failure"
        )
        guard = recipe.steps["guard_pr_url"]  # type: ignore[attr-defined]
        assert guard.action == "route", (
            f"{recipe_name}: guard_pr_url must be an action: route step"
        )
        routes_to_extract = any(
            c.route == "extract_pr_number" for c in (guard.on_result.conditions or [])
        )
        assert routes_to_extract, (
            f"{recipe_name}: guard_pr_url must route to extract_pr_number when pr_url is truthy"
        )

    def test_review_pr_step_exists_and_is_run_skill(self, recipe: object) -> None:
        """T_RP2: review_pr step exists and uses run_skill tool."""
        step = recipe.steps["review_pr"]  # type: ignore[attr-defined]
        assert step.tool == "run_skill"

    def test_review_pr_skipped_when_open_pr_false(self, recipe: object) -> None:
        """T_RP3: review_pr is gated by inputs.open_pr (skip_when_false)."""
        step = recipe.steps["review_pr"]  # type: ignore[attr-defined]
        assert step.skip_when_false == "inputs.open_pr"

    def test_review_pr_catch_all_routes_to_check_review_loop(self, recipe: object) -> None:
        """T_RP4: catch-all routes through check_review_loop (mandatory waypoint).

        All verdicts (approved, needs_human, any future verdict) must pass through
        check_review_loop to ensure review_loop_count is always incremented.
        """
        step = recipe.steps["review_pr"]  # type: ignore[attr-defined]
        assert step.on_result is not None
        default_conditions = [
            c for c in step.on_result.conditions if c.when is None or c.when == "true"
        ]
        assert any(c.route == "check_review_loop" for c in default_conditions), (
            "catch-all must route to check_review_loop, not directly to check_repo_ci_event"
        )

    def test_review_pr_captures_verdict(self, recipe: object) -> None:
        """T_RP4b: review_pr captures the verdict output as review_verdict to avoid clobber."""
        step = recipe.steps["review_pr"]  # type: ignore[attr-defined]
        assert "review_verdict" in step.capture
        assert step.capture["review_verdict"] == "${{ result.verdict }}"

    def test_review_pr_changes_requested_routes_to_resolve_review(self, recipe: object) -> None:
        """T_RP4c: changes_requested reaches resolve_review."""
        step = recipe.steps["review_pr"]  # type: ignore[attr-defined]
        assert step.on_result is not None
        changes_conditions = [
            c for c in step.on_result.conditions if c.when and "changes_requested" in c.when
        ]
        routes = {c.route for c in changes_conditions}
        if "enrich_diff_context" in routes:
            enrich = recipe.steps["enrich_diff_context"]  # type: ignore[attr-defined]
            assert enrich.on_success == "resolve_review"
        else:
            assert "resolve_review" in routes

    def test_review_pr_routes_to_check_repo_ci_event_on_failure(self, recipe: object) -> None:
        """T_RP5: review_pr.on_failure routes to check_repo_ci_event (no review to resolve)."""
        assert recipe.steps["review_pr"].on_failure == "check_repo_ci_event"  # type: ignore[attr-defined]

    def test_resolve_review_only_reachable_via_verdict(self, recipe: object) -> None:
        """T_RP5b: resolve_review reachable via verdict, not on_failure."""
        step = recipe.steps["review_pr"]  # type: ignore[attr-defined]
        assert step.on_failure != "resolve_review"
        assert step.on_context_limit != "resolve_review"
        verdict_routes = [
            c.route
            for c in step.on_result.conditions
            if c.route in ("resolve_review", "enrich_diff_context")
        ]
        assert len(verdict_routes) >= 1, "resolve_review must be reachable via on_result"

    def test_review_pr_failure_and_context_limit_converge(self, recipe: object) -> None:
        """T_RP5c: on_failure and on_context_limit both route to check_repo_ci_event."""
        step = recipe.steps["review_pr"]  # type: ignore[attr-defined]
        assert step.on_failure == step.on_context_limit == "check_repo_ci_event"

    def test_resolve_review_has_retries(self, recipe: object) -> None:
        """T_RP6: resolve_review has retries=2 matching resolve_ci pattern."""
        assert recipe.steps["resolve_review"].retries == 2  # type: ignore[attr-defined]

    def test_resolve_review_routes_to_pre_review_rebase(self, recipe: object) -> None:
        """T_RP7: resolve_review uses on_result: verdict dispatch routing to pre_review_rebase."""
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
        assert any("pre_review_rebase" in r for r in real_fix_routes), (
            "resolve_review on_result must route verdict=real_fix to pre_review_rebase"
        )

    def test_pre_review_rebase_routes_to_re_push_review(self, recipe: object) -> None:
        """T_RP7b: pre_review_rebase uses run_python and routes clean to re_push_review."""
        assert "pre_review_rebase" in recipe.steps  # type: ignore[operator]
        step = recipe.steps["pre_review_rebase"]  # type: ignore[attr-defined]
        assert step.tool == "run_python"
        assert step.on_success is None, "routing is via on_result, not on_success"
        assert step.on_result is not None
        clean_routes = [c.route for c in step.on_result.conditions if c.when and "clean" in c.when]
        assert "re_push_review" in clean_routes
        assert step.on_failure == "resolve_pre_review_conflicts"

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

    def test_annotate_step_captures_diff_metrics_path(self, recipe: object) -> None:
        step = recipe.steps["annotate_pr_diff"]  # type: ignore[attr-defined]
        assert "diff_metrics_path" in step.capture
        assert step.capture["diff_metrics_path"] == "${{ result.diff_metrics_path }}"

    def test_review_pr_command_includes_diff_metrics_path(self, recipe: object) -> None:
        step = recipe.steps["review_pr"]  # type: ignore[attr-defined]
        cmd = step.with_args.get("skill_command", "")
        assert "diff_metrics_path=" in cmd

    def test_resolve_review_step_uses_correct_skill(self, recipe: object) -> None:
        """resolve_review step must invoke /autoskillit:resolve-review in all recipes."""
        resolve_step = recipe.steps["resolve_review"]  # type: ignore[attr-defined]
        skill_cmd = resolve_step.with_args.get("skill_command", "")
        assert "resolve-review" in skill_cmd and "resolve-failures" not in skill_cmd, (
            "resolve_review step must call /autoskillit:resolve-review, "
            f"not resolve-failures. Got: {skill_cmd}"
        )


def test_implementation_groups_has_ci_watch() -> None:
    """T_RP10: implementation-groups now has ci_watch (parity with other recipes)."""
    recipe = load_recipe(builtin_recipes_dir() / "implementation-groups.yaml")
    assert "ci_watch" in recipe.steps
    assert "resolve_ci" in recipe.steps
    assert "re_push" in recipe.steps


def test_merge_prs_review_pr_integration_includes_diff_metrics_path() -> None:
    recipe = load_recipe(builtin_recipes_dir() / "merge-prs.yaml")
    step = recipe.steps["review_pr_integration"]
    cmd = step.with_args.get("skill_command", "")
    assert "diff_metrics_path=" in cmd


def test_merge_prs_annotate_step_captures_diff_metrics_path() -> None:
    recipe = load_recipe(builtin_recipes_dir() / "merge-prs.yaml")
    step = recipe.steps["annotate_pr_diff"]
    assert "diff_metrics_path" in step.capture


def test_merge_prs_pre_review_rebase_integration_uses_run_python() -> None:
    """merge-prs pre_review_rebase_integration must use run_python (not run_cmd)."""
    recipe = load_recipe(builtin_recipes_dir() / "merge-prs.yaml")
    step = recipe.steps["pre_review_rebase_integration"]
    assert step.tool == "run_python", (
        f"pre_review_rebase_integration must use run_python, got {step.tool!r}"
    )


def test_merge_prs_pre_review_rebase_integration_routes_to_conflict_resolution() -> None:
    """merge-prs pre_review_rebase_integration on_result must route to conflict resolution."""
    recipe = load_recipe(builtin_recipes_dir() / "merge-prs.yaml")
    step = recipe.steps["pre_review_rebase_integration"]
    assert step.on_result is not None
    routes = [c.route for c in step.on_result.conditions]
    assert "resolve_pre_review_integration_conflicts" in routes, (
        f"pre_review_rebase_integration on_result must include "
        f"resolve_pre_review_integration_conflicts, got {routes}"
    )


def test_merge_prs_resolve_pre_review_integration_conflicts_uses_merge_skill() -> None:
    """merge-prs resolve_pre_review_integration_conflicts must invoke resolve-merge-conflicts."""
    recipe = load_recipe(builtin_recipes_dir() / "merge-prs.yaml")
    assert "resolve_pre_review_integration_conflicts" in recipe.steps
    step = recipe.steps["resolve_pre_review_integration_conflicts"]
    assert step.tool == "run_skill"
    cmd = step.with_args.get("skill_command", "")
    assert "resolve-merge-conflicts" in cmd


# ---------------------------------------------------------------------------
# T4.1–T4.7: local_review_rounds wiring tests
# ---------------------------------------------------------------------------


class TestAnnotatePrDiffLocalReviewRounds:
    @pytest.fixture(
        scope="class",
        params=[
            "implementation.yaml",
            "implementation-groups.yaml",
            "remediation.yaml",
            # merge-prs.yaml is excluded: it uses annotate_pr_diff with a fixed base and does
            # not expose local_review_rounds as a recipe ingredient. Its annotate step wiring is
            # covered by test_merge_prs_annotate_step_captures_diff_metrics_path.
        ],
    )
    def recipe(self, request: pytest.FixtureRequest) -> object:
        return load_recipe(builtin_recipes_dir() / request.param)

    def test_annotate_step_captures_review_mode(self, recipe: object) -> None:
        """T4.1: annotate_pr_diff step captures review_mode."""
        step = recipe.steps["annotate_pr_diff"]
        assert "review_mode" in step.capture
        assert step.capture["review_mode"] == "${{ result.review_mode }}"

    def test_annotate_step_passes_local_review_rounds(self, recipe: object) -> None:
        """T4.2: annotate_pr_diff step passes local_review_rounds via with_args."""
        step = recipe.steps["annotate_pr_diff"]
        assert "local_review_rounds" in step.with_args

    def test_annotate_step_passes_current_iteration(self, recipe: object) -> None:
        """T4.3: annotate_pr_diff step passes current_iteration via with_args."""
        step = recipe.steps["annotate_pr_diff"]
        assert "current_iteration" in step.with_args

    def test_annotate_step_passes_base_branch(self, recipe: object) -> None:
        """T4.4: annotate_pr_diff step passes base_branch via with_args."""
        step = recipe.steps["annotate_pr_diff"]
        assert "base_branch" in step.with_args

    def test_review_pr_command_includes_mode(self, recipe: object) -> None:
        """T4.5: review_pr skill_command includes mode=${{ context.review_mode }}."""
        step = recipe.steps["review_pr"]
        cmd = step.with_args.get("skill_command", "")
        assert "mode=${{ context.review_mode }}" in cmd

    def test_resolve_review_command_includes_mode(self, recipe: object) -> None:
        """T4.6: resolve_review skill_command includes mode=${{ context.review_mode }}."""
        step = recipe.steps["resolve_review"]
        cmd = step.with_args.get("skill_command", "")
        assert "mode=${{ context.review_mode }}" in cmd

    def test_local_review_rounds_ingredient_exists(self, recipe: object) -> None:
        """T4.7: local_review_rounds is in recipe.ingredients."""
        assert "local_review_rounds" in recipe.ingredients


# ---------------------------------------------------------------------------
# T1: local_review_rounds default is not empty string (explicit numeric)
# T5: display and runtime defaults are consistent
# T7: review_max_retries default tested for all three recipes
# ---------------------------------------------------------------------------


class TestLocalReviewRoundsDefault:
    """T1 + T5: local_review_rounds must have explicit non-empty default."""

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

    def test_local_review_rounds_default_is_not_empty_string(self, recipe: object) -> None:
        """T1: local_review_rounds.default must NOT be '' in bundled recipes."""
        ing = recipe.ingredients["local_review_rounds"]
        assert ing.default != "", (
            f"{recipe.name}: local_review_rounds.default is '', "
            "which creates a shadow-default that bypasses type semantics"
        )

    def test_local_review_rounds_default_is_parseable_as_int(self, recipe: object) -> None:
        """T1: local_review_rounds.default must be parseable as int."""
        ing = recipe.ingredients["local_review_rounds"]
        assert ing.default is not None
        try:
            int(ing.default)
        except ValueError:
            pytest.fail(
                f"{recipe.name}: local_review_rounds.default={ing.default!r} "
                "is not parseable as int"
            )

    def test_local_review_rounds_type_is_integer(self, recipe: object) -> None:
        """T5: local_review_rounds must declare type=integer for semantic rule enforcement."""
        ing = recipe.ingredients["local_review_rounds"]
        assert ing.type == "integer", (
            f"{recipe.name}: local_review_rounds.type must be 'integer', got {ing.type!r}"
        )


class TestReviewMaxRetriesDefault:
    """T7: review_max_retries.default must be '3' for all three main recipes."""

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

    def test_review_max_retries_default_is_3(self, recipe: object) -> None:
        """T7: review_max_retries.default must be '3' in all bundled recipes."""
        ing = recipe.ingredients["review_max_retries"]
        assert ing.default == "3", (
            f"{recipe.name}: review_max_retries.default is {ing.default!r}, expected '3'"
        )


class TestLocalReviewRoundsAndMaxRetriesAlignment:
    """Alignment guard: local_review_rounds must be < review_max_retries for mode graduation."""

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

    def test_local_review_rounds_less_than_max_retries(self, recipe: object) -> None:
        """local_review_rounds must be < review_max_retries for mode graduation to occur.

        If local_review_rounds >= review_max_retries, the loop exits at max_exceeded
        before review_mode can transition to github, and zero GitHub comments are posted.
        """
        local_rounds_ing = recipe.ingredients["local_review_rounds"]
        max_retries_ing = recipe.ingredients["review_max_retries"]
        try:
            local_rounds = int(local_rounds_ing.default)
            max_retries = int(max_retries_ing.default)
        except (ValueError, TypeError) as exc:
            pytest.fail(f"[{recipe.name}] ingredient default is not a valid integer: {exc}")
        assert local_rounds < max_retries, (
            f"[{recipe.name}] local_review_rounds ({local_rounds}) >= review_max_retries "
            f"({max_retries}). Mode will never transition to github with default config."
        )


class TestPreReviewRebaseConflictResolution:
    """Verify pre_review_rebase uses run_python with conflict routing in 3 recipes.

    merge-prs uses different step names; covered by standalone tests.
    """

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

    def test_pre_review_rebase_uses_run_python(self, recipe: object) -> None:
        step = recipe.steps["pre_review_rebase"]
        assert step.tool == "run_python", (
            f"{recipe.name}: pre_review_rebase must use run_python, got {step.tool!r}"
        )

    def test_pre_review_rebase_on_result_routes_to_conflict_resolution(
        self, recipe: object
    ) -> None:
        step = recipe.steps["pre_review_rebase"]
        assert step.on_result is not None
        routes = [c.route for c in step.on_result.conditions]
        assert "resolve_pre_review_conflicts" in routes, (
            f"{recipe.name}: pre_review_rebase on_result must include resolve_pre_review_conflicts"
        )

    def test_resolve_pre_review_conflicts_uses_merge_conflicts_skill(self, recipe: object) -> None:
        assert "resolve_pre_review_conflicts" in recipe.steps
        step = recipe.steps["resolve_pre_review_conflicts"]
        assert step.tool == "run_skill"
        cmd = step.with_args.get("skill_command", "")
        assert "resolve-merge-conflicts" in cmd
