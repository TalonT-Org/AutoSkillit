"""Tests for skip-inviting note text on optional recipe steps."""

from __future__ import annotations

import re

import pytest

from autoskillit.core import pkg_root
from autoskillit.recipe.io import load_recipe
from tests.recipe.conftest import BUNDLED_RECIPE_NAMES

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_SKIP_INVITING_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("never blocks", re.compile(r"never\s+blocks?", re.IGNORECASE)),
    ("best-effort", re.compile(r"best[- ]effort", re.IGNORECASE)),
    ("optional: true (prose)", re.compile(r"optional[=: ]+true", re.IGNORECASE)),
    ("can be skipped", re.compile(r"can be skipped", re.IGNORECASE)),
    ("non-critical", re.compile(r"non[- ]critical", re.IGNORECASE)),
    ("not required", re.compile(r"not required", re.IGNORECASE)),
]


@pytest.mark.parametrize("recipe_name", BUNDLED_RECIPE_NAMES)
def test_no_skip_inviting_notes_on_optional_steps(recipe_name: str) -> None:
    """Note fields on optional steps with skip_when_false must not contain skip-inviting text."""
    recipe_obj = load_recipe(pkg_root() / "recipes" / f"{recipe_name}.yaml")
    violations: list[str] = []
    for step_name, step in recipe_obj.steps.items():
        if not step.optional or not step.skip_when_false:
            continue
        note = step.note or ""
        if not note:
            continue
        for phrase, pattern in _SKIP_INVITING_PATTERNS:
            if pattern.search(note):
                violations.append(f"step '{step_name}' note contains '{phrase}': {note!r}")
    assert violations == [], (
        f"Recipe '{recipe_name}' has skip-inviting note text on optional steps:\n"
        + "\n".join(violations)
    )
