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
        "resolve_task",
        "analyze",
        "extract_domain",
        "generate_phases",
        "build_plan_snapshot",
        "elaborate_phases",
        "merge_phases",
        "refine_phases",
        "expand_assignments",
        "elaborate_assignments",
        "merge_assignments",
        "refine_assignments",
        "expand_wps",
        "elaborate_wps",
        "finalize_wp_manifest",
        "merge_wps",
        "refine_wps",
        "validate_task_alignment",
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


def test_planner_recipe_has_kitchen_rules(planner_recipe):
    assert planner_recipe.kitchen_rules
    assert len(planner_recipe.kitchen_rules) >= 3


def test_kitchen_rules_include_sequential_dispatch(planner_recipe):
    assert any("SEQUENTIAL DISPATCH" in rule for rule in planner_recipe.kitchen_rules), (
        "kitchen_rules must include a SEQUENTIAL DISPATCH rule"
    )


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


def test_planner_recipe_extract_domain_uses_positional_args(planner_recipe):
    step = planner_recipe.steps["extract_domain"]
    skill_cmd = step.with_args.get("skill_command", "")
    assert "analysis.json" in skill_cmd, "Must pass analysis.json as positional arg"
    assert "context.task_file_path" in skill_cmd, "Must pass task_file_path as positional arg"
    assert "env" not in step.with_args, "Must not use env: block (ADR-0003)"


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


def test_elaborate_assignments_uses_planner_elaborate_assignments_skill(planner_recipe):
    step = planner_recipe.steps["elaborate_assignments"]
    skill_cmd = step.with_args.get("skill_command", "")
    assert "planner-elaborate-assignments" in skill_cmd, (
        "elaborate_assignments must invoke planner-elaborate-assignments skill"
    )


def test_planner_recipe_has_phase_wp_steps(planner_recipe):
    expected = {
        "expand_wps",
        "elaborate_wps",
        "merge_wps",
        "refine_wps",
        "finalize_wp_manifest",
    }
    assert expected <= planner_recipe.steps.keys(), (
        f"Missing phase-WP steps: {expected - planner_recipe.steps.keys()}"
    )


def test_planner_recipe_elaborate_wps_uses_correct_skill(planner_recipe):
    step = planner_recipe.steps["elaborate_wps"]
    skill_cmd = step.with_args.get("skill_command", "")
    assert "planner-elaborate-wps" in skill_cmd, (
        "elaborate_wps must invoke planner-elaborate-wps skill"
    )


def test_planner_recipe_finalize_step_routes_to_merge_wps(planner_recipe):
    step = planner_recipe.steps["finalize_wp_manifest"]
    assert step.on_success == "merge_wps", (
        f"finalize_wp_manifest must route to merge_wps on success, got {step.on_success!r}"
    )


# --- T2: Parallel dispatch pattern tests ---


def test_elaborate_phases_uses_capture_list(planner_recipe):
    step = planner_recipe.steps["elaborate_phases"]
    assert step.capture_list, "elaborate_phases must use capture_list for parallel accumulation"
    assert "elab_result_path" in step.capture_list


def test_elaborate_assignments_uses_capture_list(planner_recipe):
    step = planner_recipe.steps["elaborate_assignments"]
    assert step.capture_list
    assert "phase_assignments_result_dir" in step.capture_list


def test_elaborate_wps_uses_capture_list(planner_recipe):
    step = planner_recipe.steps["elaborate_wps"]
    assert step.capture_list
    assert "phase_wps_result_dir" in step.capture_list


def test_parallel_elaborate_steps_have_dispatch_note(planner_recipe):
    for step_name in ("elaborate_phases", "elaborate_assignments"):
        step = planner_recipe.steps[step_name]
        assert step.note, f"{step_name} must have a note for parallel dispatch instructions"
        assert "parallel" in step.note.lower(), f"{step_name} note must mention parallel dispatch"


def test_elaborate_wps_has_sequential_dispatch_note(planner_recipe):
    step = planner_recipe.steps["elaborate_wps"]
    assert step.note, "elaborate_wps must have a note for sequential dispatch instructions"
    assert "sequential" in step.note.lower(), "elaborate_wps note must mention sequential dispatch"
    assert "parallel" not in step.note.lower(), (
        "elaborate_wps note must not mention parallel dispatch"
    )


# --- T3: No sequential loops ---


def test_no_check_remaining_loops(planner_recipe):
    for name, step in planner_recipe.steps.items():
        if step.tool == "run_python" and step.with_args.get("callable", ""):
            assert "check_remaining" not in step.with_args["callable"], (
                f"Step {name} still uses check_remaining — recipe should use parallel dispatch"
            )


