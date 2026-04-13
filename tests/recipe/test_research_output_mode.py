# tests/recipe/test_research_output_mode.py
import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

RESEARCH_RECIPE_PATH = builtin_recipes_dir() / "research.yaml"


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(RESEARCH_RECIPE_PATH)


# --- REQ-R741-H01: ingredient exists with default "local" ---


def test_default_mode_is_local(recipe):
    """ingredients.output_mode must exist with default == 'local' (issue body)."""
    assert "output_mode" in recipe.ingredients, "output_mode ingredient missing"
    ing = recipe.ingredients["output_mode"]
    assert ing.default == "local", (
        f"output_mode default must be 'local' (issue body override), got {ing.default!r}"
    )


# --- REQ-R741-H04/H05: route_pr_or_local exists and is wired ---


def test_route_pr_or_local_exists(recipe):
    assert "route_pr_or_local" in recipe.steps, "route_pr_or_local step missing"


def test_stage_bundle_routes_to_route_pr_or_local(recipe):
    """stage_bundle.on_success must be route_pr_or_local after groupH."""
    stage = recipe.steps["stage_bundle"]
    assert stage.on_success == "route_pr_or_local", (
        f"stage_bundle.on_success must be 'route_pr_or_local', got {stage.on_success!r}"
    )


def test_route_pr_or_local_local_branch(recipe):
    """route_pr_or_local must route to finalize_bundle when output_mode == local."""
    step = recipe.steps["route_pr_or_local"]
    assert step.action == "route", "route_pr_or_local must use action: route"
    conditions = step.on_result.conditions if step.on_result else []
    when_conditions = [c for c in conditions if c.when is not None]
    local_routes = [c for c in when_conditions if "local" in (c.when or "")]
    assert any(c.route == "finalize_bundle" for c in local_routes), (
        "route_pr_or_local must route to finalize_bundle when output_mode == local"
    )


def test_route_pr_or_local_pr_fallthrough(recipe):
    """route_pr_or_local fall-through (no when) must route to compose_research_pr."""
    step = recipe.steps["route_pr_or_local"]
    conditions = step.on_result.conditions if step.on_result else []
    fallthrough = [c for c in conditions if c.when is None]
    assert len(fallthrough) == 1 and fallthrough[0].route == "compose_research_pr", (
        "route_pr_or_local must fall through to compose_research_pr for pr mode"
    )


# --- REQ-R741-H06/H07: route_archive_or_export exists and is wired ---


def test_route_archive_or_export_exists(recipe):
    assert "route_archive_or_export" in recipe.steps, "route_archive_or_export step missing"


def test_finalize_bundle_render_routes_to_route_archive_or_export(recipe):
    """finalize_bundle_render.on_success and on_failure must be route_archive_or_export."""
    fbr = recipe.steps["finalize_bundle_render"]
    assert fbr.on_success == "route_archive_or_export", (
        "finalize_bundle_render.on_success must be 'route_archive_or_export',"
        f" got {fbr.on_success!r}"
    )
    assert fbr.on_failure == "route_archive_or_export", (
        "finalize_bundle_render.on_failure must be 'route_archive_or_export',"
        f" got {fbr.on_failure!r}"
    )


def test_route_archive_or_export_local_branch(recipe):
    """route_archive_or_export must route to export_local_bundle when local."""
    step = recipe.steps["route_archive_or_export"]
    conditions = step.on_result.conditions if step.on_result else []
    when_conditions = [c for c in conditions if c.when is not None]
    local_routes = [c for c in when_conditions if "local" in (c.when or "")]
    assert any(c.route == "export_local_bundle" for c in local_routes), (
        "route_archive_or_export must route to export_local_bundle when output_mode == local"
    )


def test_route_archive_or_export_pr_fallthrough(recipe):
    """route_archive_or_export fall-through must route to begin_archival."""
    step = recipe.steps["route_archive_or_export"]
    conditions = step.on_result.conditions if step.on_result else []
    fallthrough = [c for c in conditions if c.when is None]
    assert len(fallthrough) == 1 and fallthrough[0].route == "begin_archival", (
        "route_archive_or_export must fall through to begin_archival for pr mode"
    )


# --- REQ-R741-H09: export_local_bundle step ---


def test_export_local_bundle_exists(recipe):
    assert "export_local_bundle" in recipe.steps, "export_local_bundle step missing"


def test_export_local_bundle_emits_local_bundle_path(recipe):
    step = recipe.steps["export_local_bundle"]
    cmd = step.with_args.get("cmd", "")
    assert "local_bundle_path=" in cmd, (
        "export_local_bundle cmd must emit local_bundle_path= for capture"
    )


def test_export_local_bundle_uses_source_dir_research_bundles(recipe):
    step = recipe.steps["export_local_bundle"]
    cmd = step.with_args.get("cmd", "")
    assert "research-bundles" in cmd, (
        "export_local_bundle must export to {source_dir}/research-bundles/"
    )
    assert "source_dir" in cmd, "export_local_bundle cmd must reference inputs.source_dir"


def test_export_local_bundle_routes_to_research_complete(recipe):
    step = recipe.steps["export_local_bundle"]
    assert step.on_success == "research_complete", (
        "export_local_bundle.on_success must be research_complete"
    )
    assert step.on_failure == "research_complete", (
        "export_local_bundle.on_failure must be research_complete"
    )


