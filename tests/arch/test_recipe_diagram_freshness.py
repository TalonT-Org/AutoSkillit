"""Parametrized diagram freshness enforcement: bundled recipes must have non-stale
diagrams; missing diagrams are xfail(strict=True) with shrink-enforcement meta-test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from autoskillit.recipe.diagrams import check_diagram_staleness
from autoskillit.recipe.io import builtin_recipes_dir

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_RECIPES_DIR = builtin_recipes_dir()
_BUNDLED_RECIPE_YAML_PATHS = sorted(_RECIPES_DIR.glob("*.yaml"))

MINIMUM_RECIPE_COUNT = 10
CURRENT_XFAIL_CAP = 10

_RECIPES_WITH_DIAGRAMS = [
    p for p in _BUNDLED_RECIPE_YAML_PATHS if (_RECIPES_DIR / "diagrams" / f"{p.stem}.md").exists()
]
_WITHOUT_SET = set(_BUNDLED_RECIPE_YAML_PATHS) - set(_RECIPES_WITH_DIAGRAMS)
_RECIPES_WITHOUT_DIAGRAMS = sorted(_WITHOUT_SET)


def _make_params() -> list[Any]:
    params: list[Any] = []
    for p in _BUNDLED_RECIPE_YAML_PATHS:
        if p in _WITHOUT_SET:
            params.append(
                pytest.param(
                    p,
                    id=p.stem,
                    marks=pytest.mark.xfail(
                        strict=True,
                        reason=f"diagram not yet created for {p.stem}",
                    ),
                )
            )
        else:
            params.append(pytest.param(p, id=p.stem))
    return params


@pytest.mark.parametrize("recipe_path", _make_params())
def test_bundled_recipe_diagram_not_stale(recipe_path: Path) -> None:
    is_stale = check_diagram_staleness(recipe_path.stem, _RECIPES_DIR, recipe_path)
    assert not is_stale, (
        f"Diagram for '{recipe_path.stem}' is stale or missing. "
        f"Run '/render-recipe {recipe_path.stem}' to regenerate."
    )


def test_minimum_recipe_count() -> None:
    assert len(_BUNDLED_RECIPE_YAML_PATHS) >= MINIMUM_RECIPE_COUNT, (
        f"Expected at least {MINIMUM_RECIPE_COUNT} bundled recipe YAMLs, "
        f"found {len(_BUNDLED_RECIPE_YAML_PATHS)}. "
        "Is builtin_recipes_dir() resolving correctly?"
    )


def test_xfail_diagram_count_is_shrinking() -> None:
    assert len(_RECIPES_WITHOUT_DIAGRAMS) <= CURRENT_XFAIL_CAP, (
        f"Expected at most {CURRENT_XFAIL_CAP} recipes without diagrams, "
        f"found {len(_RECIPES_WITHOUT_DIAGRAMS)}: "
        f"{sorted(p.stem for p in _RECIPES_WITHOUT_DIAGRAMS)}. "
        "A new recipe was added without a diagram — either create the diagram "
        "or raise CURRENT_XFAIL_CAP (with justification)."
    )


def test_check_diagram_staleness_missing_is_stale(tmp_path: Path) -> None:
    (tmp_path / "diagrams").mkdir()
    recipe_yaml = tmp_path / "fake_recipe.yaml"
    recipe_yaml.write_text("steps: []\n")
    assert check_diagram_staleness("fake_recipe", tmp_path, recipe_yaml)
