"""Contract-level tests for write_behavior + output_dir coherence in planner.yaml.

Enforces the invariant: every planner skill step that expects writes (write_behavior:
always) must declare output_dir to set the allowed_write_prefix before session launch.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_RECIPE_DIR = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "recipes"
_CONTRACTS_PATH = (
    Path(__file__).parent.parent.parent / "src" / "autoskillit" / "recipe" / "skill_contracts.yaml"
)


@pytest.fixture(scope="module")
def planner_yaml() -> dict:
    return yaml.safe_load((_RECIPE_DIR / "planner.yaml").read_text())


@pytest.fixture(scope="module")
def contracts() -> dict:
    return yaml.safe_load(_CONTRACTS_PATH.read_text())


def _skill_name_from_command(command: str) -> str | None:
    """Extract bare skill name from a skill_command string."""
    m = re.search(r"/(?:autoskillit:)?([a-z][a-z0-9-]+)", command)
    return m.group(1) if m else None


def test_planner_skills_with_write_behavior_always_have_output_dir(
    planner_yaml: dict, contracts: dict
) -> None:
    """Every planner run_skill step whose skill has write_behavior: always must declare output_dir.

    This prevents a skill that is expected to write (write_behavior: always) from running
    without an allowed_write_prefix — which would cause zero-write detection failures and
    leave write scope enforcement unenforced.
    """
    skills = contracts.get("skills", {})
    steps = planner_yaml.get("steps", {})

    violations: list[str] = []
    for step_name, step in steps.items():
        if not isinstance(step, dict):
            continue
        if step.get("tool") != "run_skill":
            continue
        with_block = step.get("with", {}) or {}
        skill_cmd = str(with_block.get("skill_command", ""))
        skill_name = _skill_name_from_command(skill_cmd)
        if skill_name is None:
            continue
        contract = skills.get(skill_name, {})
        if contract.get("write_behavior") == "always":
            if not with_block.get("output_dir"):
                violations.append(f"{step_name} ({skill_name})")

    assert not violations, (
        f"planner.yaml run_skill steps with write_behavior=always but no output_dir: "
        f"{violations}. Add output_dir: '${{{{ context.planner_dir }}}}' (or subdirectory) "
        "to each step's with: block."
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
