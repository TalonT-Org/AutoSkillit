"""Bundled-recipe regression guard: no capture inversions or hardcoded event literals.

Tests in this file will FAIL against the pre-Part-B state where:
- wait_for_ci steps hardcode event='push' without upstream merge_group_trigger capture
- diagnose-ci skill_command calls hardcode 'push' as the 5th positional argument

They become green once:
- rules_reachability.py is created with event-scope-requires-upstream-capture rule
- Bundled recipes replace event: "push" with event: "${{ context.ci_event }}"
- diagnose-ci calls replace hardcoded 'push' with ${{ context.ci_event }}
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import run_semantic_rules

_BUNDLED_RECIPE_PATHS: list[Path] = sorted(builtin_recipes_dir().glob("*.yaml"))


@pytest.mark.parametrize("recipe_path", _BUNDLED_RECIPE_PATHS, ids=lambda p: p.stem)
def test_bundled_recipe_has_no_capture_inversions(recipe_path):
    """After Part B: all bundled recipes produce zero inversion findings. Before
    Part B: the four recipes with wait_for_ci event='push' fail this test."""
    recipe = load_recipe(recipe_path)
    findings = run_semantic_rules(recipe)
    inversions = [f for f in findings if "inversion" in f.rule]
    assert inversions == [], (
        f"{recipe_path.stem} has inversions: "
        + "\n".join(f"  {f.rule}: {f.message}" for f in inversions)
    )


@pytest.mark.parametrize("recipe_path", _BUNDLED_RECIPE_PATHS, ids=lambda p: p.stem)
def test_diagnose_ci_skill_command_uses_captured_ci_event(recipe_path):
    """No skill_command step may pass a hardcoded trigger event as a positional.
    All /autoskillit:diagnose-ci invocations must thread ${{ context.ci_event }}."""
    recipe = yaml.safe_load(recipe_path.read_text())
    for step_name, step in recipe.get("steps", {}).items():
        cmd = step.get("skill_command") or step.get("with", {}).get("skill_command", "")
        if "diagnose-ci" in cmd:
            assert "${{ context.ci_event }}" in cmd or "push" not in cmd.split(), (
                f"{recipe_path.stem}:{step_name} — diagnose-ci call hardcodes 'push'"
            )
