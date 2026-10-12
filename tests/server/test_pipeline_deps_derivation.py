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
    tool_by_step: dict[str, str] | None = None,
):
    tool_by_step = tool_by_step or {}
    return _make_finalized_projection(
        steps=tuple(
            FinalizedRecipeStep(name=name, tool=tool_by_step.get(name, "run_skill"))
            for name in step_names
        ),
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

    def test_run_python_predecessor_does_not_create_dependency(self):
        projection = _projection(
            "make_plan",
            "dry_walkthrough",
            edges=(
                RecipeFlowEdge(
                    source="make_plan",
                    edge_type="success",
                    target="dry_walkthrough",
                    condition=None,
                    result_field=None,
                ),
            ),
            tool_by_step={"make_plan": "run_python"},
        )

        assert _derive_phase_a_deps(projection) == {}

    def test_non_skill_dependent_does_not_create_dependency(self):
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
            tool_by_step={"review_approach": "run_cmd"},
        )

        assert _derive_phase_a_deps(projection) == {}

    def test_ambiguous_predecessors_are_counted_before_tool_filter(self):
        projection = _projection(
            "rectify",
            "make_plan",
            "dry_walkthrough",
            edges=(
                RecipeFlowEdge(
                    source="rectify",
                    edge_type="success",
                    target="dry_walkthrough",
                    condition=None,
                    result_field=None,
                ),
                RecipeFlowEdge(
                    source="rectify",
                    edge_type="success",
                    target="make_plan",
                    condition=None,
                    result_field=None,
                ),
                RecipeFlowEdge(
                    source="make_plan",
                    edge_type="success",
                    target="dry_walkthrough",
                    condition=None,
                    result_field=None,
                ),
            ),
            tool_by_step={"make_plan": "run_python"},
        )

        assert _derive_phase_a_deps(projection) == {}

    def test_tracker_dependencies_unions_requested_and_derived_without_mutation(self, monkeypatch):
        from autoskillit.server.tools import _pipeline_deps
        from autoskillit.server.tools._pipeline_deps import _tracker_dependencies

        projection = _projection(
            "rectify",
            "make_plan",
            "review_approach",
            edges=(
                RecipeFlowEdge(
                    source="rectify",
                    edge_type="success",
                    target="review_approach",
                    condition=None,
                    result_field=None,
                ),
                RecipeFlowEdge(
                    source="review_approach",
                    edge_type="success",
                    target="make_plan",
                    condition=None,
                    result_field=None,
                ),
            ),
        )
        derived = _derive_phase_a_deps(projection)
        assert _tracker_dependencies(projection, None) == derived

        derived_by_tracker = {"review_approach": ["rectify"]}
        monkeypatch.setattr(
            _pipeline_deps,
            "_derive_phase_a_deps",
            lambda _projection: derived_by_tracker,
        )
        requested = {
            "review_approach": ["make_plan", "rectify"],
            "custom": ["verify"],
        }

        merged = _tracker_dependencies(projection, requested)

        assert set(merged["review_approach"]) == {"make_plan", "rectify"}
        assert len(merged["review_approach"]) == 2
        assert merged["custom"] == ["verify"]
        assert derived_by_tracker == {"review_approach": ["rectify"]}

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