# --- T4: Refine tier steps wired ---


@pytest.mark.parametrize(
    "step_name,skill_name",
    [
        ("refine_phases", "planner-refine-phases"),
        ("refine_assignments", "planner-refine-assignments"),
        ("refine_wps", "planner-refine-wps"),
    ],
)
def test_refine_tier_steps_use_correct_skills(planner_recipe, step_name, skill_name):
    step = planner_recipe.steps[step_name]
    assert step.tool == "run_skill"
    assert skill_name in step.with_args.get("skill_command", "")


# --- T5: Merge steps use merge_tier_results ---


@pytest.mark.parametrize("step_name", ["merge_phases", "merge_assignments", "merge_wps"])
def test_merge_steps_use_merge_tier_results(planner_recipe, step_name):
    step = planner_recipe.steps[step_name]
    assert step.tool == "run_python"
    assert step.with_args.get("callable") == "autoskillit.planner.merge.merge_tier_results"


# --- validate_task_alignment step integration ---


def test_validate_task_alignment_step_exists(planner_recipe):
    assert "validate_task_alignment" in planner_recipe.steps
    step = planner_recipe.steps["validate_task_alignment"]
    assert step.tool == "run_skill"
    assert "planner-validate-task-alignment" in step.with_args.get("skill_command", "")


def test_planner_recipe_has_assess_review_approach_step(planner_recipe):
    assert "assess_review_approach_step" in planner_recipe.steps
    step = planner_recipe.steps["assess_review_approach_step"]
    assert step.tool == "run_skill"
    assert "planner-assess-review-approach" in (step.with_args.get("skill_command") or "")
    assert step.optional is True


def test_planner_recipe_assess_review_approach_routes_to_validate_on_failure(planner_recipe):
    step = planner_recipe.steps["assess_review_approach_step"]
    assert step.on_success == "validate"
    assert step.on_failure == "validate"


def test_planner_recipe_no_task_file_ingredient(planner_recipe):
    assert "task_file" not in planner_recipe.ingredients


def test_planner_recipe_has_resolve_task_step(planner_recipe):
    assert "resolve_task" in planner_recipe.steps
    step = planner_recipe.steps["resolve_task"]
    assert step.tool == "run_python"
    assert step.with_args.get("callable") == "autoskillit.planner.resolve_task_input"
    assert "task_file_path" in (step.capture or {})
    assert "task_label" in (step.capture or {})


def test_planner_init_routes_to_resolve_task(planner_recipe):
    init_step = planner_recipe.steps["init"]
    assert init_step.on_success == "resolve_task"


def test_planner_resolve_task_routes_to_analyze(planner_recipe):
    step = planner_recipe.steps["resolve_task"]
    assert step.on_success == "analyze"


def test_extract_domain_passes_task_file_path_positionally(planner_recipe):
    step = planner_recipe.steps["extract_domain"]
    skill_cmd = step.with_args.get("skill_command", "")
    assert "context.task_file_path" in skill_cmd
    assert "env" not in step.with_args, "Must not use env: block (ADR-0003)"


def test_generate_phases_passes_task_file_path_positionally(planner_recipe):
    step = planner_recipe.steps["generate_phases"]
    skill_cmd = step.with_args.get("skill_command", "")
    assert "context.task_file_path" in skill_cmd
    assert "env" not in step.with_args, "Must not use env: block (ADR-0003)"


@pytest.mark.parametrize(
    "step_name", ["build_plan_snapshot", "merge_phases", "merge_assignments", "merge_wps"]
)
def test_merge_steps_receive_task_file_path(planner_recipe, step_name):
    step = planner_recipe.steps[step_name]
    assert "task_file_path" in step.with_args, f"{step_name} must receive task_file_path"
    assert "context.task_file_path" in step.with_args["task_file_path"]


def test_compile_step_receives_task_file_path_and_label(planner_recipe):
    step = planner_recipe.steps["compile"]
    assert "task_file_path" in step.with_args
    assert "task_label" in step.with_args
    assert "context.task_file_path" in step.with_args["task_file_path"]
    assert "context.task_label" in step.with_args["task_label"]


def test_no_step_references_inputs_task_file(planner_recipe):
    for name, step in planner_recipe.steps.items():
        step_str = str(step.with_args)
        assert "inputs.task_file" not in step_str, (
            f"Step {name!r} still references inputs.task_file"
        )