# --- REQ-R741-H08: finalize_bundle local mode ---


def test_finalize_bundle_reads_output_mode(recipe):
    """finalize_bundle cmd must read OUTPUT_MODE from inputs.output_mode."""
    step = recipe.steps["finalize_bundle"]
    cmd = step.with_args.get("cmd", "")
    assert "OUTPUT_MODE" in cmd, "finalize_bundle must set OUTPUT_MODE from inputs.output_mode"
    assert "inputs.output_mode" in cmd or "inputs.output_mode" in cmd, (
        "finalize_bundle cmd must reference ${{ inputs.output_mode }}"
    )


def test_finalize_bundle_skips_commit_in_local_mode(recipe):
    """finalize_bundle cmd must gate git commit behind OUTPUT_MODE != local."""
    step = recipe.steps["finalize_bundle"]
    cmd = step.with_args.get("cmd", "")
    # git commit must exist (for pr mode) but be conditional
    assert "git commit" in cmd, "finalize_bundle must still have git commit (for pr mode)"
    commit_pos = cmd.find("git commit")
    output_mode_check_pos = cmd.rfind("OUTPUT_MODE", 0, commit_pos)
    assert output_mode_check_pos != -1, (
        "git commit block in finalize_bundle must be guarded by OUTPUT_MODE check"
    )


def test_finalize_bundle_preserves_html_in_local_mode(recipe):
    """finalize_bundle tar exclusion must include report.html for local mode browsing."""
    step = recipe.steps["finalize_bundle"]
    cmd = step.with_args.get("cmd", "")
    assert "report.html" in cmd, (
        "finalize_bundle must reference report.html in tar exclusion for local mode"
    )


def test_finalize_bundle_render_always_runs(recipe):
    """finalize_bundle.on_success must always be finalize_bundle_render (no skip)."""
    fb = recipe.steps["finalize_bundle"]
    assert fb.on_success == "finalize_bundle_render", (
        "finalize_bundle.on_success must be finalize_bundle_render (always runs)"
    )
    fbr = recipe.steps["finalize_bundle_render"]
    assert not getattr(fbr, "skip_when_false", None), (
        "finalize_bundle_render must never have skip_when_false"
    )


# --- REQ-R741-H15: research-bundles kitchen rule ---


def test_research_bundles_documented_in_kitchen_rules(recipe):
    """research.yaml kitchen_rules must document research-bundles/ directory."""
    rules_text = " ".join(recipe.kitchen_rules or [])
    assert "research-bundles" in rules_text, (
        "kitchen_rules must document the research-bundles/ output directory"
    )


# --- REQ-R741-H02: semantic validator rule ---


def test_research_output_mode_enum_rule_fires_for_invalid_value():
    """research_output_mode_enum rule must fire ERROR when output_mode.default is invalid."""
    import yaml

    from autoskillit.core import Severity
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.io import _parse_recipe
    from autoskillit.recipe.validator import run_semantic_rules

    src = RESEARCH_RECIPE_PATH.read_text()
    data = yaml.safe_load(src)
    data["ingredients"]["output_mode"] = {"default": "bogus", "required": False}
    bad_recipe = _parse_recipe(data)
    ctx = make_validation_context(bad_recipe)
    findings = run_semantic_rules(ctx)
    rule_findings = [f for f in findings if f.rule == "research_output_mode_enum"]
    assert rule_findings, (
        "research_output_mode_enum rule must fire for invalid output_mode default"
    )
    assert any(f.severity == Severity.ERROR for f in rule_findings), (
        "research_output_mode_enum finding must be ERROR severity"
    )


def test_research_output_mode_enum_rule_clean_for_valid_values():
    """research_output_mode_enum rule must NOT fire for 'local' or 'pr'."""
    import yaml

    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.io import _parse_recipe
    from autoskillit.recipe.validator import run_semantic_rules

    for valid in ("local", "pr"):
        src = RESEARCH_RECIPE_PATH.read_text()
        data = yaml.safe_load(src)
        data["ingredients"]["output_mode"] = {"default": valid, "required": False}
        recipe = _parse_recipe(data)
        ctx = make_validation_context(recipe)
        findings = run_semantic_rules(ctx)
        rule_findings = [f for f in findings if f.rule == "research_output_mode_enum"]
        assert not rule_findings, (
            f"research_output_mode_enum must not fire for valid value {valid!r}"
        )


def test_generate_report_steps_pass_output_mode(recipe):
    """All generate_report steps must pass --output-mode flag in skill_command."""
    for step_name in ("generate_report", "generate_report_inconclusive", "re_generate_report"):
        step = recipe.steps[step_name]
        cmd = step.with_args.get("skill_command", "")
        assert "--output-mode" in cmd, f"{step_name} skill_command must include --output-mode flag"


def test_generate_report_steps_pass_issue_url(recipe):
    """generate_report, generate_report_inconclusive, re_generate_report must pass --issue-url."""
    for step_name in ("generate_report", "generate_report_inconclusive", "re_generate_report"):
        step = recipe.steps[step_name]
        cmd = step.with_args.get("skill_command", "")
        assert "--issue-url" in cmd, f"{step_name} skill_command must include --issue-url flag"
