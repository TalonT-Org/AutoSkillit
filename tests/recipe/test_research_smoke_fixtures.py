"""Smoke-test fixtures for the research recipe: classification and structural validation.

Validates classification pipeline behavior using two canonical fixture plans:
- FIXTURE_TRIVIAL_PLAN: corpus-search question with no methodology tradition keywords
- FIXTURE_RCT_PLAN: RCT-shaped plan with CONSORT keywords triggering controlled_intervention
"""

from __future__ import annotations

import re

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.methodology_tradition_registry import load_all_methodology_traditions
from autoskillit.recipe.methodology_tradition_router import classify_methodology

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


FIXTURE_TRIVIAL_PLAN = (
    "Does the phrase 'machine learning' appear more frequently than "
    "'deep learning' in recent conference proceedings from 2020 to 2024? "
    "Count occurrences in title and abstract fields from NeurIPS, ICML, "
    "and ICLR proceedings. Report frequency trends by year."
)

FIXTURE_RCT_PLAN = (
    "In this hypothetical randomized controlled trial, 100 participants "
    "were randomly assigned to treatment or placebo groups. The primary "
    "endpoint is symptom score change at 6 weeks. Analysis follows "
    "intent-to-treat principles with CONSORT reporting guidelines. "
    "Confounders are addressed via baseline stratification. Random "
    "allocation uses computer-generated sequences with allocation "
    "concealment via sealed envelopes."
)


@pytest.fixture(scope="module")
def research_recipe():
    return load_recipe(builtin_recipes_dir() / "research.yaml")


# ---------------------------------------------------------------------------
# Test Group A: Classification Validation (T_RSF_1 – T_RSF_10)
# ---------------------------------------------------------------------------


def test_trivial_fixture_no_methodology_tradition():
    result = classify_methodology(FIXTURE_TRIVIAL_PLAN)
    assert result.primary_tradition is None


def test_trivial_fixture_precedence_fallback():
    result = classify_methodology(FIXTURE_TRIVIAL_PLAN)
    assert result.precedence_trace == "stage1_no_match_fallback"


def test_trivial_fixture_empty_candidate_set():
    result = classify_methodology(FIXTURE_TRIVIAL_PLAN)
    assert result.candidate_set == ()


def test_rct_fixture_controlled_intervention_tradition():
    result = classify_methodology(FIXTURE_RCT_PLAN)
    assert result.primary_tradition == "controlled_intervention"


def test_rct_fixture_single_match_precedence():
    result = classify_methodology(FIXTURE_RCT_PLAN)
    assert result.precedence_trace == "stage1_single_match"


def test_rct_fixture_single_candidate():
    result = classify_methodology(FIXTURE_RCT_PLAN)
    assert len(result.candidate_set) == 1


def test_rct_fixture_consort_keywords_present():
    traditions = load_all_methodology_traditions()
    ci_spec = next(s for s in traditions if s.name == "controlled_intervention")

    def _kw_pattern(keyword: str) -> re.Pattern[str]:
        return re.compile(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)", re.IGNORECASE)

    matched = sum(
        1 for kw in ci_spec.detection_keywords if _kw_pattern(kw.lower()).search(FIXTURE_RCT_PLAN)
    )
    assert matched >= 3


def test_both_fixtures_nonempty_and_distinct():
    assert FIXTURE_TRIVIAL_PLAN
    assert FIXTURE_RCT_PLAN
    assert FIXTURE_TRIVIAL_PLAN != FIXTURE_RCT_PLAN


# ---------------------------------------------------------------------------
# Test Group B: Recipe Structural Gaps (T_RSF_11 – T_RSF_15)
# ---------------------------------------------------------------------------


def test_dial_captures_methodology_tradition(research_recipe):
    assert "methodology_tradition" in research_recipe.steps["vis_dial"].capture


def test_generate_report_receives_experiment_type_arg(research_recipe):
    skill_cmd = research_recipe.steps["generate_report"].with_args.get("skill_command", "")
    assert "--experiment-type" in skill_cmd


def test_generate_report_receives_methodology_traditions_arg(research_recipe):
    skill_cmd = research_recipe.steps["generate_report"].with_args.get("skill_command", "")
    assert "--methodology-traditions" in skill_cmd


def test_generate_report_inconclusive_receives_same_args(research_recipe):
    skill_cmd = research_recipe.steps["generate_report_inconclusive"].with_args.get(
        "skill_command", ""
    )
    assert "--experiment-type" in skill_cmd
    assert "--methodology-traditions" in skill_cmd


def test_re_generate_report_receives_same_args(research_recipe):
    skill_cmd = research_recipe.steps["re_generate_report"].with_args.get("skill_command", "")
    assert "--experiment-type" in skill_cmd
    assert "--methodology-traditions" in skill_cmd
