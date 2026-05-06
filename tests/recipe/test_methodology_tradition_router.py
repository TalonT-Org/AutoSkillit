"""Tests for methodology_tradition_router two-stage Tier-C router."""

from __future__ import annotations

import pytest

from autoskillit.recipe.methodology_tradition_registry import (
    load_all_methodology_traditions,
)
from autoskillit.recipe.methodology_tradition_router import (
    UnionRuleDef,
    classify_methodology,
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


TRADITION_FIXTURES: list[tuple[str, str]] = [
    (
        "controlled_intervention",
        "This randomized controlled trial follows CONSORT guidelines with intent-to-treat "
        "analysis across parallel group arms.",
    ),
    (
        "systematic_synthesis",
        "The systematic review employed PRISMA standards and performed meta-analysis with "
        "forest plots to report pooled estimates across studies.",
    ),
    (
        "observational_correlational",
        "An observational cohort study was conducted using STROBE reporting guidelines to "
        "examine the association between exposure and outcome.",
    ),
    (
        "diagnostic_accuracy",
        "The diagnostic accuracy study evaluated sensitivity and specificity using ROC curve "
        "analysis following STARD recommendations.",
    ),
    (
        "prediction_model_validation",
        "External validation of the clinical prediction rule was performed using TRIPOD guidelines, "
        "assessing calibration and discrimination via c-statistic.",
    ),
    (
        "simulation_modeling_tradition",
        "An agent-based model was developed following the ODD protocol, implemented in NetLogo "
        "to simulate emergent behavior across parameter sweeps.",
    ),
    (
        "measurement_instrument_validation_tradition",
        "Measurement properties were assessed using COSMIN criteria, evaluating internal consistency "
        "via Cronbach alpha and test-retest reliability.",
    ),
    (
        "quality_improvement",
        "The quality improvement initiative followed SQUIRE guidelines, employing PDSA cycles "
        "to drive process improvement in clinical pathways.",
    ),
    (
        "economic_evaluation",
        "Cost-effectiveness analysis was conducted following CHEERS guidelines, calculating "
        "ICER and QALY outcomes using Markov modeling.",
    ),
    (
        "animal_preclinical",
        "The animal study was designed per ARRIVE guidelines, with randomization of animals "
        "and blinding of assessors to evaluate the intervention.",
    ),
    (
        "qualitative_interpretive_tradition",
        "Qualitative research was conducted following COREQ reporting standards, using "
        "thematic analysis of semi-structured interview transcripts.",
    ),
    (
        "method_comparison_benchmarking",
        "The method comparison study established a SOTA baseline using leaderboard evaluation "
        "and ablation study to benchmark performance against prior work.",
    ),
]


@pytest.mark.parametrize("tradition_slug,plan_snippet", TRADITION_FIXTURES)
def test_single_tradition_detected(tradition_slug, plan_snippet):
    result = classify_methodology(plan_snippet)
    assert result.primary_tradition == tradition_slug
    assert result.precedence_trace == "stage1_single_match"
    assert tradition_slug in result.candidate_set
    assert len(result.candidate_set) == 1
    assert result.applied_union_rules == []


def test_no_tradition_detected():
    result = classify_methodology("This paper describes a novel deep learning architecture.")
    assert result.primary_tradition is None
    assert result.precedence_trace == "stage1_no_match_fallback"
    assert result.candidate_set == []
    assert result.applied_union_rules == []


def test_multi_match_returns_candidates():
    plan_text = (
        "This systematic review and meta-analysis includes randomized controlled trials "
        "following PRISMA and CONSORT guidelines with forest plots."
    )
    result = classify_methodology(plan_text)
    assert result.primary_tradition is None
    assert result.precedence_trace == "stage1_multi_match"
    assert len(result.candidate_set) >= 2
    assert "controlled_intervention" in result.candidate_set
    assert "systematic_synthesis" in result.candidate_set


def test_multi_match_resolved_by_priority():
    plan_text = (
        "This systematic review and meta-analysis includes randomized controlled trials "
        "following PRISMA and CONSORT guidelines with forest plots."
    )
    result = classify_methodology(plan_text, resolve_by_priority=True)
    assert result.primary_tradition == "controlled_intervention"
    assert result.precedence_trace == "stage1_multi_match_resolved_by_priority"
    assert len(result.candidate_set) >= 2


def test_union_rule_applied():
    rule = UnionRuleDef(
        name="rct_with_synthesis",
        member_traditions=frozenset({"controlled_intervention", "systematic_synthesis"}),
        resolved_tradition="systematic_synthesis",
    )
    plan_text = (
        "This systematic review and meta-analysis includes randomized controlled trials "
        "following PRISMA and CONSORT guidelines."
    )
    result = classify_methodology(plan_text, union_rules=[rule])
    assert result.primary_tradition == "systematic_synthesis"
    assert "rct_with_synthesis" in result.applied_union_rules
    assert "stage2_tiebreak_by_rule_rct_with_synthesis" == result.precedence_trace


def test_union_rule_not_applied_when_no_member_match():
    rule = UnionRuleDef(
        name="unrelated_rule",
        member_traditions=frozenset({"economic_evaluation", "quality_improvement"}),
        resolved_tradition="economic_evaluation",
    )
    plan_text = (
        "This randomized controlled trial follows CONSORT guidelines "
        "with intent-to-treat analysis."
    )
    result = classify_methodology(plan_text, union_rules=[rule])
    assert result.primary_tradition == "controlled_intervention"
    assert result.precedence_trace == "stage1_single_match"
    assert result.applied_union_rules == []


def test_deterministic_classification():
    plan_text = (
        "This randomized controlled trial follows CONSORT guidelines "
        "with intent-to-treat analysis across parallel group arms."
    )
    results = [classify_methodology(plan_text) for _ in range(10)]
    first = results[0]
    for r in results[1:]:
        assert r.primary_tradition == first.primary_tradition
        assert r.precedence_trace == first.precedence_trace
        assert r.candidate_set == first.candidate_set


def test_candidate_set_sorted_by_priority():
    plan_text = (
        "This systematic review and meta-analysis includes randomized controlled trials "
        "following PRISMA and CONSORT guidelines."
    )
    result = classify_methodology(plan_text)
    traditions = load_all_methodology_traditions()
    priority_map = {s.name: s.priority for s in traditions}
    priorities = [priority_map[c] for c in result.candidate_set]
    assert priorities == sorted(priorities)


def test_high_threshold_eliminates_weak_matches():
    plan_text = "The study used an RCT design with novel computational methods."
    result_threshold_1 = classify_methodology(plan_text, min_keyword_matches=1)
    result_threshold_3 = classify_methodology(plan_text, min_keyword_matches=3)
    assert "controlled_intervention" in result_threshold_1.candidate_set
    assert "controlled_intervention" not in result_threshold_3.candidate_set


def test_case_insensitive_matching():
    plan_text = "This RANDOMIZED CONTROLLED TRIAL follows consort guidelines with INTENT-TO-TREAT."
    result = classify_methodology(plan_text)
    assert result.primary_tradition == "controlled_intervention"


def test_result_is_frozen():
    result = classify_methodology("This randomized controlled trial uses CONSORT and ITT.")
    with pytest.raises(AttributeError):
        result.primary_tradition = "something_else"
