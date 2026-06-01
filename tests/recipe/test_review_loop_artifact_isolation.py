"""Tests that loop-iterated steps use iteration-scoped output_dir (1a, 1f)."""

from __future__ import annotations

import pytest

from autoskillit.core import SKILL_TOOLS
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe._rule_helpers import _find_cycle_members
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]

_RECIPE_NAMES = ["implementation", "implementation-groups", "remediation"]


@pytest.fixture(scope="module", params=_RECIPE_NAMES)
def bundled_recipe(request):
    return load_recipe(builtin_recipes_dir() / f"{request.param}.yaml")


def test_loop_iterated_run_skill_steps_use_iter_scoped_output_dir(bundled_recipe) -> None:
    """run_skill steps in review loop cycles must use iteration-scoped output_dir.

    Iteration-scoped means output_dir includes a ${{ context. variable reference
    (e.g., ${{ context.review_loop_count }}) so each iteration writes to a distinct
    directory and cannot collide with artifacts from a prior iteration.
    """
    ctx = make_validation_context(bundled_recipe)
    cycle_sets = _find_cycle_members(ctx.step_graph, bundled_recipe.steps)
    cycle_members: set[str] = set()
    for cs in cycle_sets:
        cycle_members |= cs

    violations: list[tuple[str, str]] = []
    for step_name in cycle_members:
        step = bundled_recipe.steps.get(step_name)
        if step is None or step.tool not in SKILL_TOOLS:
            continue
        output_dir = step.with_args.get("output_dir", "")
        if not output_dir:
            continue
        if "{{AUTOSKILLIT_TEMP}}" not in output_dir:
            continue
        if "${{ context." not in output_dir:
            violations.append((step_name, output_dir))

    assert not violations, (
        f"run_skill steps in loop cycle have static output_dir (not iteration-scoped): "
        f"{[(name, d) for name, d in violations]}. "
        f"Loop-iterated run_skill steps must include a ${{{{ context.<var> }}}} reference "
        f"in output_dir to prevent artifact collision between iterations."
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
