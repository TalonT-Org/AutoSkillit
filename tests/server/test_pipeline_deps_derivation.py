"""Tests for _derive_phase_a_deps curated dependency derivation."""

from __future__ import annotations

import pytest

from autoskillit.recipe.schema import RecipeStep
from autoskillit.server.tools._pipeline_deps import _derive_phase_a_deps

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestDerivePhaseADeps:
    def test_review_approach_depends_on_rectify(self):
        steps = {
            "rectify": RecipeStep(name="rectify", on_success="review_approach"),
            "review_approach": RecipeStep(name="review_approach", on_success="done"),
        }
        deps = _derive_phase_a_deps(steps)
        assert deps == {"review_approach": ["rectify"]}

    def test_review_approach_depends_on_make_plan(self):
        steps = {
            "make_plan": RecipeStep(name="make_plan", on_success="review_approach"),
            "review_approach": RecipeStep(name="review_approach", on_success="done"),
        }
        deps = _derive_phase_a_deps(steps)
        assert deps == {"review_approach": ["make_plan"]}

    def test_no_curated_targets_present_returns_empty(self):
        steps = {
            "build": RecipeStep(name="build", on_success="test"),
            "test": RecipeStep(name="test", on_success="done"),
        }
        assert _derive_phase_a_deps(steps) == {}

    def test_curated_target_with_no_matching_predecessor_omitted(self):
        steps = {
            "unrelated": RecipeStep(name="unrelated", on_success="review_approach"),
            "review_approach": RecipeStep(name="review_approach", on_success="done"),
        }
        assert _derive_phase_a_deps(steps) == {}

    def test_cycle_member_skipped(self):
        # dry_walkthrough participates in a loop via next_or_done -> dry_walkthrough.
        steps = {
            "rectify": RecipeStep(name="rectify", on_success="dry_walkthrough"),
            "dry_walkthrough": RecipeStep(name="dry_walkthrough", on_success="next_or_done"),
            "next_or_done": RecipeStep(name="next_or_done", on_success="dry_walkthrough"),
        }
        deps = _derive_phase_a_deps(steps)
        assert "dry_walkthrough" not in deps

    def test_empty_active_steps_returns_empty(self):
        assert _derive_phase_a_deps({}) == {}
