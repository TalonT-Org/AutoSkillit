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
    assert planner_recipe.name == "planner"


def test_planner_recipe_has_required_steps(planner_recipe):
    required_steps = {
        "init",
        "analyze",
        "extract_domain",
        "generate_phases",
        "check_phases",
        "elaborate_phase",
        "build_phase_assignment_manifest",
        "check_phase_assignments",
        "elaborate_phase_assignments",
        "build_phase_wp_manifest",
        "check_phase_wps",
        "elaborate_phase_wps",
        "finalize_wp_manifest",
        "reconcile_deps",
        "validate",
        "check_verdict",
        "refine",
        "compile",
        "done",
        "escalate_stop",
    }
    assert required_steps <= planner_recipe.steps.keys(), (
        f"Missing steps: {required_steps - planner_recipe.steps.keys()}"
    )


def test_planner_recipe_declares_requires_packs(planner_recipe):
    assert planner_recipe.requires_packs
    assert "kitchen-core" in planner_recipe.requires_packs


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
    for step_name in ["check_phases", "check_phase_assignments", "check_phase_wps"]:
        step = planner_recipe.steps[step_name]
        assert step.on_result is not None, f"{step_name} must have on_result routing"
        assert len(step.on_result.conditions) >= 2, (
            f"{step_name} needs at least two routes (has_remaining true/false)"
        )


def test_planner_recipe_has_kitchen_rules(planner_recipe):
    assert planner_recipe.kitchen_rules
    assert len(planner_recipe.kitchen_rules) >= 3


def test_planner_recipe_validate_routes_to_refine_on_fail(planner_recipe):
    assert "validate" in planner_recipe.steps, "validate step must exist"
    assert "check_verdict" in planner_recipe.steps, "check_verdict step must exist"
    refine_reachable = False
    for step_name in ["validate", "check_verdict"]:
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
    assert (contracts_dir / "planner.yaml").exists(), "Run: autoskillit recipes validate planner"


def test_planner_recipe_extract_domain_uses_env_var(planner_recipe):
    """extract_domain step must use PLANNER_ANALYSIS_FILE env-var, not positional $3."""
    step = planner_recipe.steps["extract_domain"]
    skill_cmd = step.with_args.get("skill_command", "")
    assert "$3" not in skill_cmd, "Must not use positional $3"
    assert "_ _" not in skill_cmd, "Must not have reserved-slot placeholders"
    env = step.with_args.get("env", {})
    assert "PLANNER_ANALYSIS_FILE" in env, "env must declare PLANNER_ANALYSIS_FILE"


def test_planner_init_captures_planner_dir(planner_recipe):
    init_step = planner_recipe.steps["init"]
    assert init_step.tool == "run_python"
    assert init_step.with_args.get("callable") == "autoskillit.planner.create_run_dir"
    assert "planner_dir" in (init_step.capture or {})


def test_planner_steps_use_context_planner_dir():
    import yaml

    from autoskillit.recipe.io import builtin_recipes_dir

    raw = yaml.safe_load((builtin_recipes_dir() / "planner.yaml").read_text())
    raw_steps = raw.get("steps", {})
    for step_name, step_dict in raw_steps.items():
        step_str = str(step_dict)
        assert "{{AUTOSKILLIT_TEMP}}/planner" not in step_str, (
            f"Step '{step_name}' still references bare AUTOSKILLIT_TEMP/planner path"
        )


def test_elaborate_phase_assignments_uses_planner_elaborate_assignments_skill(planner_recipe):
    step = planner_recipe.steps["elaborate_phase_assignments"]
    skill_cmd = step.with_args.get("skill_command", "")
    assert "planner-elaborate-assignments" in skill_cmd, (
        "elaborate_phase_assignments must invoke planner-elaborate-assignments skill"
    )


def test_planner_recipe_has_phase_wp_steps(planner_recipe):
    expected = {
        "build_phase_wp_manifest",
        "check_phase_wps",
        "elaborate_phase_wps",
        "finalize_wp_manifest",
    }
    assert expected <= planner_recipe.steps.keys(), (
        f"Missing phase-WP steps: {expected - planner_recipe.steps.keys()}"
    )


def test_planner_recipe_elaborate_phase_wps_uses_correct_skill(planner_recipe):
    step = planner_recipe.steps["elaborate_phase_wps"]
    skill_cmd = step.with_args.get("skill_command", "")
    assert "planner-elaborate-wps" in skill_cmd, (
        "elaborate_phase_wps must invoke planner-elaborate-wps skill"
    )


def test_planner_recipe_finalize_step_routes_to_reconcile(planner_recipe):
    step = planner_recipe.steps["finalize_wp_manifest"]
    assert step.on_success == "reconcile_deps", (
        f"finalize_wp_manifest must route to reconcile_deps on success, got {step.on_success!r}"
    )
