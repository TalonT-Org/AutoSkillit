"""Contract tests: research sub-recipe semantic rules and dataflow analysis."""

from __future__ import annotations

from pathlib import Path

import pytest

import autoskillit.recipe  # noqa: F401 -- pyright: ignore[reportUnusedImport] -- triggers rule registration
from autoskillit.core import Severity
from autoskillit.recipe.io import builtin_recipes_dir, builtin_sub_recipes_dir, load_recipe
from autoskillit.recipe.schema import DataFlowReport, RecipeKind
from autoskillit.recipe.validator import analyze_dataflow, run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

RECIPE_DIR = builtin_recipes_dir()

_RESEARCH_SUB_RECIPE_PATHS: list[Path] = sorted(
    builtin_sub_recipes_dir().glob("research-*.yaml"),
)

if not _RESEARCH_SUB_RECIPE_PATHS:
    pytest.skip("No research sub-recipe YAMLs found", allow_module_level=True)


@pytest.mark.parametrize(
    "recipe_path",
    _RESEARCH_SUB_RECIPE_PATHS,
    ids=lambda p: p.stem,
)
def test_research_sub_recipe_has_no_error_severity_findings(recipe_path: Path) -> None:
    recipe = load_recipe(recipe_path)
    findings = run_semantic_rules(recipe)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert not errors, f"{recipe_path.stem}: {len(errors)} ERROR finding(s): " + "; ".join(
        f"{f.rule}: {f.message}" for f in errors
    )


@pytest.mark.parametrize(
    "path",
    _RESEARCH_SUB_RECIPE_PATHS,
    ids=lambda p: p.stem,
)
def test_research_sub_recipe_has_clean_dataflow(path: Path) -> None:
    recipe = load_recipe(path)
    report = analyze_dataflow(recipe)
    assert isinstance(report, DataFlowReport)
    assert report.warnings == [], (
        f"{path.stem}: {len(report.warnings)} dataflow warning(s): "
        + "; ".join(f"{w.code}@{w.step_name}: {w.message}" for w in report.warnings)
    )


# T1: All four research sub-recipes declare kind: food-truck
@pytest.mark.parametrize(
    "recipe_name",
    [
        "research-design",
        "research-implement",
        "research-review",
        "research-archive",
    ],
)
def test_research_sub_recipes_declare_food_truck_kind(recipe_name: str) -> None:
    recipe = load_recipe(RECIPE_DIR / f"{recipe_name}.yaml")
    assert recipe.kind == RecipeKind.FOOD_TRUCK


# T8: Archive recipe has no dead ingredient declarations
def test_archive_recipe_no_dead_required_ingredients() -> None:
    recipe = load_recipe(RECIPE_DIR / "research-archive.yaml")
    ingredient_names = set(recipe.ingredients.keys())
    assert "all_diagram_paths" not in ingredient_names
    assert "report_path_after_finalize" not in ingredient_names
