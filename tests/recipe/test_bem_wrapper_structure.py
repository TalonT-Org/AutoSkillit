import pytest

from autoskillit.recipe.contracts import load_bundled_manifest
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import validate_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

RECIPE_PATH = builtin_recipes_dir() / "bem-wrapper.yaml"


def _load():
    return load_recipe(RECIPE_PATH)


def test_bem_wrapper_recipe_file_exists():
    assert RECIPE_PATH.exists(), "bem-wrapper.yaml not found in bundled recipes"


def test_bem_wrapper_recipe_loads():
    recipe = _load()
    assert recipe.name == "bem-wrapper"
    assert recipe.kind == "standard"


def test_bem_wrapper_passes_validation():
    recipe = _load()
    errors = validate_recipe(recipe)
    assert errors == [], f"Unexpected validation errors: {errors}"


def test_bem_wrapper_ingredients_declared():
    recipe = _load()
    assert "issue_urls" in recipe.ingredients
    assert recipe.ingredients["issue_urls"].required is True
    assert "base_branch" in recipe.ingredients
    assert recipe.ingredients["base_branch"].default == "develop"


def test_bem_wrapper_routing_chain():
    """Verify step-level routing covers all expected transitions."""
    recipe = _load()
    steps = recipe.steps

    assert steps["refetch_issues"].on_success == "check_issue_count"
    assert steps["refetch_issues"].on_failure == "emit_fallback_map"

    # check_issue_count is a route action with two branches
    cic = steps["check_issue_count"]
    assert cic.action == "route"
    route_targets = {c.route for c in cic.on_result.conditions}
    assert "emit_empty_result" in route_targets
    assert "run_bem" in route_targets

    assert steps["run_bem"].on_success == "emit_result"
    assert steps["run_bem"].on_failure == "emit_fallback_map"

    assert steps["emit_fallback_map"].on_success == "emit_result"
    assert steps["emit_fallback_map"].on_failure == "escalate_stop"

    assert steps["emit_result"].on_success == "done"
    assert steps["emit_result"].on_failure == "done"

    assert steps["emit_empty_result"].on_success == "done"
    assert steps["emit_empty_result"].on_failure == "done"


def test_bem_wrapper_fallback_references_autoskillit_temp():
    """Fallback map must reference {{AUTOSKILLIT_TEMP}} placeholder, not bare $AUTOSKILLIT_TEMP."""
    raw = RECIPE_PATH.read_text()
    assert "{{AUTOSKILLIT_TEMP}}" in raw
    assert "$AUTOSKILLIT_TEMP" not in raw


def test_bem_wrapper_fallback_writes_file_not_json_to_stdout():
    """emit_fallback_map must write JSON to a file and return the path.

    The campaign captures the path via result.execution_map. If raw JSON were
    returned, implement-findings would get ENOENT when opening it as a file path.
    """
    recipe = _load()
    step = recipe.steps["emit_fallback_map"]
    assert step.tool == "run_python"
    assert step.with_args["callable"] == "autoskillit.recipe._cmd_rpc.emit_fallback_map"
    assert step.capture.get("execution_map") == "${{ result.execution_map }}"


def test_bem_wrapper_emit_result_consumes_execution_map_context():
    """emit_result must reference context.execution_map in its cmd to satisfy dead_output."""
    recipe = _load()
    cmd = recipe.steps["emit_result"].with_args["cmd"]
    assert "context.execution_map" in cmd


def test_bem_wrapper_done_message_mentions_dispatch_plan():
    recipe = _load()
    msg = recipe.steps["done"].message
    assert "dispatch_plan" in msg


def test_bem_wrapper_done_message_mentions_execution_map():
    recipe = _load()
    msg = recipe.steps["done"].message
    assert "execution_map" in msg


def test_bem_wrapper_run_skill_capture_keys_in_contract():
    """Captured keys from run_bem must exist in build-execution-map skill contract."""
    recipe = _load()
    manifest = load_bundled_manifest()
    bem_outputs = {o["name"] for o in manifest["skills"]["build-execution-map"]["outputs"]}
    step = recipe.steps["run_bem"]
    for key in step.capture:
        capture_val = step.capture[key]
        # capture value format: "${{ result.<field_name> }}"
        field = capture_val.replace("${{ result.", "").replace(" }}", "").strip()
        assert field in bem_outputs, f"Captured field '{field}' not in build-execution-map outputs"


def test_bem_wrapper_emit_empty_result_cmd_echoes_none():
    recipe = _load()
    cmd = recipe.steps["emit_empty_result"].with_args["cmd"]
    assert "none" in cmd.lower()


def test_bem_wrapper_escalate_stop_is_stop_action():
    recipe = _load()
    assert recipe.steps["escalate_stop"].action == "stop"
    assert recipe.steps["done"].action == "stop"


def test_bem_wrapper_no_dead_outputs():
    """No captured context variables should be dead (captured but never consumed)."""
    from autoskillit.recipe._analysis import analyze_dataflow

    recipe = _load()
    report = analyze_dataflow(recipe)
    dead = {w.field for w in report.warnings if w.code == "DEAD_OUTPUT"}
    assert dead == set(), f"Dead output variables detected: {dead}"
