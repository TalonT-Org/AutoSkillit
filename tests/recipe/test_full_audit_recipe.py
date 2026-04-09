from pathlib import Path

RECIPE_PATH = Path(__file__).resolve().parents[2] / ".autoskillit" / "recipes" / "full-audit.yaml"


def test_full_audit_recipe_file_exists() -> None:
    """The recipe YAML exists at the project-scoped location."""
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
    assert len(recipe.kitchen_rules) == 3


def test_full_audit_discovered_as_project_recipe(tmp_path: Path) -> None:
    """list_recipes discovers the recipe as RecipeSource.PROJECT."""
    import shutil

    from autoskillit.core.types import RecipeSource
    from autoskillit.recipe.io import list_recipes

    project_dir = tmp_path / "project"
    recipe_dir = project_dir / ".autoskillit" / "recipes"
    recipe_dir.mkdir(parents=True)
    shutil.copy2(RECIPE_PATH, recipe_dir / "full-audit.yaml")

    result = list_recipes(project_dir)
    match = [r for r in result.items if r.name == "full-audit"]
    assert len(match) == 1
    assert match[0].source == RecipeSource.PROJECT


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
            "validate-audit",
            "prepare-issue",
        }
    )
    ctx = make_validation_context(recipe, available_skills=known_skills)
    findings = run_semantic_rules(ctx)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert not errors, f"Semantic rule errors: {[f.rule + ': ' + f.message for f in errors]}"
