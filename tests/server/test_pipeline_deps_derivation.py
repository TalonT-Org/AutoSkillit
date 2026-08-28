"""Tests for _derive_phase_a_deps curated dependency derivation."""

from __future__ import annotations

import pytest

from autoskillit.core import FinalizedRecipeStep, RecipeFlowEdge
from autoskillit.server.tools._pipeline_deps import _derive_phase_a_deps
from tests.server._helpers import _make_finalized_projection

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _projection(
    *step_names: str,
    edges: tuple[RecipeFlowEdge, ...] = (),
):
    return _make_finalized_projection(
        steps=tuple(FinalizedRecipeStep(name=name) for name in step_names),
        edges=edges,
    )


class TestDerivePhaseADeps:
    def test_review_approach_depends_on_rectify(self):
        projection = _projection(
            "rectify",
            "review_approach",
            edges=(
                RecipeFlowEdge(
                    source="rectify",
                    edge_type="success",
                    target="review_approach",
                    condition=None,
                    result_field=None,
                ),
            ),
        )
        deps = _derive_phase_a_deps(projection)
        assert deps == {"review_approach": ["rectify"]}

    def test_review_approach_depends_on_make_plan(self):
        projection = _projection(
            "make_plan",
            "review_approach",
            edges=(
                RecipeFlowEdge(
                    source="make_plan",
                    edge_type="success",
                    target="review_approach",
                    condition=None,
                    result_field=None,
                ),
            ),
        )
        deps = _derive_phase_a_deps(projection)
        assert deps == {"review_approach": ["make_plan"]}

    def test_no_curated_targets_present_returns_empty(self):
        projection = _projection(
            "build",
            "test",
            edges=(
                RecipeFlowEdge(
                    source="build",
                    edge_type="success",
                    target="test",
                    condition=None,
                    result_field=None,
                ),
            ),
        )
        assert _derive_phase_a_deps(projection) == {}

    def test_curated_target_with_no_matching_predecessor_omitted(self):
        projection = _projection(
            "unrelated",
            "review_approach",
            edges=(
                RecipeFlowEdge(
                    source="unrelated",
                    edge_type="success",
                    target="review_approach",
                    condition=None,
                    result_field=None,
                ),
            ),
        )
        assert _derive_phase_a_deps(projection) == {}

    def test_cycle_member_skipped(self):
        # dry_walkthrough participates in a loop via next_or_done -> dry_walkthrough.
        projection = _projection(
            "rectify",
            "dry_walkthrough",
            "next_or_done",
            edges=(
                RecipeFlowEdge(
                    source="rectify",
                    edge_type="success",
                    target="dry_walkthrough",
                    condition=None,
                    result_field=None,
                ),
                RecipeFlowEdge(
                    source="dry_walkthrough",
                    edge_type="success",
                    target="next_or_done",
                    condition=None,
                    result_field=None,
                ),
                RecipeFlowEdge(
                    source="next_or_done",
                    edge_type="success",
                    target="dry_walkthrough",
                    condition=None,
                    result_field=None,
                ),
            ),
        )
        deps = _derive_phase_a_deps(projection)
        assert "dry_walkthrough" not in deps

    def test_empty_active_steps_returns_empty(self):
        assert _derive_phase_a_deps(None) == {}
