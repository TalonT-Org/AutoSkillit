"""Tests that loop-iterated steps use iteration-scoped output_dir (1a, 1f)."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]

_RECIPE_NAMES = ["implementation", "implementation-groups", "remediation"]


@pytest.fixture(scope="module", params=_RECIPE_NAMES)
def bundled_recipe(request):
    return load_recipe(builtin_recipes_dir() / f"{request.param}.yaml")


def test_loop_iterated_run_skill_steps_use_iter_scoped_output_dir(bundled_recipe) -> None:
    """Bundled loop artifact producers pass the production provenance rule."""
    violations = [
        finding
        for finding in run_semantic_rules(bundled_recipe)
        if finding.rule == "loop-iterated-step-requires-iteration-scoped-output"
    ]

    assert not violations, (
        "loop artifact producers have static resolved output directories: "
        f"{[(finding.step_name, finding.message) for finding in violations]}"
    )


def test_annotate_pr_diff_and_enrich_diff_context_share_output_dir(bundled_recipe) -> None:
    """annotate_pr_diff and enrich_diff_context must use the same output_dir template.

    These two steps are writer and reader of the same iteration directory.
    Path divergence between them causes enrich_diff_context to read from a directory
    that annotate_pr_diff did not write to.
    """
    steps = bundled_recipe.steps
    annotate_step = steps.get("annotate_pr_diff")
    enrich_step = steps.get("enrich_diff_context")

    if annotate_step is None or enrich_step is None:
        pytest.skip("Recipe does not have both annotate_pr_diff and enrich_diff_context steps")

    annotate_dir = annotate_step.with_args.get("output_dir", "")
    enrich_dir = enrich_step.with_args.get("output_dir", "")
    assert annotate_dir == enrich_dir, (
        f"annotate_pr_diff output_dir={annotate_dir!r} != "
        f"enrich_diff_context output_dir={enrich_dir!r}. "
        f"These co-located steps must use the same output_dir template."
    )
