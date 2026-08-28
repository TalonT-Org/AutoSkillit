"""Projection and analysis-graph coverage for server-authoritative step guards."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    FinalizedRecipeProjection,
    FinalizedRecipeStep,
    RecipeBindingProjection,
    RecipeStepGuard,
)
from autoskillit.recipe._analysis import build_recipe_graph
from autoskillit.recipe._analysis_graph import _build_raw_step_edges
from autoskillit.recipe._api import load_and_validate
from autoskillit.recipe.schema import Recipe, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _projection(*, guards: tuple[RecipeStepGuard, ...] = ()) -> FinalizedRecipeProjection:
    return FinalizedRecipeProjection(
        binding_projection=RecipeBindingProjection({}),
        ordered_step_names=("apply", "synthesize"),
        entrypoint="apply",
        ordered_flow_edges=(),
        ordered_steps=tuple(FinalizedRecipeStep(name=name) for name in ("apply", "synthesize")),
        ingredient_names=frozenset(),
        ordered_step_guards=guards,
    )


def test_composed_guard_is_published_through_the_public_projection_type() -> None:
    result = load_and_validate("research-design", include_finalized_projection=True)
    projection = result["_finalized_projection"]

    assert isinstance(projection, FinalizedRecipeProjection)
    assert projection.ordered_step_guards == (
        RecipeStepGuard("apply", "is_silent_type", "synthesize"),
    )


@pytest.mark.parametrize(
    "guard",
    [
        RecipeStepGuard("missing", "is_silent_type", "synthesize"),
        RecipeStepGuard("apply", "is_silent_type", "missing"),
    ],
)
def test_projection_rejects_guards_outside_finalized_steps(guard: RecipeStepGuard) -> None:
    with pytest.raises(ValueError, match="guards|bypasses"):
        _projection(guards=(guard,))


def test_composed_guard_accepts_terminal_bypass(tmp_path) -> None:
    recipes = tmp_path / ".autoskillit" / "recipes"
    recipes.mkdir(parents=True)
    recipes.joinpath("terminal-guard.yaml").write_text(
        """\
name: terminal-guard
description: terminal guard projection
steps:
  probe:
    tool: run_cmd
    with:
      cmd: printf true
    capture:
      should_skip: ${{ result.stdout }}
    on_success: apply
  apply:
    tool: run_skill
    with:
      skill_command: /autoskillit:investigate
    skip_when_true: context.should_skip
    on_success: done
"""
    )

    result = load_and_validate(
        "terminal-guard", project_dir=tmp_path, include_finalized_projection=True
    )

    assert result["_finalized_projection"].ordered_step_guards == (
        RecipeStepGuard("apply", "should_skip", "done"),
    )


def test_unguarded_composition_publishes_no_step_guards(tmp_path) -> None:
    recipes = tmp_path / ".autoskillit" / "recipes"
    recipes.mkdir(parents=True)
    recipes.joinpath("unguarded.yaml").write_text(
        """\
name: unguarded
description: unguarded projection
steps:
  done:
    action: stop
    message: done
"""
    )

    result = load_and_validate(
        "unguarded", project_dir=tmp_path, include_finalized_projection=True
    )
    assert result["_finalized_projection"].ordered_step_guards == ()


def test_analysis_graph_exposes_guard_and_runtime_bypass_edge() -> None:
    recipe = Recipe(
        name="guarded",
        description="guarded graph",
        steps={
            "apply": RecipeStep(
                tool="run_skill",
                skip_when_true="context.is_silent_type",
                on_success="synthesize",
            ),
            "synthesize": RecipeStep(action="stop", message="done"),
        },
    )

    graph = build_recipe_graph(recipe)
    bypasses = [
        edge
        for edge in _build_raw_step_edges(recipe)["apply"]
        if edge.edge_type == "runtime_skip_bypass"
    ]

    apply_node = next(node for node, attrs in graph.nodes(data=True) if attrs["name"] == "apply")
    assert graph.nodes[apply_node]["skip_when_true"] == "context.is_silent_type"
    assert [(edge.edge_type, edge.target) for edge in bypasses] == [
        ("runtime_skip_bypass", "synthesize"),
    ]
