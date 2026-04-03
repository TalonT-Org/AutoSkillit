"""Shared test helpers for tests/recipe/."""

from __future__ import annotations

from pathlib import Path

import yaml

from autoskillit.recipe.io import _parse_step
from autoskillit.recipe.schema import Recipe

# Known violations fixed in Parts B and C — excluded from general semantic-error assertions.
NO_AUTOSKILLIT_IMPORT = "no-autoskillit-import-in-skill-python-block"

# ---------------------------------------------------------------------------
# Shared helper: recipe workflow factory
# ---------------------------------------------------------------------------


def _make_workflow(steps: dict[str, dict]) -> Recipe:
    parsed_steps = {name: _parse_step(data) for name, data in steps.items()}
    return Recipe(name="test", description="test", steps=parsed_steps, kitchen_rules=["test"])


# ---------------------------------------------------------------------------
# Shared fixture data: valid recipe dict and YAML writer
# ---------------------------------------------------------------------------

VALID_RECIPE = {
    "name": "test-recipe",
    "description": "A test recipe",
    "ingredients": {
        "test_dir": {"description": "Dir to test", "required": True},
        "branch": {"description": "Branch", "default": "main"},
    },
    "kitchen_rules": ["NEVER use native tools"],
    "steps": {
        "run_tests": {
            "tool": "test_check",
            "with": {"worktree_path": "${{ inputs.test_dir }}"},
            "on_success": "done",
            "on_failure": "escalate",
        },
        "done": {"action": "stop", "message": "Tests passed."},
        "escalate": {"action": "stop", "message": "Need help."},
    },
}


def _write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.dump(data, default_flow_style=False))
    return path
