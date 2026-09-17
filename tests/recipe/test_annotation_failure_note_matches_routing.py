"""Keep annotation-failure notes aligned with their declared failure routes."""

from __future__ import annotations

import re

import pytest

from autoskillit.core.io import load_yaml
from autoskillit.recipe.io import builtin_recipes_dir

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_ANNOTATION_STEPS = (
    ("implementation", "annotate_pr_diff"),
    ("implementation-groups", "annotate_pr_diff"),
    ("remediation", "annotate_pr_diff"),
    ("merge-prs", "annotate_pr_diff"),
)


@pytest.mark.parametrize(
    ("recipe_name", "step_name"),
    _ANNOTATION_STEPS,
    ids=[recipe_name for recipe_name, _ in _ANNOTATION_STEPS],
)
def test_annotation_failure_note_routes_to_declared_failure_step(
    recipe_name: str,
    step_name: str,
) -> None:
    step = load_yaml(builtin_recipes_dir() / f"{recipe_name}.yaml")["steps"][step_name]
    note = step["note"]
    match = re.search(r"\bRoutes to ([a-z][a-z0-9_-]*)\b", note, re.IGNORECASE)

    assert match is not None, (
        f"{recipe_name}.{step_name}.note must use the established 'Routes to <step>' convention"
    )
    assert match.group(1) == step["on_failure"]
