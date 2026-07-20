"""Tests for post-run diagnostic analysis steps in recipes."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.repository import builtin_recipes_dir

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


# DIAG_C7: analyze-pipeline-health skill exists
def test_analyze_pipeline_health_skill_exists():
    """analyze-pipeline-health skill directory must exist with SKILL.md."""
    from autoskillit.core import pkg_root

    skill_dir = pkg_root() / "skills_extended" / "analyze-pipeline-health"
    assert (skill_dir / "SKILL.md").is_file(), (
        f"analyze-pipeline-health SKILL.md not found at {skill_dir / 'SKILL.md'}"
    )


# DIAG_C8: coordinator SKILL.md validates scanner completion and emits output delimiter
def test_analyze_pipeline_health_coordinator_validates_scanners():
    """analyze-pipeline-health SKILL.md must instruct scanner validation and emit a delimiter."""
    from autoskillit.core import pkg_root

    skill_path = pkg_root() / "skills_extended" / "analyze-pipeline-health" / "SKILL.md"
    content = skill_path.read_text()

    assert "scan_result:" in content, (
        "analyze-pipeline-health SKILL.md Step 4 must instruct validation of 'scan_result:' "
        "completion token from each scanner"
    )
    assert "---pipeline-health-result---" in content, (
        "analyze-pipeline-health SKILL.md Step 6 must instruct emitting "
        "the '---pipeline-health-result---' delimiter"
    )


# DIAG_C9: analyze_pipeline_health steps have required fields in implementation recipe
@pytest.mark.parametrize("recipe_name", ["implementation", "remediation", "merge-prs"])
def test_analyze_pipeline_health_steps_have_required_fields(recipe_name):
    """analyze_pipeline_health steps must have required optional-step fields."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    for name, step in recipe.steps.items():
        if name.startswith("analyze_pipeline_health"):
            assert step.on_context_limit is not None, f"{name} missing on_context_limit"
            assert step.optional is True, f"{name} optional must be True"
            assert step.skip_when_false == "inputs.pipeline_health", (
                f"{name} skip_when_false must be inputs.pipeline_health"
            )


# T7: REQ-TEST-004
def test_implementation_family_recipes_route_terminals_through_diagnostics() -> None:
    """REQ-TEST-004: Every register_clone_* terminal in implementation-family recipes
    routes through an analyze_pipeline_health* step gated on pipeline_health."""
    recipes_dir = builtin_recipes_dir()
    target_names = ["implementation.yaml", "implementation-groups.yaml"]
    missing: list[str] = []
    for name in target_names:
        path = recipes_dir / name
        if not path.exists():
            missing.append(name)
    assert not missing, f"Recipe files not found: {missing}"

    diag_step_prefixes = ("analyze_pipeline_health",)
    required_post_run_refs = ("inputs.pipeline_health",)

    for name in target_names:
        recipe_path = recipes_dir / name
        recipe = load_recipe(recipe_path)
        steps = recipe.steps
        clone_steps = [(n, s) for n, s in steps.items() if n.startswith("register_clone_")]
        assert clone_steps, f"{name}: expected at least one register_clone_* step"
        for step_name, cs in clone_steps:
            # The step must route on_success to an analyze_pipeline_health* step
            next_step = cs.on_success
            assert next_step is not None, f"{name}: {step_name} has no on_success"
            assert next_step.startswith(diag_step_prefixes), (
                f"{name}: {step_name}.on_success={next_step!r} must start with "
                f"{diag_step_prefixes!r}"
            )
            # The analyze_pipeline_health* step must gate on inputs.pipeline_health
            diag = steps.get(next_step)
            assert diag is not None, (
                f"{name}: step {step_name} routes to {next_step!r} but no such step exists"
            )
            assert diag.skip_when_false in required_post_run_refs, (
                f"{name}: {next_step}.skip_when_false={diag.skip_when_false!r} "
                f"must reference one of {required_post_run_refs!r}"
            )
