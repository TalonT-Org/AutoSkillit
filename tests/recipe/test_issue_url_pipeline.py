"""Tests for issue_url ingredient threading across the three PR-opening recipes."""
from pathlib import Path
import pytest
from autoskillit.recipe._api import validate_from_path
from autoskillit.recipe.io import load_recipe

RECIPES_DIR = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "recipes"

def _recipe_path(name: str) -> Path:
    return RECIPES_DIR / f"{name}.yaml"


class TestImplementationPipelineIssueUrl:
    def test_recipe_validates_clean(self):
        """implementation-pipeline must validate with no errors after adding issue_url."""
        result = validate_from_path(_recipe_path("implementation-pipeline"))
        assert result["valid"] is True
        errors = [f for f in result.get("findings", []) if f.get("severity") == "error"]
        assert errors == [], f"Unexpected errors: {errors}"

    def test_issue_url_ingredient_declared(self):
        """issue_url ingredient must be declared as optional with default empty string."""
        import yaml
        data = yaml.safe_load(_recipe_path("implementation-pipeline").read_text())
        assert "issue_url" in data["ingredients"]
        ing = data["ingredients"]["issue_url"]
        assert ing.get("required", False) is False
        assert ing.get("default", None) == ""

    def test_fetch_issue_step_present(self):
        """fetch_issue step must exist with correct structure."""
        import yaml
        data = yaml.safe_load(_recipe_path("implementation-pipeline").read_text())
        assert "fetch_issue" in data["steps"]
        step = data["steps"]["fetch_issue"]
        assert step["tool"] == "fetch_github_issue"
        assert step.get("optional") is True
        assert step.get("skip_when_false") == "inputs.issue_url"
        assert "issue_number" in step.get("capture", {})
        assert "issue_content" in step.get("capture", {})

    def test_fetch_issue_between_set_merge_target_and_create_branch(self):
        """fetch_issue must be positioned after set_merge_target and before create_branch."""
        import yaml
        data = yaml.safe_load(_recipe_path("implementation-pipeline").read_text())
        assert data["steps"]["set_merge_target"]["on_success"] == "fetch_issue"
        assert data["steps"]["fetch_issue"]["on_success"] == "create_branch"

    def test_issue_content_referenced_in_plan_step(self):
        """plan step must reference context.issue_content in with: for dataflow tracking."""
        import yaml
        data = yaml.safe_load(_recipe_path("implementation-pipeline").read_text())
        plan_with = data["steps"]["plan"].get("with", {})
        assert any("issue_content" in str(v) for v in plan_with.values())

    def test_issue_number_referenced_in_open_pr_step(self):
        """open_pr_step must reference context.issue_number in with: for dataflow tracking."""
        import yaml
        data = yaml.safe_load(_recipe_path("implementation-pipeline").read_text())
        openpr_with = data["steps"]["open_pr_step"].get("with", {})
        assert any("issue_number" in str(v) for v in openpr_with.values())

    def test_no_dead_output_for_issue_captures(self):
        """issue_number and issue_content captured by fetch_issue must not be dead outputs."""
        from autoskillit.recipe.validator import analyze_dataflow
        from autoskillit.recipe.io import load_recipe
        recipe = load_recipe(_recipe_path("implementation-pipeline"))
        report = analyze_dataflow(recipe)
        dead = [w for w in report.warnings if w.code == "DEAD_OUTPUT"
                and w.field in ("issue_number", "issue_content")]
        assert dead == [], f"Unexpected DEAD_OUTPUT warnings: {dead}"


