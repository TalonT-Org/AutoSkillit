"""Tests for methodology disambiguation."""

from __future__ import annotations

import dataclasses

import pytest

from autoskillit.recipe.methodology_disambiguation import (
    disambiguate,
    load_disambiguation_rules,
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestRuleTests:
    def test_rule_prisma_dominance(self) -> None:
        result = disambiguate({"systematic_synthesis", "controlled_intervention"})
        assert result.primary_tradition == "systematic_synthesis"
        assert "rule_prisma_dominance" in result.precedence_trace

    def test_rule_prisma_dominance_tripod_srma_exception(self) -> None:
        result = disambiguate({"systematic_synthesis", "prediction_model_validation"})
        assert result.primary_tradition == "systematic_synthesis"
        assert "TRIPOD_SRMA" in result.applied_union_rules
        assert "exception_prediction_model_validation" in result.precedence_trace

    def test_rule_rct_economic_union(self) -> None:
        result = disambiguate({"controlled_intervention", "economic_evaluation"})
        assert result.primary_tradition == "controlled_intervention"
        assert "CHEERS_union" in result.applied_union_rules
        assert "rule_rct_economic_union" in result.precedence_trace

    def test_rule_arrive_supersedes_consort(self) -> None:
        result = disambiguate({"animal_preclinical", "controlled_intervention"})
        assert result.primary_tradition == "animal_preclinical"
        assert result.applied_union_rules == ()
        assert "rule_arrive_supersedes_consort" in result.precedence_trace

    def test_rule_benchmarking_prediction_nested(self) -> None:
        result = disambiguate({"method_comparison_benchmarking", "prediction_model_validation"})
        assert result.primary_tradition == "method_comparison_benchmarking"
        assert "TRIPOD_nested" in result.applied_union_rules
        assert "rule_benchmarking_prediction_nested" in result.precedence_trace


class TestOverlapTests:
    def test_overlap_tripod_consort_union(self) -> None:
        result = disambiguate({"prediction_model_validation", "controlled_intervention"})
        assert result.primary_tradition == "controlled_intervention"
        assert "TRIPOD_union" in result.applied_union_rules
        assert "overlap_tripod_consort_union" in result.precedence_trace

    def test_overlap_strobe_prisma_moose(self) -> None:
        result = disambiguate({"observational_correlational", "systematic_synthesis"})
        assert result.primary_tradition == "systematic_synthesis"
        assert "MOOSE_override" in result.applied_union_rules
        assert "rule_prisma_dominance" in result.precedence_trace
        assert "overlap_strobe_prisma_moose" in result.precedence_trace

    def test_overlap_odd_controlled_nesting(self) -> None:
        result = disambiguate({"simulation_modeling_tradition", "controlled_intervention"})
        assert result.primary_tradition == "simulation_modeling_tradition"
        assert "controlled_intervention_secondary" in result.applied_union_rules
        assert "overlap_odd_controlled_nesting" in result.precedence_trace

    def test_overlap_benchmarking_prisma_separation(self) -> None:
        result = disambiguate({"method_comparison_benchmarking", "systematic_synthesis"})
        assert result.primary_tradition == "method_comparison_benchmarking"
        assert "PRISMA_curation_phase" in result.applied_union_rules
        assert "rule_prisma_dominance" in result.precedence_trace
        assert "overlap_benchmarking_prisma_separation" in result.precedence_trace

    def test_overlap_srqr_consort_parallel(self) -> None:
        result = disambiguate({"qualitative_interpretive_tradition", "controlled_intervention"})
        assert result.primary_tradition == "controlled_intervention"
        assert "SRQR_parallel" in result.applied_union_rules
        assert "overlap_srqr_consort_parallel" in result.precedence_trace


class TestStructuralBehavioralTests:
    def test_fallthrough_uses_highest_priority(self) -> None:
        result = disambiguate({"diagnostic_accuracy", "quality_improvement"})
        assert result.primary_tradition == "diagnostic_accuracy"
        assert "fallthrough_priority" in result.precedence_trace

    def test_result_is_frozen(self) -> None:
        result = disambiguate({"controlled_intervention", "economic_evaluation"})
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.primary_tradition = "something_else"

    def test_single_candidate_returns_that_candidate(self) -> None:
        result = disambiguate({"controlled_intervention"})
        assert result.primary_tradition == "controlled_intervention"
        assert result.precedence_trace == "single_candidate"
        assert result.applied_union_rules == ()

    def test_empty_candidate_set(self) -> None:
        result = disambiguate(set())
        assert result.primary_tradition is None
        assert result.precedence_trace == "no_candidates"
        assert result.candidate_set == ()

    def test_load_disambiguation_rules_bundled(self) -> None:
        rules, overlaps = load_disambiguation_rules()
        rule_names = {r.name for r in rules}
        assert rule_names == {
            "prisma_dominance",
            "rct_economic_union",
            "arrive_supersedes_consort",
            "benchmarking_prediction_nested",
        }
        overlap_names = {o.name for o in overlaps}
        assert overlap_names == {
            "tripod_consort_union",
            "strobe_prisma_moose",
            "odd_controlled_nesting",
            "benchmarking_prisma_separation",
            "srqr_consort_parallel",
        }

    def test_disambiguation_deterministic(self) -> None:
        candidates = {"controlled_intervention", "economic_evaluation"}
        results = [disambiguate(candidates) for _ in range(10)]
        for r in results[1:]:
            assert r.primary_tradition == results[0].primary_tradition
            assert r.applied_union_rules == results[0].applied_union_rules
            assert r.precedence_trace == results[0].precedence_trace

    def test_rule_precedence_order(self) -> None:
        result = disambiguate(
            {"systematic_synthesis", "animal_preclinical", "controlled_intervention"}
        )
        assert result.primary_tradition == "systematic_synthesis"
        assert result.precedence_trace.startswith("rule_prisma_dominance")
