"""Skill-level integration tests for vis-lens-methodology-norms tradition expansion."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from autoskillit.core import RETIRED_SKILL_NAMES
from autoskillit.recipe.methodology_disambiguation import (
    disambiguate,
)
from autoskillit.recipe.methodology_tradition_registry import (
    MethodologyTraditionSpec,
    get_methodology_tradition_by_name,
    is_out_of_scope_tradition,
    load_all_methodology_traditions,
)
from autoskillit.recipe.methodology_tradition_router import (
    classify_methodology,
)
from autoskillit.recipe.methodology_venue_appendix import (
    resolve_venue_appendices,
)
from autoskillit.workspace.skills import DefaultSkillResolver

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]

EXPECTED_TRADITIONS: list[str] = [
    "controlled_intervention",
    "systematic_synthesis",
    "observational_correlational",
    "diagnostic_accuracy",
    "prediction_model_validation",
    "simulation_modeling_tradition",
    "measurement_instrument_validation_tradition",
    "quality_improvement",
    "economic_evaluation",
    "animal_preclinical",
    "qualitative_interpretive_tradition",
    "method_comparison_benchmarking",
]

TIER_C_PLAN_FIXTURES: list[tuple[str, str]] = [
    (
        "controlled_intervention",
        "This randomized controlled trial evaluated the efficacy of a novel "
        "intervention using CONSORT-compliant design. Participants were randomly "
        "allocated to treatment or placebo arms. Primary endpoints were assessed "
        "at 12-week follow-up using intention-to-treat analysis.",
    ),
    (
        "systematic_synthesis",
        "We conducted a systematic review following PRISMA 2020 guidelines. "
        "A comprehensive meta-analysis synthesized effect sizes across 42 eligible "
        "studies. Heterogeneity was assessed using the I-squared statistic and "
        "publication bias was evaluated via funnel plot asymmetry.",
    ),
    (
        "observational_correlational",
        "This prospective cohort study followed 5,000 participants over ten years. "
        "STROBE reporting guidelines were followed throughout. Hazard ratios were "
        "estimated using Cox proportional hazards models adjusting for confounders.",
    ),
    (
        "diagnostic_accuracy",
        "We evaluated the diagnostic accuracy of a novel biomarker panel using "
        "STARD-compliant methodology. Sensitivity and specificity were calculated "
        "against the histopathology reference standard. ROC analysis determined "
        "optimal cutoff thresholds.",
    ),
    (
        "prediction_model_validation",
        "This study developed and externally validated a clinical prediction model "
        "following TRIPOD guidelines. Model calibration was assessed using the "
        "Hosmer-Lemeshow test. Discrimination was quantified via the concordance "
        "statistic on the held-out validation cohort.",
    ),
    (
        "simulation_modeling_tradition",
        "We constructed an agent-based model to evaluate healthcare resource "
        "allocation under pandemic scenarios. Parameter sweep experiments revealed "
        "emergent behavior patterns at the population level. Individual-based model "
        "dynamics were validated against observational data.",
    ),
    (
        "measurement_instrument_validation_tradition",
        "This psychometric validation study followed COSMIN methodology to evaluate "
        "a patient-reported outcome measure. Test-retest reliability and internal "
        "consistency were assessed. Structural validity was established through "
        "confirmatory factor analysis.",
    ),
    (
        "quality_improvement",
        "This quality improvement initiative applied the SQUIRE 2.0 framework to "
        "reduce medication errors in an inpatient setting. Three iterative PDSA "
        "cycles refined the intervention. Statistical process control charts "
        "monitored performance over the implementation period.",
    ),
    (
        "economic_evaluation",
        "We conducted a cost-effectiveness analysis alongside the clinical trial "
        "following CHEERS 2022 reporting standards. Incremental cost-effectiveness "
        "ratios were computed from the healthcare payer perspective. Probabilistic "
        "sensitivity analysis explored parameter uncertainty.",
    ),
    (
        "animal_preclinical",
        "This preclinical study adhered to ARRIVE 2.0 reporting guidelines for "
        "animal research. Male Sprague-Dawley rats were randomized to treatment "
        "groups. Histological endpoints were scored by blinded observers using "
        "validated grading systems.",
    ),
    (
        "qualitative_interpretive_tradition",
        "We employed reflexive thematic analysis within a constructivist paradigm "
        "following COREQ reporting standards. Semi-structured interviews were "
        "conducted with 24 participants until thematic saturation. Credibility was "
        "enhanced through member checking and peer debriefing.",
    ),
    (
        "method_comparison_benchmarking",
        "This benchmark study compared five transformer architectures on standard "
        "NLP tasks using an ablation study design. Statistical significance was "
        "assessed via bootstrap confidence intervals and performance comparison "
        "across five random seeds.",
    ),
]

DISAMBIGUATION_PLAN_FIXTURES: list[tuple[str, str, str, tuple[str, ...]]] = [
    (
        "prisma_dominance",
        "This systematic review and meta-analysis followed PRISMA guidelines to "
        "synthesize evidence from randomized controlled trial and clinical trial "
        "designs. CONSORT reporting standards were applied. Forest plots summarized "
        "pooled effect estimates across included studies.",
        "systematic_synthesis",
        (),
    ),
    (
        "rct_economic_union",
        "This randomized controlled trial included a prospective economic evaluation. "
        "CONSORT reporting was followed for the clinical endpoints. Cost-effectiveness "
        "analysis adhered to CHEERS 2022 standards with an NHS perspective. "
        "Incremental cost per QALY was the primary economic outcome.",
        "controlled_intervention",
        ("CHEERS_union",),
    ),
    (
        "arrive_supersedes_consort",
        "This preclinical animal study used a randomized controlled trial design "
        "following ARRIVE 2.0 guidelines. Treatment and vehicle groups of C57BL/6 "
        "mice were compared. CONSORT reporting standards and clinical trial "
        "registration were applied. Behavioral endpoints were assessed by blinded raters.",
        "animal_preclinical",
        (),
    ),
    (
        "benchmarking_prediction_nested",
        "This benchmark study used an ablation study design to compare prediction "
        "models following TRIPOD guidelines. TRIPOD-adherent calibration and "
        "discrimination metrics were computed. External validation cohorts confirmed "
        "the c-statistic across the benchmark evaluation protocol.",
        "method_comparison_benchmarking",
        ("TRIPOD_nested",),
    ),
]

VENUE_PLAN_FIXTURES: list[tuple[str, str, str, bool]] = [
    (
        "foundation_models",
        "This study benchmarks foundation model performance across few-shot learning tasks. Standard NLP and vision benchmarks measure generalization. Ablation experiments isolate pretraining data effects.",
        "method_comparison_benchmarking",
        False,
    ),
    (
        "foundation_models",
        "We evaluate foundation model calibration on held-out validation sets. Few-shot scaling laws are analyzed across model sizes. Zero-shot validation performance is compared against fine-tuned baselines.",
        "prediction_model_validation",
        True,
    ),
    (
        "foundation_models",
        "This psychometric analysis evaluates latent trait measurement properties of foundation model outputs. Item response theory models assess construct validity. Cronbach alpha quantifies internal consistency of generated scales.",
        "measurement_instrument_validation_tradition",
        True,
    ),
    (
        "reinforcement_learning",
        "This benchmark compares deep reinforcement learning algorithms on Atari and MuJoCo environments. Ablation studies isolate architectural contributions. Statistical significance is assessed across 10 random seeds.",
        "method_comparison_benchmarking",
        False,
    ),
    (
        "reinforcement_learning",
        "We study multi-agent reinforcement learning with emergent behavior in population dynamics environments. Swarm intelligence metrics quantify collective coordination. Agent-based simulation validates scaling.",
        "simulation_modeling_tradition",
        True,
    ),
    (
        "reinforcement_learning",
        "This study evaluates reinforcement learning policy on held-out task distributions. Sample efficiency curves measure data requirements. Policy evaluation protocols follow systematic benchmarking methodology.",
        "prediction_model_validation",
        True,
    ),
    (
        "supervised_classification",
        "This study benchmarks supervised classification models on tabular and image datasets. Ablation experiments measure feature importance. Bootstrap confidence intervals assess statistical significance.",
        "method_comparison_benchmarking",
        False,
    ),
    (
        "supervised_classification",
        "We evaluate supervised classification for medical imaging diagnosis of radiology findings. Sensitivity and specificity are computed against pathology gold standard. ROC analysis determines optimal thresholds.",
        "diagnostic_accuracy",
        True,
    ),
    (
        "supervised_classification",
        "This supervised classification model predicts patient outcome trajectories. Prognosis endpoints include 30-day mortality and risk prediction scores. Clinical decision support integration is evaluated.",
        "prediction_model_validation",
        True,
    ),
    (
        "nlp",
        "This NLP benchmark compares transformer architectures on text classification and generation tasks. Ablation experiments measure attention head contribution. Results are averaged over five random seeds.",
        "method_comparison_benchmarking",
        False,
    ),
    (
        "nlp",
        "We develop a clinical NLP pipeline for automated risk scoring from patient notes. The clinical text extraction module predicts patient outcome categories. External validation uses held-out hospital data.",
        "prediction_model_validation",
        True,
    ),
    (
        "nlp",
        "This NLP study applies discourse analysis to conversational AI outputs. Narrative structure is examined through thematic coding of dialogue turns. Conversation analysis reveals pragmatic patterns in generated text.",
        "qualitative_interpretive_tradition",
        True,
    ),
    (
        "computer_vision",
        "This computer vision benchmark compares object detection architectures on COCO and ImageNet. Ablation experiments measure backbone contribution. mAP scores are reported across five training runs.",
        "method_comparison_benchmarking",
        False,
    ),
    (
        "computer_vision",
        "We evaluate a computer vision model for medical imaging classification in radiology screening. Sensitivity and specificity of pathology detection are calculated against expert consensus. STARD methodology is followed.",
        "diagnostic_accuracy",
        True,
    ),
    (
        "computer_vision",
        "This computer vision system predicts patient outcome from retinal imaging biomarkers. Clinical decision support thresholds are derived from ROC analysis. Prognosis models are validated on external cohorts.",
        "prediction_model_validation",
        True,
    ),
    (
        "generative_models",
        "This study benchmarks generative model architectures on image synthesis quality. FID and IS scores are compared across diffusion and GAN variants. Ablation experiments isolate conditioning mechanism effects.",
        "method_comparison_benchmarking",
        False,
    ),
    (
        "generative_models",
        "We develop a human evaluation instrument for generative model output quality. Inter-rater reliability is assessed via ICC. Construct validity is established through scale development methodology.",
        "measurement_instrument_validation_tradition",
        True,
    ),
    (
        "generative_models",
        "This study applies qualitative assessment of generative model outputs through expert rating sessions. Human evaluation uses content analysis protocols with thematic coding. Evaluator agreement is reported.",
        "qualitative_interpretive_tradition",
        True,
    ),
    (
        "agentic_systems",
        "This benchmark compares agentic system architectures on task completion metrics. Ablation experiments measure planning module contribution. Results are averaged across 20 evaluation episodes.",
        "method_comparison_benchmarking",
        False,
    ),
    (
        "agentic_systems",
        "We study multi-agent coordination through agent-based model simulation. Emergent behavior patterns are analyzed in swarm intelligence scenarios. Population-level dynamics emerge from individual agent rules.",
        "simulation_modeling_tradition",
        True,
    ),
    (
        "agentic_systems",
        "This agentic system targets process improvement in automated workflows. Workflow optimization metrics track task efficiency gains. Error reduction rates are measured across deployment iterations.",
        "quality_improvement",
        True,
    ),
    (
        "time_series",
        "This study benchmarks time series forecasting architectures on standard datasets. Ablation experiments isolate temporal encoding contributions. MAE and RMSE are reported with bootstrap confidence intervals.",
        "method_comparison_benchmarking",
        False,
    ),
    (
        "time_series",
        "We develop a clinical forecasting model for patient trajectory prediction. Risk prediction scores for mortality are validated on external cohorts. Calibration curves assess probability reliability.",
        "prediction_model_validation",
        True,
    ),
    (
        "time_series",
        "This time series study employs a dynamical system model with differential equation formulation. Compartmental model dynamics are simulated across parameter ranges. System dynamics analysis reveals stability regions.",
        "simulation_modeling_tradition",
        True,
    ),
]


@pytest.mark.parametrize("tradition_name", EXPECTED_TRADITIONS)
def test_tradition_schema_valid(tradition_name: str) -> None:
    spec = get_methodology_tradition_by_name(tradition_name)
    assert spec is not None, f"Tradition '{tradition_name}' not found"
    assert isinstance(spec, MethodologyTraditionSpec)
    assert spec.name == tradition_name
    assert spec.display_name, "display_name must be non-empty"
    assert spec.schema_version == "1.0"
    assert isinstance(spec.priority, int) and spec.priority >= 1
    assert len(spec.detection_keywords) >= 2, "need >= 2 detection keywords for matching"
    assert isinstance(spec.mandatory_figures, list)
    assert isinstance(spec.strongly_expected_figures, list)
    assert isinstance(spec.anti_patterns, list)
    assert isinstance(spec.canonical_guideline, dict)
    assert {"name", "governing_body", "stable_for_decade", "canonical"} == set(
        spec.canonical_guideline.keys()
    )
    assert isinstance(spec.venue_specific_appendices, tuple)


@pytest.mark.parametrize(
    "tradition_slug,plan_text",
    TIER_C_PLAN_FIXTURES,
    ids=[t[0] for t in TIER_C_PLAN_FIXTURES],
)
def test_tier_c_selection(tradition_slug: str, plan_text: str) -> None:
    result = classify_methodology(plan_text)
    assert result.primary_tradition == tradition_slug
    assert result.precedence_trace == "stage1_single_match"
    assert tradition_slug in result.candidate_set
    assert result.applied_union_rules == ()


@pytest.mark.parametrize(
    "rule_name,plan_text,expected_primary,expected_union_rules",
    DISAMBIGUATION_PLAN_FIXTURES,
    ids=[f[0] for f in DISAMBIGUATION_PLAN_FIXTURES],
)
def test_disambiguation_rule(
    rule_name: str,
    plan_text: str,
    expected_primary: str,
    expected_union_rules: tuple[str, ...],
) -> None:
    router_result = classify_methodology(plan_text)
    assert len(router_result.candidate_set) >= 2, (
        f"Expected multi-match for {rule_name}, got {router_result.candidate_set}"
    )

    disamb_result = disambiguate(set(router_result.candidate_set))
    assert disamb_result.primary_tradition == expected_primary
    for rule in expected_union_rules:
        assert rule in disamb_result.applied_union_rules
    assert f"rule_{rule_name}" in disamb_result.precedence_trace


@pytest.mark.parametrize(
    "sub_area,plan_text,expected_parent,expected_rerouted",
    VENUE_PLAN_FIXTURES,
    ids=[f"{f[0]}_{f[2]}{'_rerouted' if f[3] else '_primary'}" for f in VENUE_PLAN_FIXTURES],
)
def test_venue_appendix_branching(
    sub_area: str,
    plan_text: str,
    expected_parent: str,
    expected_rerouted: bool,
) -> None:
    matches = resolve_venue_appendices(plan_text)
    sub_area_matches = [m for m in matches if m.sub_area == sub_area]
    assert len(sub_area_matches) >= 1, f"No match for sub_area={sub_area}"
    match = sub_area_matches[0]
    assert match.resolved_parent == expected_parent
    assert match.re_routed == expected_rerouted


def test_no_mandatory_figures_path() -> None:
    spec = get_methodology_tradition_by_name("qualitative_interpretive_tradition")
    assert spec is not None
    assert is_out_of_scope_tradition(spec) is True
    assert spec.mandatory_figures == []
    assert len(spec.strongly_expected_figures) >= 1
    for fig in spec.strongly_expected_figures:
        assert "figure" in fig and "source" in fig
    assert spec.canonical_guideline["name"] == "COREQ/SRQR"


def test_renamed_skill_discoverable() -> None:
    assert "vis-lens-domain-norms" in RETIRED_SKILL_NAMES
    resolver = DefaultSkillResolver()
    info = resolver.resolve("vis-lens-methodology-norms")
    assert info is not None, "DefaultSkillResolver must find vis-lens-methodology-norms"
    assert info.name == "vis-lens-methodology-norms"
    assert info.path.is_file()


def test_classification_determinism() -> None:
    plan_text = TIER_C_PLAN_FIXTURES[0][1]
    results = [classify_methodology(plan_text) for _ in range(10)]
    first = results[0]
    for r in results[1:]:
        assert r.primary_tradition == first.primary_tradition
        assert r.candidate_set == first.candidate_set
        assert r.precedence_trace == first.precedence_trace
        assert r.applied_union_rules == first.applied_union_rules


def test_cache_invalidation(tmp_path: Path) -> None:
    user_dir = tmp_path / ".autoskillit" / "methodology-traditions"
    user_dir.mkdir(parents=True)
    custom_yaml: dict = {
        "name": "cache_test_tradition",
        "display_name": "Cache Test",
        "schema_version": "1.0",
        "priority": 99,
        "canonical_guideline": {
            "name": "TEST",
            "governing_body": "Test",
            "stable_for_decade": True,
            "canonical": True,
        },
        "fields_spanned": ["test"],
        "detection_keywords": ["cache_test_keyword_alpha", "cache_test_keyword_beta"],
        "mandatory_figures": [{"figure": "test", "source": "test"}],
        "strongly_expected_figures": [],
        "anti_patterns": [],
    }
    yaml_path = user_dir / "cache_test_tradition.yaml"
    yaml_path.write_text(yaml.dump(custom_yaml))

    r1 = load_all_methodology_traditions(project_dir=tmp_path)
    names_1 = {s.name for s in r1}
    assert "cache_test_tradition" in names_1

    old_mt = user_dir.stat().st_mtime
    yaml_path.unlink()
    os.utime(user_dir, (old_mt + 2, old_mt + 2))

    r2 = load_all_methodology_traditions(project_dir=tmp_path)
    names_2 = {s.name for s in r2}
    assert "cache_test_tradition" not in names_2


class TestIntegratedPipeline:
    def test_single_tradition_full_pipeline(self) -> None:
        plan_text = (
            "This benchmark study compares five supervised classification architectures "
            "on standard image datasets using an ablation study design. Statistical "
            "significance was assessed via bootstrap confidence intervals across seeds."
        )
        router_result = classify_methodology(plan_text)
        assert router_result.primary_tradition == "method_comparison_benchmarking"

        venue_matches = resolve_venue_appendices(plan_text)
        sub_areas = {m.sub_area for m in venue_matches}
        assert "supervised_classification" in sub_areas

    def test_multi_tradition_disambiguation_then_venue(self) -> None:
        plan_text = (
            "This systematic review synthesized evidence from randomized controlled "
            "trials following PRISMA 2020 guidelines. A clinical trial registry was "
            "consulted. Meta-analysis pooled effect sizes across CONSORT-compliant RCTs."
        )
        router_result = classify_methodology(plan_text)
        assert len(router_result.candidate_set) >= 2

        disamb_result = disambiguate(set(router_result.candidate_set))
        assert disamb_result.primary_tradition == "systematic_synthesis"

    def test_out_of_scope_skips_venue_appendix(self) -> None:
        spec = get_methodology_tradition_by_name("qualitative_interpretive_tradition")
        assert spec is not None
        assert is_out_of_scope_tradition(spec) is True
        assert spec.mandatory_figures == []
        assert len(spec.strongly_expected_figures) >= 1

    def test_venue_reroute_produces_correct_appendix(self) -> None:
        plan_text = (
            "We evaluate a supervised classification model for medical imaging "
            "diagnosis in radiology screening. Sensitivity and specificity are "
            "computed against the pathology reference standard."
        )
        matches = resolve_venue_appendices(plan_text)
        sc_matches = [m for m in matches if m.sub_area == "supervised_classification"]
        assert len(sc_matches) >= 1
        match = sc_matches[0]
        assert match.re_routed is True
        assert match.resolved_parent == "diagnostic_accuracy"
        assert match.appendix.sub_area == "supervised_classification"
        assert len(match.appendix.expectations) >= 1
