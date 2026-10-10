"""Contract-level tests for planner.yaml write isolation.

Every planner run_skill output_dir must be rooted at the planner run directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]

_RECIPE_DIR = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "recipes"


@pytest.fixture(scope="module")
def planner_yaml() -> dict:
    return load_yaml(_RECIPE_DIR / "planner.yaml")


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
