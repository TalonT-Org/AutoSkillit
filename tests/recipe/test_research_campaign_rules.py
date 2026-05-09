"""Contract tests: research-campaign.yaml semantic rules."""

from __future__ import annotations

import pytest

import autoskillit.recipe  # noqa: F401 -- pyright: ignore[reportUnusedImport] -- triggers rule registration
from autoskillit.core import Severity
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import CampaignDispatch, Recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

RECIPE_PATH = builtin_recipes_dir() / "campaigns" / "research-campaign.yaml"


@pytest.fixture(scope="module")
def validation_ctx():
    if not RECIPE_PATH.exists():
        pytest.skip("research-campaign.yaml not yet created")
    recipe = load_recipe(RECIPE_PATH)
    return make_validation_context(
        recipe,
        available_recipes=frozenset(
            {"research-design", "research-implement", "research-review", "research-archive"}
        ),
        project_dir=RECIPE_PATH.parent.parent.parent,
    )


@pytest.fixture(scope="module")
def campaign_recipe() -> Recipe:
    if not RECIPE_PATH.exists():
        pytest.skip("research-campaign.yaml not yet created")
    return load_recipe(RECIPE_PATH)


def _get_dispatch(recipe: Recipe, name: str) -> CampaignDispatch | None:
    return next((d for d in recipe.dispatches if d.name == name), None)


def test_research_campaign_file_exists() -> None:
    assert RECIPE_PATH.exists()


def test_research_campaign_no_error_findings(validation_ctx) -> None:
    findings = run_semantic_rules(validation_ctx)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert not errors, f"{len(errors)} ERROR finding(s): " + "; ".join(
        f"{f.rule}: {f.message}" for f in errors
    )


def test_dispatch_recipe_exists_passes(validation_ctx) -> None:
    findings = run_semantic_rules(validation_ctx)
    matched = [f for f in findings if f.rule == "dispatch-recipe-exists"]
    assert not matched, "; ".join(f"{f.rule}: {f.message}" for f in matched)


def test_campaign_ingredient_refs_have_prior_capture_passes(validation_ctx) -> None:
    findings = run_semantic_rules(validation_ctx)
    matched = [f for f in findings if f.rule == "campaign-ingredient-refs-have-prior-capture"]
    assert not matched, "; ".join(f"{f.rule}: {f.message}" for f in matched)


def test_dispatch_capture_value_references_result_passes(validation_ctx) -> None:
    findings = run_semantic_rules(validation_ctx)
    matched = [f for f in findings if f.rule == "dispatch-capture-value-references-result"]
    assert not matched, "; ".join(f"{f.rule}: {f.message}" for f in matched)


def test_depends_on_acyclic_passes(validation_ctx) -> None:
    findings = run_semantic_rules(validation_ctx)
    matched = [f for f in findings if f.rule == "depends-on-acyclic"]
    assert not matched, "; ".join(f"{f.rule}: {f.message}" for f in matched)


def test_dispatch_capture_fields_in_sentinel_contract_passes(validation_ctx) -> None:
    rule_name = "dispatch-capture-field-in-sentinel"
    findings = run_semantic_rules(validation_ctx)
    matched = [f for f in findings if f.rule == rule_name]
    assert not matched, "; ".join(f"{f.rule}: {f.message}" for f in matched)


def test_dispatch_required_ingredient_provided_passes(validation_ctx) -> None:
    findings = run_semantic_rules(validation_ctx)
    matched = [f for f in findings if f.rule == "dispatch-required-ingredient-provided"]
    assert not matched, "; ".join(f"{f.rule}: {f.message}" for f in matched)


def test_campaign_dangling_ingredient_passes(validation_ctx) -> None:
    findings = run_semantic_rules(validation_ctx)
    matched = [f for f in findings if f.rule == "campaign-dangling-ingredient"]
    assert not matched, "; ".join(f"{f.rule}: {f.message}" for f in matched)


def test_run_design_captures_scope_report_and_experiment_type(campaign_recipe: Recipe) -> None:
    run_design = _get_dispatch(campaign_recipe, "run-design")
    assert run_design is not None
    assert "scope_report" in run_design.capture
    assert "experiment_type" in run_design.capture


def test_run_implement_captures_experiment_results(campaign_recipe: Recipe) -> None:
    run_impl = _get_dispatch(campaign_recipe, "run-implement")
    assert run_impl is not None
    assert "experiment_results" in run_impl.capture


def test_run_implement_forwards_task(campaign_recipe: Recipe) -> None:
    run_impl = _get_dispatch(campaign_recipe, "run-implement")
    assert run_impl is not None
    assert "task" in run_impl.ingredients


def test_run_review_forwards_required_research_ingredients(campaign_recipe: Recipe) -> None:
    run_rev = _get_dispatch(campaign_recipe, "run-review")
    assert run_rev is not None
    for key in ("task", "experiment_results", "experiment_type", "scope_report"):
        assert key in run_rev.ingredients, f"run-review missing ingredient: {key}"


def test_run_review_no_phantom_all_diagram_paths_capture(campaign_recipe: Recipe) -> None:
    run_rev = _get_dispatch(campaign_recipe, "run-review")
    assert run_rev is not None
    assert "all_diagram_paths" not in run_rev.capture


def test_run_archive_no_dead_ingredient_forwarding(campaign_recipe: Recipe) -> None:
    run_arc = _get_dispatch(campaign_recipe, "run-archive")
    assert run_arc is not None
    assert "all_diagram_paths" not in run_arc.ingredients
    assert "report_path_after_finalize" not in run_arc.ingredients


def test_dispatch_capture_field_in_all_sentinels_contract_passes(validation_ctx) -> None:
    """Per-sentinel-path validation: captures must appear in ALL sentinel paths, not just union.

    Uses the existing module-scoped `validation_ctx` fixture.
    """
    findings = run_semantic_rules(validation_ctx)
    all_sentinel_findings = [
        f for f in findings if f.rule == "dispatch-capture-field-in-all-sentinels"
    ]
    assert all_sentinel_findings == [], (
        f"Per-path sentinel validation failures: {all_sentinel_findings}"
    )
