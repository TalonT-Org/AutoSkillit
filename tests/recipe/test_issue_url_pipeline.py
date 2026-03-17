"""Tests for issue_url ingredient threading across the three PR-opening recipes."""

from pathlib import Path

import yaml

from autoskillit.recipe._api import validate_from_path
from autoskillit.recipe.io import load_recipe

RECIPES_DIR = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "recipes"


def _recipe_path(name: str) -> Path:
    return RECIPES_DIR / f"{name}.yaml"


class TestImplementationPipelineIssueUrl:
    def test_recipe_validates_clean(self):
        """implementation must validate with no errors after adding issue_url."""
        result = validate_from_path(_recipe_path("implementation"))
        errors = [f for f in result.get("findings", []) if f.get("severity") == "error"]
        assert errors == [], f"Unexpected errors: {errors}"

    def test_issue_url_ingredient_declared(self):
        """issue_url ingredient must be declared as optional with no default."""
        data = yaml.safe_load(_recipe_path("implementation").read_text())
        assert "issue_url" in data["ingredients"]
        ing = data["ingredients"]["issue_url"]
        assert ing.get("required", False) is False
        assert ing.get("default") is None

    def test_no_fetch_issue_step(self):
        """fetch_issue step must NOT exist — orchestrator no longer fetches issue content."""
        data = yaml.safe_load(_recipe_path("implementation").read_text())
        assert "fetch_issue" not in data["steps"]

    def test_get_issue_title_step_present(self):
        """get_issue_title step must exist with correct structure."""
        data = yaml.safe_load(_recipe_path("implementation").read_text())
        assert "get_issue_title" in data["steps"]
        assert "parse_issue_number" not in data["steps"]
        step = data["steps"]["get_issue_title"]
        assert step["tool"] == "get_issue_title"
        assert step.get("optional") is True
        assert step.get("skip_when_false") == "inputs.issue_url"
        assert "issue_number" in step.get("capture", {})
        assert "issue_title" in step.get("capture", {})
        assert "issue_slug" in step.get("capture", {})

    def test_get_issue_title_between_set_merge_target_and_create_branch(self):
        """get_issue_title must be positioned after set_merge_target, before create_branch."""
        data = yaml.safe_load(_recipe_path("implementation").read_text())
        assert data["steps"]["set_merge_target"]["on_success"] == "get_issue_title"
        assert data["steps"]["get_issue_title"]["on_success"] == "claim_issue"

    def test_create_branch_uses_slug_fallback(self):
        """create_branch shell uses ${SLUG:-$RUN} pattern."""
        data = yaml.safe_load(_recipe_path("implementation").read_text())
        cmd = data["steps"]["compute_branch"]["with"]["cmd"]
        assert "SLUG" in cmd
        assert "${SLUG:-" in cmd

    def test_issue_url_referenced_in_downstream_skill_step(self):
        """plan step must reference inputs.issue_url, not issue_content."""
        data = yaml.safe_load(_recipe_path("implementation").read_text())
        skill_step_with = data["steps"]["plan"].get("with", {})
        assert any("issue_url" in str(v) for v in skill_step_with.values())
        assert not any("issue_content" in str(v) for v in skill_step_with.values())

    def test_issue_number_referenced_in_open_pr_step(self):
        """open_pr_step must reference context.issue_number in with: for dataflow tracking."""
        data = yaml.safe_load(_recipe_path("implementation").read_text())
        openpr_with = data["steps"]["open_pr_step"].get("with", {})
        assert any("issue_number" in str(v) for v in openpr_with.values())

    def test_no_dead_output_for_issue_number(self):
        """issue_number captured by get_issue_title must not be a dead output."""
        from autoskillit.recipe.validator import analyze_dataflow

        recipe = load_recipe(_recipe_path("implementation"))
        report = analyze_dataflow(recipe)
        dead = [
            w for w in report.warnings if w.code == "DEAD_OUTPUT" and w.field == "issue_number"
        ]
        assert dead == [], f"Unexpected DEAD_OUTPUT for issue_number: {dead}"

    def test_no_issue_content_dead_output(self):
        """issue_content must not appear in captures at all — it is no longer fetched."""
        from autoskillit.recipe.validator import analyze_dataflow

        recipe = load_recipe(_recipe_path("implementation"))
        report = analyze_dataflow(recipe)
        issue_content_dead = [
            w for w in report.warnings if w.code == "DEAD_OUTPUT" and w.field == "issue_content"
        ]
        assert issue_content_dead == []
        data = yaml.safe_load(_recipe_path("implementation").read_text())
        for step_name, step in data["steps"].items():
            captures = step.get("capture", {})
            assert "issue_content" not in captures, (
                f"Step '{step_name}' must not capture issue_content"
            )


