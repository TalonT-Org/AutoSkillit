from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

RECIPE_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "recipes" / "full-audit.yaml"
)


def test_full_audit_recipe_file_exists() -> None:
    """The recipe YAML exists at the bundled location."""
    assert RECIPE_PATH.exists(), f"Expected recipe at {RECIPE_PATH}"


def test_full_audit_recipe_validates() -> None:
    """full-audit.yaml loads without errors and passes validate_recipe."""
    from autoskillit.recipe.io import load_recipe
    from autoskillit.recipe.validator import validate_recipe

    recipe = load_recipe(RECIPE_PATH)
    errors = validate_recipe(recipe)
    assert errors == [], f"Validation errors: {errors}"


def test_full_audit_recipe_name() -> None:
    from autoskillit.recipe.io import load_recipe

    recipe = load_recipe(RECIPE_PATH)
    assert recipe.name == "full-audit"


def test_full_audit_required_ingredients() -> None:
    from autoskillit.recipe.io import load_recipe

    recipe = load_recipe(RECIPE_PATH)
    assert "workspace" in recipe.ingredients
    assert recipe.ingredients["workspace"].required is True
    assert "branch" in recipe.ingredients
    assert recipe.ingredients["branch"].required is True


def test_full_audit_recipe_step_names() -> None:
    from autoskillit.recipe.io import load_recipe

    recipe = load_recipe(RECIPE_PATH)
    expected = {
        "checkout",
        "run_audits",
        "validate_audits",
        "create_issues",
        "done",
        "escalate_stop",
    }
    assert set(recipe.steps.keys()) == expected


def test_full_audit_routing_chain() -> None:
    """checkout → run_audits → validate_audits → create_issues → done."""
    from autoskillit.recipe.io import load_recipe

    recipe = load_recipe(RECIPE_PATH)
    assert recipe.steps["checkout"].on_success == "run_audits"
    assert recipe.steps["run_audits"].on_success == "validate_audits"
    assert recipe.steps["validate_audits"].on_success == "create_issues"
    assert recipe.steps["create_issues"].on_success == "done"


def test_full_audit_failure_routes() -> None:
    from autoskillit.recipe.io import load_recipe

    recipe = load_recipe(RECIPE_PATH)
    for name in ("checkout", "run_audits", "validate_audits", "create_issues"):
        step = recipe.steps[name]
        assert step.on_failure == "escalate_stop", f"{name}.on_failure != escalate_stop"


def test_full_audit_kitchen_rules() -> None:
    from autoskillit.recipe.io import load_recipe

    recipe = load_recipe(RECIPE_PATH)
    assert len(recipe.kitchen_rules) == 4


def test_full_audit_done_step_has_message() -> None:
    """done step must have a non-empty message field."""
    from autoskillit.recipe.io import load_recipe

    recipe = load_recipe(RECIPE_PATH)
    assert recipe.steps["done"].message, "done step must have a non-empty message"


def test_full_audit_done_step_message_embeds_issue_urls_in_sentinel() -> None:
    """done step message must instruct the model to include issue_urls in the sentinel JSON."""

    data = yaml.safe_load(RECIPE_PATH.read_text())
    message = data["steps"]["done"]["message"]
    assert "issue_urls" in message, "done message must reference issue_urls"
    msg_lower = message.lower()
    assert "sentinel" in msg_lower or "json" in msg_lower or '{"success"' in message, (
        "done message must instruct model to embed issue_urls in the sentinel JSON block"
    )


def test_full_audit_discovered_as_builtin_recipe(tmp_path: Path) -> None:
    """list_recipes discovers the recipe as RecipeSource.BUILTIN."""
    from autoskillit.core.types import RecipeSource
    from autoskillit.recipe.io import list_recipes

    result = list_recipes(tmp_path)  # tmp_path has no project-scoped recipes
    match = [r for r in result.items if r.name == "full-audit"]
    assert len(match) == 1
    assert match[0].source == RecipeSource.BUILTIN


