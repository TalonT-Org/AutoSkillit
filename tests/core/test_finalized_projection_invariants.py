"""Closure invariants for finalized recipe execution projections."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    RECIPE_TERMINAL_TARGETS,
    FinalizedRecipeProjection,
    FinalizedRecipeStep,
    RecipeBindingProjection,
    RecipeFlowEdge,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _edge(source: str, edge_type: str, target: str) -> RecipeFlowEdge:
    return RecipeFlowEdge(
        source=source,
        edge_type=edge_type,
        target=target,
        condition=None,
        result_field=None,
    )


def _projection(
    step_names: tuple[str, ...],
    edges: tuple[RecipeFlowEdge, ...],
) -> FinalizedRecipeProjection:
    return FinalizedRecipeProjection(
        binding_projection=RecipeBindingProjection(invocations={}),
        ordered_step_names=step_names,
        entrypoint=step_names[0],
        ordered_steps=tuple(FinalizedRecipeStep(name=name) for name in step_names),
        ingredient_names=frozenset(),
        ordered_flow_edges=edges,
    )


def test_projection_rejects_flow_edge_target_outside_steps_and_terminals() -> None:
    with pytest.raises(ValueError, match="flow-edge targets"):
        _projection(("start",), (_edge("start", "success", "missing"),))


def test_projection_rejects_step_unreachable_from_entrypoint() -> None:
    with pytest.raises(ValueError, match="entrypoint-reachable"):
        _projection(("start", "orphan"), ())


def test_projection_accepts_terminal_target_edge() -> None:
    projection = _projection(
        ("start",),
        (_edge("start", "success", next(iter(RECIPE_TERMINAL_TARGETS))),),
    )

    assert projection.ordered_step_names == ("start",)


def test_projection_accepts_deferred_skip_edge_as_only_path_to_step() -> None:
    projection = _projection(
        ("start", "guarded", "skip_only"),
        (
            _edge("start", "success", "guarded"),
            _edge("guarded", "skip", "skip_only"),
        ),
    )

    assert projection.ordered_step_names == ("start", "guarded", "skip_only")
