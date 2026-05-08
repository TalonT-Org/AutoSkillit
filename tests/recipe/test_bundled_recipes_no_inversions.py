"""Bundled-recipe regression guard: no capture inversions or hardcoded event literals."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_BUNDLED_RECIPE_PATHS: list[Path] = sorted(builtin_recipes_dir().glob("*.yaml"))


@pytest.mark.parametrize("recipe_path", _BUNDLED_RECIPE_PATHS, ids=lambda p: p.stem)
def test_bundled_recipe_has_no_capture_inversions(recipe_path):
    """After Part B: all bundled recipes produce zero inversion findings. Before
    Part B: the four recipes with wait_for_ci event='push' fail this test."""
    recipe = load_recipe(recipe_path)
    findings = run_semantic_rules(recipe)
    inversions = [f for f in findings if "inversion" in f.rule]
    assert inversions == [], f"{recipe_path.stem} has inversions: " + "\n".join(
        f"  {f.rule}: {f.message}" for f in inversions
    )


@pytest.mark.parametrize("recipe_path", _BUNDLED_RECIPE_PATHS, ids=lambda p: p.stem)
def test_diagnose_ci_skill_command_uses_captured_ci_event(recipe_path):
    """No skill_command step may pass a hardcoded trigger event as a positional.
    All /autoskillit:diagnose-ci invocations must thread ${{ context.ci_event }}."""
    recipe = yaml.safe_load(recipe_path.read_text())
    for step_name, step in recipe.get("steps", {}).items():
        cmd = step.get("skill_command") or step.get("with", {}).get("skill_command", "")
        if "diagnose-ci" in cmd:
            hardcoded_events = {"push", "merge_group"}
            assert "${{ context.ci_event }}" in cmd or not any(
                ev in cmd.split() for ev in hardcoded_events
            ), (
                f"{recipe_path.stem}:{step_name} — diagnose-ci call hardcodes a trigger "
                f"event literal instead of threading ${{{{ context.ci_event }}}}"
            )
