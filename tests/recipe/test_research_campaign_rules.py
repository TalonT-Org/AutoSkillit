"""Contract tests: research-campaign.yaml semantic rules."""

from __future__ import annotations

import pytest

import autoskillit.recipe  # noqa: F401 -- pyright: ignore[reportUnusedImport] -- triggers rule registration
from autoskillit.core import Severity
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import _RULE_REGISTRY, run_semantic_rules

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
    )


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
    rule_name = "dispatch-capture-fields-in-sentinel-contract"
    registered_names = {r.name for r in _RULE_REGISTRY}
    if rule_name not in registered_names:
        pytest.skip(f"Rule {rule_name!r} not yet registered")
    findings = run_semantic_rules(validation_ctx)
    matched = [f for f in findings if f.rule == rule_name]
    assert not matched, "; ".join(f"{f.rule}: {f.message}" for f in matched)