class TestInvestigateFirstIssueUrl:
    def test_recipe_validates_clean(self):
        result = validate_from_path(_recipe_path("investigate-first"))
        assert result["valid"] is True
        errors = [f for f in result.get("findings", []) if f.get("severity") == "error"]
        assert errors == [], f"Unexpected errors: {errors}"

    def test_issue_url_ingredient_declared(self):
        import yaml
        data = yaml.safe_load(_recipe_path("investigate-first").read_text())
        assert "issue_url" in data["ingredients"]
        ing = data["ingredients"]["issue_url"]
        assert ing.get("required", False) is False
        assert ing.get("default", None) == ""

    def test_fetch_issue_step_present(self):
        import yaml
        data = yaml.safe_load(_recipe_path("investigate-first").read_text())
        assert "fetch_issue" in data["steps"]
        step = data["steps"]["fetch_issue"]
        assert step["tool"] == "fetch_github_issue"
        assert step.get("optional") is True
        assert step.get("skip_when_false") == "inputs.issue_url"
        assert "issue_number" in step.get("capture", {})
        assert "issue_content" in step.get("capture", {})

    def test_fetch_issue_between_set_merge_target_and_create_branch(self):
        import yaml
        data = yaml.safe_load(_recipe_path("investigate-first").read_text())
        assert data["steps"]["set_merge_target"]["on_success"] == "fetch_issue"
        assert data["steps"]["fetch_issue"]["on_success"] == "create_branch"

    def test_issue_content_referenced_in_investigate_step(self):
        import yaml
        data = yaml.safe_load(_recipe_path("investigate-first").read_text())
        inv_with = data["steps"]["investigate"].get("with", {})
        assert any("issue_content" in str(v) for v in inv_with.values())

    def test_issue_number_referenced_in_open_pr_step(self):
        import yaml
        data = yaml.safe_load(_recipe_path("investigate-first").read_text())
        openpr_with = data["steps"]["open_pr_step"].get("with", {})
        assert any("issue_number" in str(v) for v in openpr_with.values())

    def test_no_dead_output_for_issue_captures(self):
        from autoskillit.recipe.validator import analyze_dataflow
        from autoskillit.recipe.io import load_recipe
        recipe = load_recipe(_recipe_path("investigate-first"))
        report = analyze_dataflow(recipe)
        dead = [w for w in report.warnings if w.code == "DEAD_OUTPUT"
                and w.field in ("issue_number", "issue_content")]
        assert dead == [], f"Unexpected DEAD_OUTPUT warnings: {dead}"


class TestAuditAndFixIssueUrl:
    def test_recipe_validates_clean(self):
        result = validate_from_path(_recipe_path("audit-and-fix"))
        assert result["valid"] is True
        errors = [f for f in result.get("findings", []) if f.get("severity") == "error"]
        assert errors == [], f"Unexpected errors: {errors}"

    def test_issue_url_ingredient_declared(self):
        import yaml
        data = yaml.safe_load(_recipe_path("audit-and-fix").read_text())
        assert "issue_url" in data["ingredients"]
        ing = data["ingredients"]["issue_url"]
        assert ing.get("required", False) is False
        assert ing.get("default", None) == ""

    def test_fetch_issue_step_present(self):
        import yaml
        data = yaml.safe_load(_recipe_path("audit-and-fix").read_text())
        assert "fetch_issue" in data["steps"]
        step = data["steps"]["fetch_issue"]
        assert step["tool"] == "fetch_github_issue"
        assert step.get("optional") is True
        assert step.get("skip_when_false") == "inputs.issue_url"
        assert "issue_number" in step.get("capture", {})
        assert "issue_content" in step.get("capture", {})

    def test_fetch_issue_between_set_merge_target_and_create_branch(self):
        import yaml
        data = yaml.safe_load(_recipe_path("audit-and-fix").read_text())
        assert data["steps"]["set_merge_target"]["on_success"] == "fetch_issue"
        assert data["steps"]["fetch_issue"]["on_success"] == "create_branch"

    def test_issue_content_referenced_in_investigate_step(self):
        import yaml
        data = yaml.safe_load(_recipe_path("audit-and-fix").read_text())
        inv_with = data["steps"]["investigate"].get("with", {})
        assert any("issue_content" in str(v) for v in inv_with.values())

    def test_issue_number_referenced_in_open_pr_step(self):
        import yaml
        data = yaml.safe_load(_recipe_path("audit-and-fix").read_text())
        openpr_with = data["steps"]["open_pr_step"].get("with", {})
        assert any("issue_number" in str(v) for v in openpr_with.values())

    def test_no_dead_output_for_issue_captures(self):
        from autoskillit.recipe.validator import analyze_dataflow
        from autoskillit.recipe.io import load_recipe
        recipe = load_recipe(_recipe_path("audit-and-fix"))
        report = analyze_dataflow(recipe)
        dead = [w for w in report.warnings if w.code == "DEAD_OUTPUT"
                and w.field in ("issue_number", "issue_content")]
        assert dead == [], f"Unexpected DEAD_OUTPUT warnings: {dead}"