class TestInvestigateFirstIssueUrl:
    def test_recipe_validates_clean(self):
        result = validate_from_path(_recipe_path("remediation"))
        errors = [f for f in result.get("findings", []) if f.get("severity") == "error"]
        assert errors == [], f"Unexpected errors: {errors}"

    def test_issue_url_ingredient_declared(self):
        data = yaml.safe_load(_recipe_path("remediation").read_text())
        assert "issue_url" in data["ingredients"]
        ing = data["ingredients"]["issue_url"]
        assert ing.get("required", False) is False
        assert ing.get("default") is None

    def test_no_fetch_issue_step(self):
        """fetch_issue step must NOT exist — orchestrator no longer fetches issue content."""
        data = yaml.safe_load(_recipe_path("remediation").read_text())
        assert "fetch_issue" not in data["steps"]

    def test_get_issue_title_step_present(self):
        """get_issue_title step must exist with correct structure."""
        data = yaml.safe_load(_recipe_path("remediation").read_text())
        assert "get_issue_title" in data["steps"]
        assert "parse_issue_number" not in data["steps"]
        step = data["steps"]["get_issue_title"]
        assert step["tool"] == "get_issue_title"
        assert step.get("optional") is True
        assert step.get("skip_when_false") == "inputs.issue_url"
        assert "issue_number" in step.get("capture", {})
        assert "issue_title" in step.get("capture", {})
        assert "issue_slug" in step.get("capture", {})

    def test_get_issue_title_between_set_merge_target_and_create_branch(self):
        """get_issue_title must be positioned after set_merge_target, before create_branch."""
        data = yaml.safe_load(_recipe_path("remediation").read_text())
        assert data["steps"]["set_merge_target"]["on_success"] == "get_issue_title"
        assert data["steps"]["get_issue_title"]["on_success"] == "claim_issue"

    def test_create_branch_uses_slug_fallback(self):
        """create_branch shell uses ${SLUG:-$RUN} pattern."""
        data = yaml.safe_load(_recipe_path("remediation").read_text())
        cmd = data["steps"]["compute_branch"]["with"]["cmd"]
        assert "SLUG" in cmd
        assert "${SLUG:-" in cmd

    def test_issue_url_referenced_in_downstream_skill_step(self):
        """investigate step must reference inputs.issue_url, not issue_content."""
        data = yaml.safe_load(_recipe_path("remediation").read_text())
        skill_step_with = data["steps"]["investigate"].get("with", {})
        assert any("issue_url" in str(v) for v in skill_step_with.values())
        assert not any("issue_content" in str(v) for v in skill_step_with.values())

    def test_issue_number_referenced_in_open_pr_step(self):
        data = yaml.safe_load(_recipe_path("remediation").read_text())
        openpr_with = data["steps"]["open_pr_step"].get("with", {})
        assert any("issue_number" in str(v) for v in openpr_with.values())

    def test_no_dead_output_for_issue_number(self):
        """issue_number captured by get_issue_title must not be a dead output."""
        from autoskillit.recipe.validator import analyze_dataflow

        recipe = load_recipe(_recipe_path("remediation"))
        report = analyze_dataflow(recipe)
        dead = [
            w for w in report.warnings if w.code == "DEAD_OUTPUT" and w.field == "issue_number"
        ]
        assert dead == [], f"Unexpected DEAD_OUTPUT for issue_number: {dead}"

    def test_no_issue_content_dead_output(self):
        """issue_content must not appear in captures at all — it is no longer fetched."""
        from autoskillit.recipe.validator import analyze_dataflow

        recipe = load_recipe(_recipe_path("remediation"))
        report = analyze_dataflow(recipe)
        issue_content_dead = [
            w for w in report.warnings if w.code == "DEAD_OUTPUT" and w.field == "issue_content"
        ]
        assert issue_content_dead == []
        data = yaml.safe_load(_recipe_path("remediation").read_text())
        for step_name, step in data["steps"].items():
            captures = step.get("capture", {})
            assert "issue_content" not in captures, (
                f"Step '{step_name}' must not capture issue_content"
            )