def test_full_audit_semantic_rules_no_errors() -> None:
    from autoskillit.core.types import Severity
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.io import load_recipe
    from autoskillit.recipe.registry import run_semantic_rules

    recipe = load_recipe(RECIPE_PATH)

    known_skills = frozenset(
        {
            "audit-tests",
            "audit-cohesion",
            "audit-arch",
            "audit-feature-gates",
            "audit-docs",
            "audit-review-decisions",
            "validate-audit",
            "validate-test-audit",
        }
    )
    ctx = make_validation_context(recipe, available_skills=known_skills)
    findings = run_semantic_rules(ctx)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert not errors, f"Semantic rule errors: {[f.rule + ': ' + f.message for f in errors]}"


def test_full_audit_description_mentions_review_decisions() -> None:
    """full-audit description must mention audit-review-decisions."""

    data = yaml.safe_load(RECIPE_PATH.read_text())
    assert "audit-review-decisions" in data["description"]


def test_full_audit_summary_mentions_six() -> None:
    """full-audit summary must reference 6 parallel chains."""

    data = yaml.safe_load(RECIPE_PATH.read_text())
    assert "6" in data["summary"]


def test_full_audit_run_audits_note_mentions_review_decisions() -> None:
    """run_audits note must include audit-review-decisions as the 6th skill."""

    data = yaml.safe_load(RECIPE_PATH.read_text())
    note = data["steps"]["run_audits"]["note"]
    assert "audit-review-decisions" in note


def test_full_audit_validate_audits_note_mentions_review_decisions() -> None:
    """validate_audits note must include review_decisions validation."""

    data = yaml.safe_load(RECIPE_PATH.read_text())
    note = data["steps"]["validate_audits"]["note"]
    assert "review_decisions" in note


def test_full_audit_validate_audits_routes_tests_to_specialized_skill() -> None:
    """validate_audits step must route test audits to validate-test-audit."""

    data = yaml.safe_load(RECIPE_PATH.read_text())
    note = data["steps"]["validate_audits"]["note"]
    assert "validate-test-audit" in note


def test_full_audit_has_max_parallel_ingredient() -> None:
    from autoskillit.recipe.io import load_recipe

    recipe = load_recipe(RECIPE_PATH)
    assert "max_parallel" in recipe.ingredients


def test_full_audit_max_parallel_defaults_to_three() -> None:
    from autoskillit.recipe.io import load_recipe

    recipe = load_recipe(RECIPE_PATH)
    ing = recipe.ingredients["max_parallel"]
    assert ing.default == "3"


def test_full_audit_max_parallel_is_hidden() -> None:
    from autoskillit.recipe.io import load_recipe

    recipe = load_recipe(RECIPE_PATH)
    ing = recipe.ingredients["max_parallel"]
    assert ing.hidden is True


def test_full_audit_kitchen_rule_mentions_prefer_completion() -> None:
    from autoskillit.recipe.io import load_recipe

    recipe = load_recipe(RECIPE_PATH)
    rules_text = " ".join(recipe.kitchen_rules).lower()
    assert "prefer-completion" in rules_text or "prefer completion" in rules_text


def test_full_audit_run_audits_note_mentions_max_parallel() -> None:

    data = yaml.safe_load(RECIPE_PATH.read_text())
    note = data["steps"]["run_audits"]["note"].lower()
    assert "max_parallel" in note or "max parallel" in note


def test_full_audit_validate_audits_no_wave_barrier() -> None:

    data = yaml.safe_load(RECIPE_PATH.read_text())
    note = data["steps"]["validate_audits"]["note"].lower()
    assert "as each" in note or "as soon as" in note or "slot" in note


def test_full_audit_create_issues_uses_batched_graphql() -> None:

    data = yaml.safe_load(RECIPE_PATH.read_text())
    step = data["steps"]["create_issues"]
    assert step["tool"] == "run_python"
    assert "batch_create_issues" in step["with"]["callable"]
    note = step["note"].lower()
    assert "batched" in note or "graphql" in note


def test_full_audit_create_issues_captures_issue_urls() -> None:
    import yaml

    data = yaml.safe_load(RECIPE_PATH.read_text())
    capture = data["steps"]["create_issues"]["capture"]
    assert "issue_urls" in capture


def test_full_audit_recipe_version_bumped() -> None:
    from packaging.version import Version

    data = yaml.safe_load(RECIPE_PATH.read_text())
    assert Version(data["recipe_version"]) > Version("1.0.0")
