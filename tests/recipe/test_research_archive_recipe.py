from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import validate_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestResearchArchiveRecipe:
    @pytest.fixture(scope="class")
    def recipe(self):
        return load_recipe(builtin_recipes_dir() / "research-archive.yaml")

    # ── Header ──────────────────────────────────────────────────────

    def test_validates_with_zero_errors(self, recipe) -> None:
        errors = validate_recipe(recipe)
        assert errors == [], f"Validation errors: {errors}"

    def test_recipe_name(self, recipe) -> None:
        assert recipe.name == "research-archive"

    def test_recipe_version(self, recipe) -> None:
        assert recipe.recipe_version == "1.0.0"

    def test_categories(self, recipe) -> None:
        assert recipe.categories == ["research-family"]

    def test_requires_packs(self, recipe) -> None:
        assert set(recipe.requires_packs) == {"github", "ci"}

    def test_no_autoskillit_version(self) -> None:
        # Intentionally does NOT use self.recipe fixture — validates raw file content
        path = builtin_recipes_dir() / "research-archive.yaml"
        content = path.read_text()
        assert "autoskillit_version" not in content

    # ── Ingredients ─────────────────────────────────────────────────

    def test_ingredient_count(self, recipe) -> None:
        assert len(recipe.ingredients) == 4

    def test_campaign_sourced_ingredients_hidden_and_required(self, recipe) -> None:
        for name in ("worktree_path", "research_dir", "pr_url"):
            ing = recipe.ingredients[name]
            assert ing.required is True, f"{name} should be required"
            assert ing.hidden is True, f"{name} should be hidden"

    def test_base_branch_default(self, recipe) -> None:
        assert recipe.ingredients["base_branch"].default == "main"

    def test_base_branch_not_hidden(self, recipe) -> None:
        assert recipe.ingredients["base_branch"].hidden is False

    # ── Steps ───────────────────────────────────────────────────────

    def test_step_count(self, recipe) -> None:
        assert len(recipe.steps) == 9

    def test_step_names(self, recipe) -> None:
        expected = {
            "begin_archival",
            "capture_experiment_branch",
            "create_artifact_branch",
            "open_artifact_pr",
            "tag_experiment_branch",
            "close_experiment_pr",
            "patch_token_summary",
            "research_complete",
            "escalate_stop",
        }
        assert set(recipe.steps.keys()) == expected

    # ── Critical routing fix: inputs.pr_url ─────────────────────────

    def test_begin_archival_uses_inputs_pr_url(self, recipe) -> None:
        step = recipe.steps["begin_archival"]
        assert step.action == "route"
        conditions = step.on_result.conditions
        pr_cond = next(
            (c for c in conditions if c.when and "inputs.pr_url" in c.when),
            None,
        )
        assert pr_cond is not None, "begin_archival must route on inputs.pr_url"
        assert pr_cond.route == "capture_experiment_branch"

    def test_begin_archival_fallback_routes_to_patch(self, recipe) -> None:
        step = recipe.steps["begin_archival"]
        conditions = step.on_result.conditions
        fallback = [c for c in conditions if c.when is None]
        assert len(fallback) == 1
        assert fallback[0].route == "patch_token_summary"

    def test_no_context_pr_url_in_recipe(self) -> None:
        # Intentionally does NOT use self.recipe fixture — validates raw file content
        content = (builtin_recipes_dir() / "research-archive.yaml").read_text()
        assert "context.pr_url" not in content

    # ── Capture fields ──────────────────────────────────────────────

    def test_capture_experiment_branch(self, recipe) -> None:
        step = recipe.steps["capture_experiment_branch"]
        assert "experiment_branch" in step.capture
        assert step.on_success == "create_artifact_branch"
        assert step.on_failure == "patch_token_summary"

    def test_create_artifact_branch_capture(self, recipe) -> None:
        step = recipe.steps["create_artifact_branch"]
        assert "artifact_branch" in step.capture
        assert step.on_success == "open_artifact_pr"

    def test_open_artifact_pr_capture(self, recipe) -> None:
        step = recipe.steps["open_artifact_pr"]
        assert "artifact_pr_url" in step.capture
        assert step.on_success == "tag_experiment_branch"

    def test_open_artifact_pr_uses_inputs_pr_url(self, recipe) -> None:
        step = recipe.steps["open_artifact_pr"]
        assert "inputs.pr_url" in step.with_args.get("cmd", "")

    def test_tag_experiment_branch_capture(self, recipe) -> None:
        step = recipe.steps["tag_experiment_branch"]
        assert "archive_tag" in step.capture
        assert step.on_success == "close_experiment_pr"

    def test_close_experiment_pr_uses_inputs_pr_url(self, recipe) -> None:
        step = recipe.steps["close_experiment_pr"]
        assert "inputs.pr_url" in step.with_args.get("cmd", "")

    # ── Graceful degradation ────────────────────────────────────────

    def test_all_failures_route_to_patch_token_summary(self, recipe) -> None:
        for name in (
            "capture_experiment_branch",
            "create_artifact_branch",
            "open_artifact_pr",
            "tag_experiment_branch",
            "close_experiment_pr",
        ):
            step = recipe.steps[name]
            assert step.on_failure == "patch_token_summary", (
                f"{name} on_failure should be patch_token_summary"
            )

    # ── patch_token_summary ─────────────────────────────────────────

    def test_patch_token_summary_callable(self, recipe) -> None:
        step = recipe.steps["patch_token_summary"]
        assert step.tool == "run_python"
        assert step.with_args["callable"] == "autoskillit.smoke_utils.patch_pr_token_summary"

    def test_patch_token_summary_uses_inputs_pr_url(self, recipe) -> None:
        step = recipe.steps["patch_token_summary"]
        assert step.with_args["pr_url"] == "${{ inputs.pr_url }}"

    def test_patch_token_summary_routes_to_complete(self, recipe) -> None:
        step = recipe.steps["patch_token_summary"]
        assert step.on_success == "research_complete"
        assert step.on_failure == "escalate_stop"

    # ── Terminal stops ──────────────────────────────────────────────

    def test_research_complete_is_stop(self, recipe) -> None:
        step = recipe.steps["research_complete"]
        assert step.action == "stop"
        assert len(step.message) >= 10

    def test_escalate_stop_is_stop(self, recipe) -> None:
        step = recipe.steps["escalate_stop"]
        assert step.action == "stop"
        assert len(step.message) >= 10

    def test_escalate_stop_is_reachable(self, recipe) -> None:
        routers = [
            name
            for name, step in recipe.steps.items()
            if step.on_failure == "escalate_stop" or step.on_success == "escalate_stop"
        ]
        assert routers, "escalate_stop must be reachable from at least one step"

    # ── Kitchen rules ───────────────────────────────────────────────

    def test_kitchen_rules_present(self, recipe) -> None:
        assert len(recipe.kitchen_rules) >= 3

    def test_kitchen_rules_forbid_native_tools(self, recipe) -> None:
        combined = " ".join(recipe.kitchen_rules)
        for tool in (
            "Read",
            "Grep",
            "Glob",
            "Edit",
            "Write",
            "Bash",
            "Agent",
            "WebFetch",
            "WebSearch",
            "NotebookEdit",
        ):
            assert tool in combined, f"kitchen_rules must mention {tool}"

    # ── cwd uses inputs.worktree_path ───────────────────────────────

    def test_capture_experiment_branch_cwd(self, recipe) -> None:
        step = recipe.steps["capture_experiment_branch"]
        assert step.with_args.get("cwd") == "${{ inputs.worktree_path }}"

    def test_patch_token_summary_cwd(self, recipe) -> None:
        step = recipe.steps["patch_token_summary"]
        assert step.with_args.get("cwd") == "${{ inputs.worktree_path }}"