class TestImplementationGroupsIssueTitle:
    def test_recipe_validates_clean(self):
        result = validate_from_path(_recipe_path("implementation-groups"))
        errors = [f for f in result.get("findings", []) if f.get("severity") == "error"]
        assert errors == [], f"Unexpected errors: {errors}"

    def test_fetch_issue_step_replaced(self):
        data = yaml.safe_load(_recipe_path("implementation-groups").read_text())
        assert "fetch_issue" not in data["steps"]
        assert "get_issue_title" in data["steps"]

    def test_get_issue_title_captures_three_fields(self):
        data = yaml.safe_load(_recipe_path("implementation-groups").read_text())
        step = data["steps"]["get_issue_title"]
        assert "issue_number" in step["capture"]
        assert "issue_title" in step["capture"]
        assert "issue_slug" in step["capture"]

    def test_get_issue_title_skips_when_no_url(self):
        data = yaml.safe_load(_recipe_path("implementation-groups").read_text())
        step = data["steps"]["get_issue_title"]
        assert step.get("skip_when_false") == "inputs.issue_url"
        assert step.get("optional") is True

    def test_create_branch_uses_slug_fallback(self):
        data = yaml.safe_load(_recipe_path("implementation-groups").read_text())
        cmd = data["steps"]["compute_branch"]["with"]["cmd"]
        assert "SLUG" in cmd
        assert "${SLUG:-" in cmd

    def test_no_issue_content_capture(self):
        """issue_content must not be captured anywhere in the recipe."""
        data = yaml.safe_load(_recipe_path("implementation-groups").read_text())
        all_captures = {
            k: v
            for step in data["steps"].values()
            if "capture" in step
            for k, v in step["capture"].items()
        }
        assert "issue_content" not in all_captures

    def test_open_pr_step_still_references_issue_number(self):
        data = yaml.safe_load(_recipe_path("implementation-groups").read_text())
        step = data["steps"]["open_pr_step"]
        assert "context.issue_number" in str(step)


