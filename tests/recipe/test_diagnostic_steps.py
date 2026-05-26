"""Tests for post-run diagnostic analysis steps in recipes."""

from __future__ import annotations

import pytest

from autoskillit.recipe.repository import builtin_recipes_dir
from autoskillit.recipe.yaml_loader import load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


# DIAG_C7: analyze-pipeline-health skill exists
def test_analyze_pipeline_health_skill_exists():
    """analyze-pipeline-health skill directory must exist with SKILL.md."""
    from autoskillit.core import pkg_root

    skill_dir = pkg_root() / "skills_extended" / "analyze-pipeline-health"
    assert (skill_dir / "SKILL.md").is_file(), (
        f"analyze-pipeline-health SKILL.md not found at {skill_dir / 'SKILL.md'}"
    )


# DIAG_C9: run_diagnostic steps have required fields in implementation recipe
@pytest.mark.parametrize("recipe_name", ["implementation", "remediation", "merge-prs"])
def test_run_diagnostic_steps_have_required_fields(recipe_name):
    """run_diagnostic steps must have on_context_limit, optional=true, skip_when_false."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    for name, step in recipe.steps.items():
        if name.startswith("run_diagnostic"):
            assert step.on_context_limit is not None, f"{name} missing on_context_limit"
            assert step.optional is True, f"{name} optional must be True"
            assert step.skip_when_false == "inputs.post_run_diagnostics", (
                f"{name} skip_when_false must be inputs.post_run_diagnostics"
            )
