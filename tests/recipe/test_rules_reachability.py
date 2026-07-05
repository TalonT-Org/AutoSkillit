"""Capture-inversion and event-scope reachability guards.

Tests verify that:
1. Bundled recipes produce zero inversion findings after Part B remediation.
2. Synthetic recipes with inversions are correctly flagged.
3. _bfs_with_facts correctly intersects facts at join points.
"""

from __future__ import annotations

import pytest

from autoskillit.recipe._analysis import (
    _bfs_with_facts,
    _build_step_graph,
    make_validation_context,
)
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.rules.rules_reachability import _check_capture_inversion
from autoskillit.recipe.schema import (
    Recipe,
    RecipeStep,
    StepResultCondition,
    StepResultRoute,
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_QUEUE_CAPABLE = ("implementation.yaml", "remediation.yaml", "implementation-groups.yaml")


# ---------------------------------------------------------------------------
# Bundled recipe guards (post-Part-B: must be clean)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("recipe_name", _QUEUE_CAPABLE)
def test_bundled_recipe_wait_for_ci_has_no_inversion_findings(recipe_name):
    """Post-Part-B: ci_watch reads ${{ context.ci_event }} and the producer
    (check_repo_merge_state) is upstream — no capture-inversion or event-scope
    finding is expected. This test enforces the correct end state."""
    recipe = load_recipe(builtin_recipes_dir() / recipe_name)
    findings = run_semantic_rules(recipe)
    inversions = [
        f
        for f in findings
        if f.rule in ("capture-inversion-detection", "event-scope-requires-upstream-capture")
    ]
    assert inversions == [], f"{recipe_name}: {[f.message for f in inversions]}"


# ---------------------------------------------------------------------------
# Synthetic recipe tests
# ---------------------------------------------------------------------------


def test_synthetic_recipe_with_inversion_is_flagged():
    """Independent: a minimal recipe with entry → wait_for_ci(event='push') →
    check_merge_group_trigger must produce exactly one capture-inversion finding."""
    recipe = _make_synthetic_recipe_with_inversion()
    findings = run_semantic_rules(recipe)
    matching = [f for f in findings if f.rule == "event-scope-requires-upstream-capture"]
    assert len(matching) == 1
    assert "merge_group_trigger" in matching[0].message


def test_synthetic_recipe_without_inversion_passes():
    """Reverse the order: entry → check_repo_merge_state → wait_for_ci(event from
    context.ci_event). No finding."""
    recipe = _make_synthetic_recipe_upstream_capture()
    findings = run_semantic_rules(recipe)
    matching = [f for f in findings if f.rule == "event-scope-requires-upstream-capture"]
    assert matching == []


def test_bfs_with_facts_intersects_at_joins():
    """Unit: on a recipe where branch A establishes (X, 'a') and branch B establishes
    nothing, the join point has facts = {} (conservative intersection). Downstream
    of the join, X is not known."""
    recipe = _make_branching_recipe()
    graph = _build_step_graph(recipe)
    facts = _bfs_with_facts(graph, recipe, start="entry")
    assert facts["join"] == {frozenset()}  # no fact holds on every path
    assert frozenset([("X", "a")]) in facts["on_branch_a"]  # fact holds here


# ---------------------------------------------------------------------------
# Synthetic recipe builders
# ---------------------------------------------------------------------------


def _make_synthetic_recipe_with_inversion() -> Recipe:
    """entry → wait_for_ci(event='push') → check_merge_group_trigger.

    The wait_for_ci step hardcodes event='push' but the producer of
    merge_group_trigger (check_merge_group_trigger) runs strictly downstream.
    This represents the pre-Part-B bug in the bundled recipes.
    """
    entry = RecipeStep(name="entry", action="route", on_success="ci_watch")
    ci_watch = RecipeStep(
        name="ci_watch",
        tool="wait_for_ci",
        with_args={"branch": "main", "event": "push"},
        on_success="check_merge_group_trigger",
    )
    check_step = RecipeStep(
        name="check_merge_group_trigger",
        tool="check_repo_merge_state",
        capture={"merge_group_trigger": "${{ result.merge_group_trigger }}"},
    )
    return Recipe(
        name="synthetic-inversion",
        description="test recipe with capture inversion",
        steps={
            "entry": entry,
            "ci_watch": ci_watch,
            "check_merge_group_trigger": check_step,
        },
    )


def _make_synthetic_recipe_upstream_capture() -> Recipe:
    """entry → check_repo_merge_state(captures ci_event, merge_group_trigger) →
    wait_for_ci(event=${{ context.ci_event }}).

    The producer of ci_event is upstream; event is a template reference, not a
    literal. No finding expected.
    """
    entry = RecipeStep(name="entry", action="route", on_success="check_repo_merge_state")
    check_step = RecipeStep(
        name="check_repo_merge_state",
        tool="check_repo_merge_state",
        capture={
            "ci_event": "${{ result.ci_event }}",
            "merge_group_trigger": "${{ result.merge_group_trigger }}",
        },
        on_success="ci_watch",
    )
    ci_watch = RecipeStep(
        name="ci_watch",
        tool="wait_for_ci",
        with_args={"branch": "main", "event": "${{ context.ci_event }}"},
    )
    return Recipe(
        name="synthetic-no-inversion",
        description="test recipe without capture inversion",
        steps={
            "entry": entry,
            "check_repo_merge_state": check_step,
            "ci_watch": ci_watch,
        },
    )


def _make_branching_recipe() -> Recipe:
    """entry (routes conditionally) → on_branch_a + on_branch_b → join.

    Branch A carries fact (X, 'a') via on_result.when = "context.X == 'a'".
    Branch B carries no fact (default route).
    At join: intersection gives empty fact set (X is not known on every path).
    """
    entry = RecipeStep(
        name="entry",
        action="route",
        on_result=StepResultRoute(
            conditions=[
                StepResultCondition(route="on_branch_a", when="context.X == 'a'"),
                StepResultCondition(route="on_branch_b"),  # default, no when
            ]
        ),
    )
    on_branch_a = RecipeStep(name="on_branch_a", action="route", on_success="join")
    on_branch_b = RecipeStep(name="on_branch_b", action="route", on_success="join")
    join = RecipeStep(name="join", action="stop", message="done")
    return Recipe(
        name="synthetic-branching",
        description="test recipe with branching for BFS fact intersection",
        steps={
            "entry": entry,
            "on_branch_a": on_branch_a,
            "on_branch_b": on_branch_b,
            "join": join,
        },
    )


# ---------------------------------------------------------------------------
# Dominance exoneration tests (R1)
# ---------------------------------------------------------------------------


def _make_dominating_producer_recipe() -> Recipe:
    """Recipe where producer B unconditionally captures var_x and dominates reader D.

    Layout:
      entry ─has_gate→ has_gate (skip_when_false) → producer → router_x → reader
                bypass: entry → router_x (skips producer when skip_when_false)

    The bypass edge from skip_when_false creates a path that does NOT go through
    the producer, causing the fact intersection to drop var_x from known_vars
    at the reader — but the producer dominates the reader on every success
    path, so the reader is safe.
    """
    entry = RecipeStep(name="entry", action="route", on_success="has_gate")
    has_gate = RecipeStep(
        name="has_gate",
        action="route",
        skip_when_false="some_gate",
        on_success="producer",
        on_result=StepResultRoute(
            conditions=[
                StepResultCondition(route="router_x", when="context.some_gate == 'true'"),
                StepResultCondition(route="router_x"),
            ]
        ),
    )
    producer = RecipeStep(
        name="producer",
        tool="run_cmd",
        with_args={"cmd": "echo capturing var_x"},
        capture={"var_x": "${{ result.output }}"},
        on_success="router_x",
    )
    router_x = RecipeStep(
        name="router_x",
        action="route",
        on_result=StepResultRoute(
            conditions=[
                StepResultCondition(route="consumer", when="context.var_x == 'yes'"),
                StepResultCondition(route="consumer"),
            ]
        ),
    )
    consumer = RecipeStep(
        name="consumer",
        tool="run_cmd",
        with_args={"cmd": "echo ${{ context.var_x }}"},
    )
    return Recipe(
        name="synthetic-dominating-producer",
        description="producer dominates reader via success-path barrier",
        steps={
            "entry": entry,
            "has_gate": has_gate,
            "producer": producer,
            "router_x": router_x,
            "consumer": consumer,
        },
    )


def test_dominating_producer_exonerates_reader():
    """R1: an unconditional producer that strictly dominates the reader exonerates it.

    The skip_when_false bypass creates a path that bypasses the producer in the
    pre-prune graph, dropping var_x from known_vars at the reader. The strict
    forward-path dominance check correctly exonerates the reader because the
    producer is on every success path.
    """
    recipe = _make_dominating_producer_recipe()
    ctx = make_validation_context(recipe)
    findings = _check_capture_inversion(ctx)
    consumer_findings = [f for f in findings if "consumer" in f.message]
    assert consumer_findings == [], (
        "Dominating producer should exonerate consumer, got: "
        + ", ".join(f.message for f in consumer_findings)
    )


def _make_non_dominating_producer_recipe() -> Recipe:
    """Recipe where producer is on only ONE branch of a fork (not a dominator).

    Layout:
      entry → fork → branch_producer (captures var_x) → join
                    → branch_other (no capture) → join
      join → reader (reads var_x)

    The producer is on one branch only — does NOT dominate the reader. Should
    produce a capture-inversion finding.
    """
    entry = RecipeStep(
        name="entry",
        action="route",
        on_result=StepResultRoute(
            conditions=[
                StepResultCondition(route="branch_producer", when="context.flag == 'yes'"),
                StepResultCondition(route="branch_other"),
            ]
        ),
    )
    branch_producer = RecipeStep(
        name="branch_producer",
        tool="run_cmd",
        with_args={"cmd": "echo producing"},
        capture={"var_x": "${{ result.output }}"},
        on_success="join",
    )
    branch_other = RecipeStep(
        name="branch_other",
        tool="run_cmd",
        with_args={"cmd": "echo nothing"},
        on_success="join",
    )
    router = RecipeStep(
        name="join",
        action="route",
        on_result=StepResultRoute(
            conditions=[
                StepResultCondition(route="reader", when="context.var_x == 'yes'"),
                StepResultCondition(route="reader"),
            ]
        ),
    )
    reader = RecipeStep(
        name="reader",
        tool="run_cmd",
        with_args={"cmd": "echo ${{ context.var_x }}"},
    )
    return Recipe(
        name="synthetic-non-dominating",
        description="producer on one branch only — not a dominator",
        steps={
            "entry": entry,
            "branch_producer": branch_producer,
            "branch_other": branch_other,
            "join": router,
            "reader": reader,
        },
    )


def test_non_dominating_producer_does_not_exonerate_reader():
    """Negative test: producer on one branch only should NOT exonerate reader.

    Distinguishes "on some path" (ancestor) from "on every path" (dominator).
    """
    recipe = _make_non_dominating_producer_recipe()
    ctx = make_validation_context(recipe)
    findings = _check_capture_inversion(ctx)
    reader_findings = [f for f in findings if "reader" in f.message]
    assert reader_findings, (
        "Non-dominating producer should flag reader with capture-inversion, "
        "but no findings were produced."
    )