class TestClaimReleaseGates:
    """Claim/release issue gate steps are present and correctly wired in all 4 recipes."""

    RECIPES = ["implementation", "implementation-groups", "remediation"]
    # Recipes where ci_watch routes directly to release_issue_success
    RECIPES_WITH_RELEASE_SUCCESS = [
        "implementation-groups",
    ]
    # Recipes where ci_watch routes to check_merge_queue (merge-queue path)
    RECIPES_WITHOUT_RELEASE_SUCCESS = [
        "implementation",
        "remediation",
    ]
    # Recipes that have the release_issue_success step (independent of ci_watch routing)
    RECIPES_WITH_RELEASE_SUCCESS_STEP = [
        "implementation-groups",
        "implementation",
        "remediation",
    ]
    RECIPES_WITHOUT_RELEASE_SUCCESS_STEP: list[str] = []

    def test_split_lists_are_exhaustive(self):
        """All RECIPES must appear in exactly one of the split lists."""
        assert set(self.RECIPES_WITH_RELEASE_SUCCESS) | set(
            self.RECIPES_WITHOUT_RELEASE_SUCCESS
        ) == set(self.RECIPES)
        assert set(self.RECIPES_WITH_RELEASE_SUCCESS_STEP) | set(
            self.RECIPES_WITHOUT_RELEASE_SUCCESS_STEP
        ) == set(self.RECIPES)

    def test_claim_issue_step_present(self):
        for name in self.RECIPES:
            data = yaml.safe_load(_recipe_path(name).read_text())
            assert "claim_issue" in data["steps"], f"{name}: missing claim_issue step"

    def test_get_issue_title_routes_to_claim_issue(self):
        for name in self.RECIPES:
            data = yaml.safe_load(_recipe_path(name).read_text())
            assert data["steps"]["get_issue_title"]["on_success"] == "claim_issue", (
                f"{name}: get_issue_title.on_success should be claim_issue"
            )

    def test_claim_issue_routes_to_create_branch_on_true(self):
        for name in self.RECIPES:
            data = yaml.safe_load(_recipe_path(name).read_text())
            step = data["steps"]["claim_issue"]
            routes = step.get("on_result", [])
            true_routes = [r["route"] for r in routes if r.get("when", "").endswith("== true")]
            assert "compute_branch" in true_routes, (
                f"{name}: claim_issue should route to compute_branch when claimed==true"
            )

    def test_release_issue_steps_present(self):
        cache = {name: yaml.safe_load(_recipe_path(name).read_text()) for name in self.RECIPES}
        for name in self.RECIPES:
            assert "release_issue_failure" in cache[name]["steps"], (
                f"{name}: missing release_issue_failure"
            )
        for name in self.RECIPES_WITH_RELEASE_SUCCESS_STEP:
            assert "release_issue_success" in cache[name]["steps"], (
                f"{name}: missing release_issue_success"
            )
        for name in self.RECIPES_WITHOUT_RELEASE_SUCCESS_STEP:
            assert "release_issue_success" not in cache[name]["steps"], (
                f"{name}: release_issue_success must be absent — label stays on success"
            )

    def test_release_issue_success_routes_to_confirm_cleanup(self):
        for name in self.RECIPES_WITH_RELEASE_SUCCESS_STEP:
            data = yaml.safe_load(_recipe_path(name).read_text())
            step = data["steps"]["release_issue_success"]
            assert step["on_success"] == "confirm_cleanup", (
                f"{name}: release_issue_success.on_success should be confirm_cleanup"
            )

    def test_release_issue_failure_routes_to_cleanup_failure(self):
        for name in self.RECIPES:
            data = yaml.safe_load(_recipe_path(name).read_text())
            step = data["steps"]["release_issue_failure"]
            assert step["on_success"] == "cleanup_failure", (
                f"{name}: release_issue_failure.on_success should be cleanup_failure"
            )

    def test_ci_watch_on_success_routing(self):
        expected = {
            **{name: "check_merge_queue" for name in self.RECIPES_WITHOUT_RELEASE_SUCCESS},
            **{name: "release_issue_success" for name in self.RECIPES_WITH_RELEASE_SUCCESS},
        }
        assert set(expected) == set(self.RECIPES), (
            "expected dict does not cover all RECIPES — update split lists"
        )
        for name, expected_route in expected.items():
            data = yaml.safe_load(_recipe_path(name).read_text())
            assert data["steps"]["ci_watch"]["on_success"] == expected_route, (
                f"{name}: ci_watch.on_success should be {expected_route!r}"
            )

    def test_claim_issue_with_args_contains_issue_url(self):
        """CC-F1: claim_issue.with_args must contain issue_url after parsing.

        Fails when the YAML uses `with_args:` key (bug) because _parse_step
        reads data.get("with", {}) and returns {} for that key.
        Passes after renaming to `with:`.
        """
        for name in self.RECIPES:
            recipe = load_recipe(_recipe_path(name))
            step = recipe.steps["claim_issue"]
            assert "issue_url" in step.with_args, (
                f"{name}: claim_issue.with_args missing issue_url — "
                f"YAML likely uses 'with_args:' instead of 'with:'"
            )

    def test_release_issue_success_with_args_contains_issue_url(self):
        """CC-F1: release_issue_success.with_args must contain issue_url after parsing."""
        for name in self.RECIPES_WITH_RELEASE_SUCCESS:
            recipe = load_recipe(_recipe_path(name))
            step = recipe.steps["release_issue_success"]
            assert "issue_url" in step.with_args, (
                f"{name}: release_issue_success.with_args missing issue_url"
            )

    def test_release_issue_failure_with_args_contains_issue_url(self):
        """CC-F1: release_issue_failure.with_args must contain issue_url after parsing."""
        for name in self.RECIPES:
            recipe = load_recipe(_recipe_path(name))
            step = recipe.steps["release_issue_failure"]
            assert "issue_url" in step.with_args, (
                f"{name}: release_issue_failure.with_args missing issue_url"
            )
