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


def test_campaign_path_coherence_rule_passes_on_valid_campaign(validation_ctx) -> None:
    """The bundled research-campaign.yaml correctly re-captures worktree_path,
    so the coherence rule should not fire."""
    findings = run_semantic_rules(validation_ctx)
    matched = [f for f in findings if f.rule == "campaign-path-coherence"]
    assert not matched, "; ".join(f"{f.rule}: {f.message}" for f in matched)


def test_campaign_path_type_enforce_rule_passes(validation_ctx) -> None:
    """All worktree_relative_path ingredients must have a corresponding worktree_path anchor."""
    findings = run_semantic_rules(validation_ctx)
    matched = [f for f in findings if f.rule == "campaign-path-type-enforce"]
    assert not matched, "; ".join(f"{f.rule}: {f.message}" for f in matched)


def test_campaign_path_coherence_rule_detects_missing_recapture() -> None:
    """Construct a campaign YAML where a dispatch invokes implement-experiment
    (which captures worktree_path) but does NOT re-capture worktree_path itself.
    The rule should emit an error."""
    import tempfile
    from pathlib import Path

    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.io import load_recipe

    # Create a minimal campaign YAML where run-implement does NOT re-capture worktree_path
    bad_yaml = """
name: bad-campaign
description: test
kind: campaign
recipe_version: "1.0.0"
requires_recipe_packs:
  - research-family
allowed_recipes:
  - research-implement

dispatches:
  - name: run-design
    recipe: research-design
    task: "Design the research"
    ingredients:
      task: "${{ inputs.task }}"
    capture:
      worktree_path: "${{ result.worktree_path }}"
      research_dir_rel: "${{ result.research_dir_rel }}"
    depends_on: []

  - name: run-implement
    recipe: research-implement
    task: "Implement the research"
    ingredients:
      worktree_path: "${{ campaign.worktree_path }}"
    capture:
      report_path: "${{ result.report_path }}"
    depends_on:
      - run-design
"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        campaign_file = tmp_path / "test-campaign.yaml"
        campaign_file.write_text(bad_yaml)

        recipe = load_recipe(campaign_file)
        ctx = make_validation_context(
            recipe,
            available_recipes=frozenset({"research-design", "research-implement"}),
            project_dir=tmp_path,
        )
        findings = run_semantic_rules(ctx)
        coherence_findings = [f for f in findings if f.rule == "campaign-path-coherence"]
        assert coherence_findings, (
            "campaign-path-coherence rule should have emitted an error for "
            "run-implement dispatch that invokes research-implement (which captures "
            "worktree_path at implement_phase) but does not re-capture worktree_path"
        )


def test_campaign_path_type_enforce_rule_detects_missing_anchor() -> None:
    """A dispatch that provides a worktree_relative_path ingredient without
    a corresponding worktree_path anchor should emit an error."""
    import tempfile
    from pathlib import Path

    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.io import load_recipe

    bad_yaml = """
name: bad-campaign
description: test
kind: campaign
recipe_version: "1.0.0"
requires_recipe_packs:
  - research-family
allowed_recipes:
  - research-implement

ingredients:
  research_dir_rel:
    description: "Repo-relative path to research dir"
    required: true
    type: worktree_relative_path

dispatches:
  - name: run-design
    recipe: research-design
    task: "Design the research"
    ingredients:
      task: "${{ inputs.task }}"
    capture:
      worktree_path: "${{ result.worktree_path }}"
      research_dir_rel: "${{ result.research_dir_rel }}"
    depends_on: []

  - name: run-implement
    recipe: research-implement
    task: "Implement the research"
    ingredients:
      research_dir_rel: "${{ campaign.research_dir_rel }}"
    capture: {}
    depends_on:
      - run-design
"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        campaign_file = tmp_path / "test-campaign.yaml"
        campaign_file.write_text(bad_yaml)

        recipe = load_recipe(campaign_file)
        ctx = make_validation_context(
            recipe,
            available_recipes=frozenset({"research-design", "research-implement"}),
            project_dir=tmp_path,
        )
        findings = run_semantic_rules(ctx)
        type_findings = [f for f in findings if f.rule == "campaign-path-type-enforce"]
        assert type_findings, (
            "campaign-path-type-enforce rule should have emitted an error for "
            "run-implement dispatch that provides research_dir_rel (type: "
            "worktree_relative_path) without a corresponding worktree_path anchor"
        )
