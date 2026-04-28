"""Tests for implement-findings.yaml recipe (T1–T14)."""

from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.schema import RecipeKind
from autoskillit.recipe.validator import run_semantic_rules

from tests.recipe.conftest import NO_AUTOSKILLIT_IMPORT

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

RECIPE_PATH = builtin_recipes_dir() / "implement-findings.yaml"


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(RECIPE_PATH)


# T1
def test_implement_findings_recipe_file_exists() -> None:
    assert RECIPE_PATH.exists()


# T2
def test_implement_findings_recipe_parses(recipe) -> None:
    assert recipe.name == "implement-findings"
    assert recipe.kind == RecipeKind.STANDARD


# T3
def test_implement_findings_recipe_validates_cleanly(recipe) -> None:
    findings = run_semantic_rules(recipe)
    errors = [
        f
        for f in findings
        if f.severity == Severity.ERROR and f.rule != NO_AUTOSKILLIT_IMPORT
    ]
    assert errors == []


# T4
def test_implement_findings_issue_urls_required(recipe) -> None:
    assert recipe.ingredients["issue_urls"].required is True
    assert recipe.ingredients["issue_urls"].default is None


# T5
def test_implement_findings_base_branch_default(recipe) -> None:
    assert recipe.ingredients["base_branch"].default == "integration"


# T6
def test_implement_findings_execution_map_hidden(recipe) -> None:
    ing = recipe.ingredients["execution_map"]
    assert ing.hidden is True
    assert ing.default == ""


# T7
def test_implement_findings_max_parallel_hidden(recipe) -> None:
    ing = recipe.ingredients["max_parallel"]
    assert ing.hidden is True
    assert ing.default == "6"


# T8
def test_implement_findings_check_resume_callable(recipe) -> None:
    step = recipe.steps["check_resume"]
    assert step.with_args["callable"] == "autoskillit.fleet._findings_rpc.parse_and_resume"


# T9
def test_implement_findings_load_bem_callable(recipe) -> None:
    step = recipe.steps["load_bem_from_file"]
    assert step.with_args["callable"] == "autoskillit.fleet._findings_rpc.load_execution_map"


# T10
@pytest.mark.parametrize("step_name", ["run_bem_internally"])
def test_implement_findings_run_skill_fault_guards(recipe, step_name: str) -> None:
    step = recipe.steps[step_name]
    assert step.on_failure is not None
    assert step.on_context_limit is not None


# T11
def test_implement_findings_requires_packs(recipe) -> None:
    packs = set(recipe.requires_packs)
    assert {"github", "clone"}.issubset(packs)


# T12
def test_implement_findings_route_bem_mode_is_route(recipe) -> None:
    step = recipe.steps["route_bem_mode"]
    assert step.action == "route"
    assert step.on_result is not None


# T13
@pytest.mark.parametrize("step_name", ["done", "escalate_stop"])
def test_implement_findings_terminal_steps(recipe, step_name: str) -> None:
    step = recipe.steps[step_name]
    assert step.action == "stop"
    assert step.message


# T14
def test_implement_findings_no_undeclared_capture_keys(recipe) -> None:
    findings = run_semantic_rules(recipe)
    undeclared = [f for f in findings if f.rule == "undeclared-capture-key"]
    assert undeclared == []
