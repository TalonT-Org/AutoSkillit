"""Tests for note/with_args shape contradiction detection on all bundled recipes."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.io import all_validated_recipe_paths, load_recipe
from autoskillit.recipe.registry import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ALL_PATHS = all_validated_recipe_paths(_PROJECT_ROOT)


@pytest.mark.parametrize("recipe_path", _ALL_PATHS, ids=[p.stem for p in _ALL_PATHS])
def test_no_note_shape_contradictions_in_bundled_recipes(recipe_path: Path) -> None:
    """Every bundled recipe must be free of note/with shape contradictions."""
    recipe = load_recipe(recipe_path)
    findings = run_semantic_rules(recipe)
    contradictions = [
        f
        for f in findings
        if f.rule == "note-shape-contradiction" and f.severity == Severity.ERROR
    ]
    assert contradictions == [], (
        f"Recipe '{recipe_path.stem}' has note/with shape contradictions:\n"
        + "\n".join(f"  step '{f.step_name}': {f.message}" for f in contradictions)
    )
