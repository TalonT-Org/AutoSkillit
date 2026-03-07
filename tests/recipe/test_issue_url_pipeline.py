"""Tests for issue_url ingredient threading across the three PR-opening recipes."""

import subprocess
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
        assert result["valid"] is True
        errors = [f for f in result.get("findings", []) if f.get("severity") == "error"]
        assert errors == [], f"Unexpected errors: {errors}"

    def test_issue_url_ingredient_declared(self):
        """issue_url ingredient must be declared as optional with default empty string."""
        data = yaml.safe_load(_recipe_path("implementation").read_text())
        assert "issue_url" in data["ingredients"]
        ing = data["ingredients"]["issue_url"]
        assert ing.get("required", False) is False
        assert ing.get("default", None) == ""

    def test_no_fetch_issue_step(self):
        """fetch_issue step must NOT exist — orchestrator no longer fetches issue content."""
        data = yaml.safe_load(_recipe_path("implementation").read_text())
        assert "fetch_issue" not in data["steps"]

    def test_parse_issue_number_step_present(self):
        """parse_issue_number step must exist with correct structure."""
        data = yaml.safe_load(_recipe_path("implementation").read_text())
        assert "parse_issue_number" in data["steps"]
        step = data["steps"]["parse_issue_number"]
        assert step["tool"] == "run_cmd"
        assert step.get("optional") is True
        assert step.get("skip_when_false") == "inputs.issue_url"
        assert "issue_number" in step.get("capture", {})
        assert "issue_content" not in step.get("capture", {})

    def test_parse_issue_number_between_set_merge_target_and_create_branch(self):
        """parse_issue_number must be positioned after set_merge_target, before create_branch."""
        data = yaml.safe_load(_recipe_path("implementation").read_text())
        assert data["steps"]["set_merge_target"]["on_success"] == "parse_issue_number"
        assert data["steps"]["parse_issue_number"]["on_success"] == "claim_issue"

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
        """issue_number captured by parse_issue_number must not be a dead output."""
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

    def test_parse_issue_number_cmd_extracts_number_from_url(self):
        """The parse_issue_number shell command must correctly extract trailing digits."""
        test_cases = [
            ("https://github.com/owner/repo/issues/42", "42"),
            ("https://github.com/owner/repo/issues/123", "123"),
            ("owner/repo#99", "99"),
            ("#7", "7"),
        ]
        for url, expected in test_cases:
            result = subprocess.run(
                ["bash", "-c", f"printf '%s' '{url}' | grep -oE '[0-9]+$' || true"],
                capture_output=True,
                text=True,
            )
            assert result.stdout.strip() == expected, (
                f"For '{url}': expected '{expected}', got '{result.stdout.strip()}'"
            )


class TestInvestigateFirstIssueUrl:
    def test_recipe_validates_clean(self):
        result = validate_from_path(_recipe_path("remediation"))
        assert result["valid"] is True
        errors = [f for f in result.get("findings", []) if f.get("severity") == "error"]
        assert errors == [], f"Unexpected errors: {errors}"

    def test_issue_url_ingredient_declared(self):
        data = yaml.safe_load(_recipe_path("remediation").read_text())
        assert "issue_url" in data["ingredients"]
        ing = data["ingredients"]["issue_url"]
        assert ing.get("required", False) is False
        assert ing.get("default", None) == ""

    def test_no_fetch_issue_step(self):
        """fetch_issue step must NOT exist — orchestrator no longer fetches issue content."""
        data = yaml.safe_load(_recipe_path("remediation").read_text())
        assert "fetch_issue" not in data["steps"]

    def test_parse_issue_number_step_present(self):
        """parse_issue_number step must exist with correct structure."""
        data = yaml.safe_load(_recipe_path("remediation").read_text())
        assert "parse_issue_number" in data["steps"]
        step = data["steps"]["parse_issue_number"]
        assert step["tool"] == "run_cmd"
        assert step.get("optional") is True
        assert step.get("skip_when_false") == "inputs.issue_url"
        assert "issue_number" in step.get("capture", {})
        assert "issue_content" not in step.get("capture", {})

    def test_parse_issue_number_between_set_merge_target_and_create_branch(self):
        """parse_issue_number must be positioned after set_merge_target, before create_branch."""
        data = yaml.safe_load(_recipe_path("remediation").read_text())
        assert data["steps"]["set_merge_target"]["on_success"] == "parse_issue_number"
        assert data["steps"]["parse_issue_number"]["on_success"] == "claim_issue"

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
        """issue_number captured by parse_issue_number must not be a dead output."""
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


