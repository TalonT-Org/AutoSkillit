from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import validate_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestResearchReviewRecipe:
    @pytest.fixture(scope="class")
    def recipe(self):
        return load_recipe(builtin_recipes_dir() / "research-review.yaml")

    # --- Header ---
    def test_loads_without_exception(self, recipe) -> None:
        assert recipe is not None

    def test_validates_with_zero_errors(self, recipe) -> None:
        errors = validate_recipe(recipe)
        assert errors == [], f"Validation errors: {errors}"

    def test_recipe_name(self, recipe) -> None:
        assert recipe.name == "research-review"

    def test_recipe_version(self, recipe) -> None:
        assert recipe.recipe_version == "1.0.0"

    def test_categories(self, recipe) -> None:
        assert recipe.categories == ["research-family"]

    def test_requires_packs(self, recipe) -> None:
        assert set(recipe.requires_packs) == {"research", "exp-lens", "vis-lens"}

    def test_no_autoskillit_version(self) -> None:
        path = builtin_recipes_dir() / "research-review.yaml"
        content = path.read_text()
        assert "autoskillit_version" not in content

    # --- Ingredients ---
    def test_user_ingredient_count(self, recipe) -> None:
        user_ingredients = {k: v for k, v in recipe.ingredients.items() if not v.hidden}
        # 7 user-facing + 8 campaign-passed (not hidden) = 15 total
        assert len(user_ingredients) == 15

    def test_hidden_ingredient_count(self, recipe) -> None:
        hidden = {k: v for k, v in recipe.ingredients.items() if v.hidden}
        assert len(hidden) == 0

    def test_user_ingredients_present(self, recipe) -> None:
        names = set(recipe.ingredients.keys())
        assert {
            "task",
            "source_dir",
            "base_branch",
            "output_mode",
            "review_pr",
            "audit_claims",
            "issue_url",
        } <= names

    def test_task_required(self, recipe) -> None:
        assert recipe.ingredients["task"].required is True

    def test_source_dir_required(self, recipe) -> None:
        assert recipe.ingredients["source_dir"].required is True

    def test_base_branch_default(self, recipe) -> None:
        assert recipe.ingredients["base_branch"].default == "main"

    def test_output_mode_default(self, recipe) -> None:
        assert recipe.ingredients["output_mode"].default == "local"

    def test_review_pr_default(self, recipe) -> None:
        assert recipe.ingredients["review_pr"].default == "false"

    def test_audit_claims_default(self, recipe) -> None:
        assert recipe.ingredients["audit_claims"].default == "false"

    # --- Steps ---
    def test_step_count(self, recipe) -> None:
        # 22 active steps + 3 terminal stops = 25
        assert len(recipe.steps) == 25

    def test_active_step_names(self, recipe) -> None:
        expected = {
            "prepare_research_pr",
            "run_experiment_lenses",
            "stage_bundle",
            "route_pr_or_local",
            "compose_research_pr",
            "guard_pr_url",
            "review_research_pr",
            "audit_claims",
            "route_review_resolve",
            "resolve_research_review",
            "route_claims_resolve",
            "resolve_claims_review",
            "merge_escalations",
            "re_run_experiment",
            "re_generate_report",
            "re_stage_bundle",
            "re_test",
            "re_push_research",
            "finalize_bundle",
            "finalize_bundle_render",
            "route_archive_or_export",
            "export_local_bundle",
        }
        assert expected <= set(recipe.steps.keys())

    def test_terminal_stops_present(self, recipe) -> None:
        assert recipe.steps["review_pr_complete"].action == "stop"
        assert recipe.steps["review_local_complete"].action == "stop"
        assert recipe.steps["escalate_stop"].action == "stop"

    # --- Key routing adaptations ---
    def test_prepare_research_pr_uses_context_worktree_path(self) -> None:
        path = builtin_recipes_dir() / "research-review.yaml"
        content = path.read_text()
        # prepare_research_pr should reference context.worktree_path in skill_command and cwd
        lines_with_ctx_wt = [
            line for line in content.splitlines() if "context.worktree_path" in line
        ]
        # context.worktree_path should appear at least twice (skill_command + cwd)
        assert len(lines_with_ctx_wt) >= 2

    def test_finalize_bundle_routes_to_finalize_bundle_render(self, recipe) -> None:
        step = recipe.steps["finalize_bundle"]
        assert step.on_success == "finalize_bundle_render"

    def test_finalize_bundle_failure_routes_to_escalate_stop(self, recipe) -> None:
        step = recipe.steps["finalize_bundle"]
        assert step.on_failure == "escalate_stop"

    def test_route_archive_or_export_pr_fallthrough_to_review_pr_complete(self, recipe) -> None:
        step = recipe.steps["route_archive_or_export"]
        # The fallback (non-local) route should be review_pr_complete
        conditions = step.on_result.conditions if step.on_result else []
        fallback = [c for c in conditions if c.when is None]
        assert len(fallback) == 1
        assert fallback[0].route == "review_pr_complete"

    def test_export_local_bundle_routes_to_review_local_complete(self, recipe) -> None:
        step = recipe.steps["export_local_bundle"]
        assert step.on_success == "review_local_complete"
        assert step.on_failure == "review_local_complete"

    def test_guard_pr_url_fallback_to_review_pr_complete(self, recipe) -> None:
        step = recipe.steps["guard_pr_url"]
        conditions = step.on_result.conditions if step.on_result else []
        fallback = [c for c in conditions if c.when is None]
        assert len(fallback) == 1
        assert fallback[0].route == "review_pr_complete"

    def test_re_push_research_routes_to_finalize_bundle_render(self, recipe) -> None:
        step = recipe.steps["re_push_research"]
        assert step.on_success == "finalize_bundle_render"

    # --- Negative guards ---
    def test_no_archival_steps(self, recipe) -> None:
        step_names = set(recipe.steps.keys())
        archival_steps = {
            "begin_archival",
            "capture_experiment_branch",
            "create_artifact_branch",
            "open_artifact_pr",
            "tag_experiment_branch",
            "close_experiment_pr",
            "patch_token_summary",
            "research_complete",
        }
        assert archival_steps.isdisjoint(step_names)

    def test_no_patch_token_summary_references(self) -> None:
        path = builtin_recipes_dir() / "research-review.yaml"
        content = path.read_text()
        assert "patch_token_summary" not in content

    def test_no_begin_archival_references(self) -> None:
        path = builtin_recipes_dir() / "research-review.yaml"
        content = path.read_text()
        assert "begin_archival" not in content

    # --- Terminal stop sentinels ---
    def test_review_pr_complete_sentinel_fields(self, recipe) -> None:
        msg = recipe.steps["review_pr_complete"].message
        assert "pr_url" in msg
        assert "report_path_after_finalize" in msg
        assert "worktree_path" in msg
        assert "research_dir" in msg

    def test_review_local_complete_sentinel_fields(self, recipe) -> None:
        msg = recipe.steps["review_local_complete"].message
        assert "local_bundle_path" in msg

    # --- kitchen_rules ---
    def test_kitchen_rules_not_empty(self, recipe) -> None:
        assert len(recipe.kitchen_rules) >= 4
