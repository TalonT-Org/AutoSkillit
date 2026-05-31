"""Contract-level tests for write_behavior + output_dir coherence across bundled recipes.

Enforces two invariants for every run_skill step:
- write_behavior=always: output_dir must be declared unconditionally.
- write_behavior=conditional: output_dir must be declared unconditionally (same as always).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import SKILL_TOOLS
from autoskillit.core.io import load_yaml
from autoskillit.recipe.contracts import load_bundled_manifest, resolve_skill_name
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]

_RECIPE_DIR = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "recipes"


@pytest.fixture(scope="module")
def planner_yaml() -> dict:
    return load_yaml(_RECIPE_DIR / "planner.yaml")


_ALL_BUNDLED_RECIPE_PATHS = sorted(builtin_recipes_dir().glob("*.yaml"))


@pytest.mark.parametrize("recipe_yaml", _ALL_BUNDLED_RECIPE_PATHS, ids=lambda p: p.stem)
def test_write_skill_steps_have_output_dir(recipe_yaml: Path) -> None:
    """Every run_skill step with write-capable behavior must declare output_dir.

    write_behavior=always: output_dir required unconditionally.
    write_behavior=conditional: output_dir required unconditionally.
    """
    recipe = load_recipe(recipe_yaml)
    manifest = load_bundled_manifest()
    skills = manifest.get("skills", {})

    violations: list[str] = []
    for step_name, step in recipe.steps.items():
        if step.tool not in SKILL_TOOLS:
            continue
        skill_cmd = str((step.with_args or {}).get("skill_command", ""))
        skill = resolve_skill_name(skill_cmd)
        if skill is None:
            continue
        skill_data = skills.get(skill, {})
        write_behavior = skill_data.get("write_behavior")
        output_dir = (step.with_args or {}).get("output_dir")

        if write_behavior == "always":
            if not output_dir:
                violations.append(
                    f"{step_name} ({skill}): write_behavior=always but no output_dir"
                )
        elif write_behavior == "conditional":
            if not output_dir:
                violations.append(
                    f"{step_name} ({skill}): write_behavior=conditional but no output_dir"
                )

    assert not violations, (
        f"{recipe_yaml.stem}: run_skill steps missing required output_dir:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_output_dir_is_under_planner_dir(planner_yaml: dict) -> None:
    """Every output_dir in a planner.yaml run_skill step must be under context.planner_dir.

    This prevents a misconfigured step from setting the write prefix to a directory
    that contains source code, defeating write isolation.
    """
    steps = planner_yaml.get("steps", {})

    violations: list[str] = []
    for step_name, step in steps.items():
        if not isinstance(step, dict):
            continue
        if step.get("tool") != "run_skill":
            continue
        with_block = step.get("with", {}) or {}
        output_dir = with_block.get("output_dir", "")
        if not output_dir:
            continue
        if not str(output_dir).startswith("${{ context.planner_dir }}"):
            violations.append(f"{step_name}: {output_dir!r}")

    assert not violations, (
        f"planner.yaml run_skill steps with output_dir NOT under context.planner_dir: "
        f"{violations}. All planner output_dirs must be rooted at "
        "'${{ context.planner_dir }}' to prevent write scope escaping into source directories."
    )
