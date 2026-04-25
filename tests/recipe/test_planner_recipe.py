"""Structural assertions for the planner recipe."""
import importlib

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


@pytest.fixture(scope="module")
def planner_recipe():
    return load_recipe(builtin_recipes_dir() / "planner.yaml")


def test_planner_recipe_loads(planner_recipe):
    assert planner_recipe is not None
    assert planner_recipe.name == "planner"


def test_planner_recipe_has_18_steps(planner_recipe):
    assert len(planner_recipe.steps) == 18


def test_planner_recipe_declares_requires_packs(planner_recipe):
    assert planner_recipe.requires_packs
    assert "kitchen-core" in planner_recipe.requires_packs


def test_planner_recipe_skill_commands_reference_valid_skills(planner_recipe):
    from autoskillit.workspace import DefaultSkillResolver

    resolver = DefaultSkillResolver()
    valid = {s.name for s in resolver.list_all()}
    for name, step in planner_recipe.steps.items():
        if step.tool == "run_skill" and step.with_args:
            skill_name = step.with_args["skill_command"].split()[0]
            assert skill_name in valid, (
                f"Step {name!r} references unknown skill {skill_name!r}"
            )


def test_planner_recipe_python_callables_importable(planner_recipe):
    for name, step in planner_recipe.steps.items():
        if step.tool == "run_python" and step.with_args:
            callable_path = step.with_args.get("callable", "")
            if not callable_path:
                continue
            module_path, func_name = callable_path.rsplit(".", 1)
            mod = importlib.import_module(module_path)
            assert hasattr(mod, func_name), (
                f"Step {name!r} callable {callable_path!r} not importable"
            )


def test_planner_recipe_loop_steps_have_exit_conditions(planner_recipe):
    for step_name in ["check_phases", "check_assignments", "check_wps"]:
        step = planner_recipe.steps[step_name]
        assert step.on_result is not None, (
            f"{step_name} must have on_result routing"
        )
        assert len(step.on_result.conditions) >= 2, (
            f"{step_name} needs at least two routes (has_remaining true/false)"
        )


def test_planner_recipe_has_kitchen_rules(planner_recipe):
    assert planner_recipe.kitchen_rules
    assert len(planner_recipe.kitchen_rules) >= 3


def test_planner_recipe_autoskillit_version_is_current(planner_recipe):
    from autoskillit.core import AUTOSKILLIT_INSTALLED_VERSION

    assert planner_recipe.version == AUTOSKILLIT_INSTALLED_VERSION


def test_planner_recipe_validate_routes_to_refine_on_fail(planner_recipe):
    refine_reachable = False
    for step_name in ["validate", "check_verdict"]:
        if step_name not in planner_recipe.steps:
            continue
        step = planner_recipe.steps[step_name]
        routes = []
        if step.on_result:
            routes += [c.route for c in step.on_result.conditions]
        if step.on_success:
            routes.append(step.on_success)
        if "refine" in routes:
            refine_reachable = True
    assert refine_reachable, "refine step must be reachable from validate or check_verdict"


def test_planner_recipe_validation_has_no_errors(planner_recipe):
    findings = run_semantic_rules(planner_recipe)
    errors = [f for f in findings if f.severity == "ERROR"]
    assert errors == [], f"Unexpected ERROR findings: {[f.rule for f in errors]}"


def test_planner_recipe_contract_exists():
    contracts_dir = builtin_recipes_dir() / "contracts"
    assert (contracts_dir / "planner.yaml").exists(), (
        "Run: autoskillit recipes validate planner (or Step 2 of the plan)"
    )
