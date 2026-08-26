"""Tests for SKILL.md to skill_contracts.yaml completeness (planner-refine focus)."""

from __future__ import annotations

import pytest

from autoskillit.recipe.contracts import load_bundled_manifest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestPlannerRefineContract:
    """planner-refine-specific contract completeness tests for Part A."""

    def test_planner_refine_outputs_in_contract(self) -> None:
        """planner-refine contract must declare issues_fixed and refinement_complete."""
        manifest = load_bundled_manifest()
        contract = manifest.get("skills", {}).get("planner-refine")
        assert contract is not None, "planner-refine not in bundled manifest"
        output_names = {o["name"] for o in contract.get("outputs", [])}
        assert "issues_fixed" in output_names, (
            f"planner-refine contract missing 'issues_fixed'. Found: {output_names}"
        )
        assert "refinement_complete" in output_names, (
            f"planner-refine contract missing 'refinement_complete'. Found: {output_names}"
        )