class TestAuditAndFixIssueUrl:
    def test_recipe_validates_clean(self):
        result = validate_from_path(_recipe_path("audit-and-fix"))
        assert result["valid"] is True
        errors = [f for f in result.get("findings", []) if f.get("severity") == "error"]
        assert errors == [], f"Unexpected errors: {errors}"

    def test_issue_url_ingredient_declared(self):
        data = yaml.safe_load(_recipe_path("audit-and-fix").read_text())
        assert "issue_url" in data["ingredients"]
        ing = data["ingredients"]["issue_url"]
        assert ing.get("required", False) is False
        assert ing.get("default", None) == ""

    def test_no_fetch_issue_step(self):
        """fetch_issue step must NOT exist — orchestrator no longer fetches issue content."""
        data = yaml.safe_load(_recipe_path("audit-and-fix").read_text())
        assert "fetch_issue" not in data["steps"]

    def test_parse_issue_number_step_present(self):
        """parse_issue_number step must exist with correct structure."""
        data = yaml.safe_load(_recipe_path("audit-and-fix").read_text())
        assert "parse_issue_number" in data["steps"]
        step = data["steps"]["parse_issue_number"]
        assert step["tool"] == "run_cmd"
        assert step.get("optional") is True
        assert step.get("skip_when_false") == "inputs.issue_url"
        assert "issue_number" in step.get("capture", {})
        assert "issue_content" not in step.get("capture", {})

    def test_parse_issue_number_between_set_merge_target_and_create_branch(self):
        """parse_issue_number must be positioned after set_merge_target, before create_branch."""
        data = yaml.safe_load(_recipe_path("audit-and-fix").read_text())
        assert data["steps"]["set_merge_target"]["on_success"] == "parse_issue_number"
        assert data["steps"]["parse_issue_number"]["on_success"] == "claim_issue"

    def test_issue_url_referenced_in_downstream_skill_step(self):
        """investigate step must reference inputs.issue_url, not issue_content."""
        data = yaml.safe_load(_recipe_path("audit-and-fix").read_text())
        skill_step_with = data["steps"]["investigate"].get("with", {})
        assert any("issue_url" in str(v) for v in skill_step_with.values())
        assert not any("issue_content" in str(v) for v in skill_step_with.values())

    def test_issue_number_referenced_in_open_pr_step(self):
        data = yaml.safe_load(_recipe_path("audit-and-fix").read_text())
        openpr_with = data["steps"]["open_pr_step"].get("with", {})
        assert any("issue_number" in str(v) for v in openpr_with.values())

    def test_no_dead_output_for_issue_number(self):
        """issue_number captured by parse_issue_number must not be a dead output."""
        from autoskillit.recipe.validator import analyze_dataflow

        recipe = load_recipe(_recipe_path("audit-and-fix"))
        report = analyze_dataflow(recipe)
        dead = [
            w for w in report.warnings if w.code == "DEAD_OUTPUT" and w.field == "issue_number"
        ]
        assert dead == [], f"Unexpected DEAD_OUTPUT for issue_number: {dead}"

    def test_no_issue_content_dead_output(self):
        """issue_content must not appear in captures at all — it is no longer fetched."""
        from autoskillit.recipe.validator import analyze_dataflow

        recipe = load_recipe(_recipe_path("audit-and-fix"))
        report = analyze_dataflow(recipe)
        issue_content_dead = [
            w for w in report.warnings if w.code == "DEAD_OUTPUT" and w.field == "issue_content"
        ]
        assert issue_content_dead == []
        data = yaml.safe_load(_recipe_path("audit-and-fix").read_text())
        for step_name, step in data["steps"].items():
            captures = step.get("capture", {})
            assert "issue_content" not in captures, (
                f"Step '{step_name}' must not capture issue_content"
            )
