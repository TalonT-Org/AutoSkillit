"""Tests for route-gate shared-stop semantic rule."""

from __future__ import annotations

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import RecipeKind
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestRouteGateSharedStop:
    """Tests for the route-gate-shared-stop semantic rule."""

    def test_shared_stop_fires_warning(self) -> None:
        """Route gate whose fallback and primary both reach same stop step produces a WARNING."""
        recipe = _make_workflow(
            {
                "start": {"action": "stop", "message": "done"},
                "route_gate": {
                    "action": "route",
                    "on_result": [
                        {"when": "${{ context.x }}", "route": "start"},
                        {"route": "start"},
                    ],
                },
            }
        )
        findings = run_semantic_rules(recipe)
        shared = [f for f in findings if f.rule == "route-gate-shared-stop"]
        assert len(shared) == 1
        assert shared[0].severity == Severity.WARNING
        assert shared[0].step_name == "route_gate"
        assert "same" in shared[0].message.lower()

    def test_disjoint_stops_no_finding(self) -> None:
        """Route gate with distinct stops for fallback and primary produces no finding."""
        recipe = _make_workflow(
            {
                "skipped": {"action": "stop", "message": "skipped outcome"},
                "success": {"action": "stop", "message": "success outcome"},
                "route_gate": {
                    "action": "route",
                    "on_result": [
                        {"when": "${{ context.x }}", "route": "success"},
                        {"route": "skipped"},
                    ],
                },
            }
        )
        findings = run_semantic_rules(recipe)
        shared = [f for f in findings if f.rule == "route-gate-shared-stop"]
        assert shared == []

    def test_shared_escalation_stop_excluded(self) -> None:
        """Shared escalation stop steps are excluded from findings."""
        recipe = _make_workflow(
            {
                "escalate_stop": {"action": "stop", "message": "escalate"},
                "route_gate": {
                    "action": "route",
                    "on_result": [
                        {"when": "${{ context.x }}", "route": "escalate_stop"},
                        {"route": "escalate_stop"},
                    ],
                },
            }
        )
        findings = run_semantic_rules(recipe)
        shared = [f for f in findings if f.rule == "route-gate-shared-stop"]
        assert shared == []

    def test_campaign_recipes_skipped(self) -> None:
        """Rule does not apply to campaign recipes."""
        recipe = _make_workflow(
            {
                "done": {"action": "stop", "message": "done"},
                "route_gate": {
                    "action": "route",
                    "on_result": [
                        {"when": "${{ context.x }}", "route": "done"},
                        {"route": "done"},
                    ],
                },
            }
        )
        # Spoof as campaign
        recipe.kind = RecipeKind.CAMPAIGN
        findings = run_semantic_rules(recipe)
        shared = [f for f in findings if f.rule == "route-gate-shared-stop"]
        assert shared == []

    def test_route_without_fallback_no_finding(self) -> None:
        """Route step with only when-conditions (no fallback) produces no finding."""
        recipe = _make_workflow(
            {
                "target_a": {"action": "stop", "message": "target a"},
                "target_b": {"action": "stop", "message": "target b"},
                "route_gate": {
                    "action": "route",
                    "on_result": [
                        {"when": "${{ context.x == 'a' }}", "route": "target_a"},
                        {"when": "${{ context.x == 'b' }}", "route": "target_b"},
                    ],
                },
            }
        )
        findings = run_semantic_rules(recipe)
        shared = [f for f in findings if f.rule == "route-gate-shared-stop"]
        assert shared == []

    def test_multiple_route_steps_partial_sharing(self) -> None:
        """Two route steps where only one has shared stops — only the violating one fires."""
        recipe = _make_workflow(
            {
                "done": {"action": "stop", "message": "done"},
                "skipped": {"action": "stop", "message": "skipped"},
                "route_gate_shared": {
                    "action": "route",
                    "on_result": [
                        {"when": "${{ context.x }}", "route": "done"},
                        {"route": "done"},
                    ],
                },
                "route_gate_clean": {
                    "action": "route",
                    "on_result": [
                        {"when": "${{ context.x }}", "route": "done"},
                        {"route": "skipped"},
                    ],
                },
            }
        )
        findings = run_semantic_rules(recipe)
        shared = [f for f in findings if f.rule == "route-gate-shared-stop"]
        assert len(shared) == 1
        assert shared[0].step_name == "route_gate_shared"

    def test_bundled_research_archive_no_shared_stop(self) -> None:
        """research-archive.yaml should have no shared stops after the recipe fix."""
        recipe = load_recipe(builtin_recipes_dir() / "research-archive.yaml")
        findings = run_semantic_rules(recipe)
        shared = [f for f in findings if f.rule == "route-gate-shared-stop"]
        assert shared == [], (
            f"Unexpected shared-stop findings in research-archive.yaml: "
            f"{[(f.step_name, f.message) for f in shared]}"
        )

    def test_bundled_research_yaml_no_shared_stop(self) -> None:
        """research.yaml should have no shared stops after the recipe fix."""
        recipe = load_recipe(builtin_recipes_dir() / "research.yaml")
        findings = run_semantic_rules(recipe)
        shared = [f for f in findings if f.rule == "route-gate-shared-stop"]
        assert shared == [], (
            f"Unexpected shared-stop findings in research.yaml: "
            f"{[(f.step_name, f.message) for f in shared]}"
        )
