"""Tests for methodology_venue_appendix — Stage B venue appendix resolution."""

from __future__ import annotations

import dataclasses

import pytest

from autoskillit.recipe.methodology_tradition_registry import (
    VenueAppendixDef,
    load_all_methodology_traditions,
)
from autoskillit.recipe.methodology_venue_appendix import (
    MLSubAreaFoldingDef,
    VenueAppendixMatch,
    load_ml_sub_area_folding,
    resolve_venue_appendices,
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


# ---------------------------------------------------------------------------
# T1. Folding map structural tests
# ---------------------------------------------------------------------------


class TestFoldingMapStructure:
    def test_all_eight_sub_areas_present(self) -> None:
        entries = load_ml_sub_area_folding()
        slugs = {e.sub_area for e in entries}
        expected = {
            "foundation_models",
            "reinforcement_learning",
            "supervised_classification",
            "nlp",
            "computer_vision",
            "generative_models",
            "agentic_systems",
            "time_series",
        }
        assert slugs == expected

    def test_each_entry_has_primary_parent(self) -> None:
        for entry in load_ml_sub_area_folding():
            assert entry.primary_parent, f"{entry.sub_area} has no primary_parent"

    def test_each_entry_has_at_least_one_alternate(self) -> None:
        for entry in load_ml_sub_area_folding():
            assert len(entry.alternate_parents) >= 1, f"{entry.sub_area} has no alternate_parents"

    def test_all_parent_slugs_reference_valid_traditions(self) -> None:
        traditions = {s.name for s in load_all_methodology_traditions()}
        for entry in load_ml_sub_area_folding():
            assert entry.primary_parent in traditions, (
                f"{entry.sub_area} primary_parent={entry.primary_parent} not in traditions"
            )
            for alt in entry.alternate_parents:
                assert alt.parent in traditions, (
                    f"{entry.sub_area} alternate parent={alt.parent} not in traditions"
                )

    def test_foundation_models_cosmin_has_constraint(self) -> None:
        fm = next(e for e in load_ml_sub_area_folding() if e.sub_area == "foundation_models")
        cosmin = next(
            (
                a
                for a in fm.alternate_parents
                if a.parent == "measurement_instrument_validation_tradition"
            ),
            None,
        )
        assert cosmin is not None
        assert cosmin.constraint == "only_if_explicit_construct_measurement"

    def test_folding_entries_are_frozen(self) -> None:
        for entry in load_ml_sub_area_folding():
            assert isinstance(entry, MLSubAreaFoldingDef)
            with pytest.raises(dataclasses.FrozenInstanceError):
                entry.sub_area = "mutated"  # type: ignore[misc]

    def test_folding_map_deterministic(self) -> None:
        results = [load_ml_sub_area_folding() for _ in range(10)]
        assert all(r == results[0] for r in results)


# ---------------------------------------------------------------------------
# T2. Venue appendix schema tests
# ---------------------------------------------------------------------------


class TestVenueAppendixSchema:
    def test_all_traditions_with_appendices_load(self) -> None:
        traditions = list(load_all_methodology_traditions())
        assert traditions

    def test_appendix_entries_have_required_fields(self) -> None:
        traditions = load_all_methodology_traditions()
        for spec in traditions:
            for app in spec.venue_specific_appendices:
                assert app.sub_area
                assert len(app.trigger_keywords) >= 1
                assert len(app.expectations) >= 1

    def test_appendix_expectations_have_figure_and_source(self) -> None:
        traditions = load_all_methodology_traditions()
        for spec in traditions:
            for app in spec.venue_specific_appendices:
                for exp in app.expectations:
                    assert "figure" in exp
                    assert "source" in exp
                    assert exp["figure"]
                    assert exp["source"]

    def test_appendix_sub_areas_match_folding_map(self) -> None:
        folding = load_ml_sub_area_folding()
        valid_sub_areas = {e.sub_area for e in folding}
        traditions = load_all_methodology_traditions()
        for spec in traditions:
            for app in spec.venue_specific_appendices:
                assert app.sub_area in valid_sub_areas, (
                    f"{spec.name} appendix sub_area={app.sub_area} not in folding map"
                )

    def test_primary_parent_traditions_have_appendices(self) -> None:
        mcb = next(
            (
                s
                for s in load_all_methodology_traditions()
                if s.name == "method_comparison_benchmarking"
            ),
            None,
        )
        assert mcb is not None
        assert len(mcb.venue_specific_appendices) >= len(load_ml_sub_area_folding())

    def test_alternate_parent_traditions_have_appendices(self) -> None:
        folding = load_ml_sub_area_folding()
        alt_parents = set()
        for entry in folding:
            for alt in entry.alternate_parents:
                alt_parents.add(alt.parent)
        traditions = {s.name: s for s in load_all_methodology_traditions()}
        for parent in alt_parents:
            spec = traditions.get(parent)
            assert spec is not None
            assert len(spec.venue_specific_appendices) >= 1

    def test_venue_appendix_def_is_frozen(self) -> None:
        traditions = load_all_methodology_traditions()
        for spec in traditions:
            for app in spec.venue_specific_appendices:
                assert isinstance(app, VenueAppendixDef)
                with pytest.raises(dataclasses.FrozenInstanceError):
                    app.sub_area = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# T3. Stage B routing tests — 24 branching fixtures
# ---------------------------------------------------------------------------

VENUE_BRANCHING_FIXTURES: list[tuple[str, str, str, bool]] = [
    # --- Foundation Models ---
    (
        "foundation_models",
        "We evaluate GPT-4 on the BIG-bench leaderboard comparing few-shot performance "
        "against SOTA baselines using ablation study and benchmark evaluation protocol.",
        "method_comparison_benchmarking",
        False,
    ),
    (
        "foundation_models",
        "We assess GPT-4 calibration on held-out clinical tasks, measuring few-shot scaling "
        "behavior and zero-shot validation across risk strata.",
        "prediction_model_validation",
        True,
    ),
    (
        "foundation_models",
        "We apply psychometric analysis with item response theory to evaluate the latent "
        "trait structure of GPT-4 outputs using Cronbach alpha.",
        "measurement_instrument_validation_tradition",
        True,
    ),
    # --- Reinforcement Learning ---
    (
        "reinforcement_learning",
        "We benchmark our RL agent against SOTA baselines on the Atari leaderboard "
        "using ablation study and performance comparison across evaluation protocol.",
        "method_comparison_benchmarking",
        False,
    ),
    (
        "reinforcement_learning",
        "We study multi-agent emergent behavior in a population dynamics simulation "
        "with swarm intelligence and agent-based reward structures.",
        "simulation_modeling_tradition",
        True,
    ),
    (
        "reinforcement_learning",
        "We perform policy evaluation on held-out task environments measuring "
        "sample efficiency across training budgets.",
        "prediction_model_validation",
        True,
    ),
    # --- Supervised Classification ---
    (
        "supervised_classification",
        "We compare classification methods on the benchmark leaderboard using "
        "ablation study and SOTA baseline performance comparison.",
        "method_comparison_benchmarking",
        False,
    ),
    (
        "supervised_classification",
        "We evaluate medical imaging classification for radiology pathology detection "
        "reporting sensitivity and specificity at operating points.",
        "diagnostic_accuracy",
        True,
    ),
    (
        "supervised_classification",
        "We build a clinical decision support model for patient outcome prognosis "
        "and risk prediction in the supervised classification pipeline.",
        "prediction_model_validation",
        True,
    ),
    # --- NLP ---
    (
        "nlp",
        "We benchmark our NLP model against SOTA on the GLUE leaderboard with "
        "ablation study and performance comparison protocol.",
        "method_comparison_benchmarking",
        False,
    ),
    (
        "nlp",
        "We develop a clinical NLP system for patient outcome risk scoring "
        "from clinical text with calibrated predictions.",
        "prediction_model_validation",
        True,
    ),
    (
        "nlp",
        "We conduct discourse analysis of clinical conversations using thematic "
        "coding and narrative structure with qualitative assessment.",
        "qualitative_interpretive_tradition",
        True,
    ),
    # --- Computer Vision ---
    (
        "computer_vision",
        "We benchmark our detection model against SOTA methods on COCO using "
        "ablation study and mAP performance comparison.",
        "method_comparison_benchmarking",
        False,
    ),
    (
        "computer_vision",
        "We evaluate medical imaging for radiology pathology detection measuring "
        "sensitivity and specificity per diagnostic threshold.",
        "diagnostic_accuracy",
        True,
    ),
    (
        "computer_vision",
        "We build a patient outcome prognosis model from imaging biomarker features "
        "with clinical decision support and risk prediction.",
        "prediction_model_validation",
        True,
    ),
    # --- Generative Models ---
    (
        "generative_models",
        "We benchmark our GAN against SOTA on FID and IS metrics using "
        "ablation study and leaderboard performance comparison.",
        "method_comparison_benchmarking",
        False,
    ),
    (
        "generative_models",
        "We conduct human evaluation with qualitative assessment of generated "
        "content using expert rating and content analysis protocols.",
        "qualitative_interpretive_tradition",
        True,
    ),
    (
        "generative_models",
        "We validate the human evaluation instrument measuring inter-rater "
        "reliability and construct validity with scale development.",
        "measurement_instrument_validation_tradition",
        True,
    ),
    # --- Agentic Systems ---
    (
        "agentic_systems",
        "We benchmark our agentic system against SOTA baselines on task success "
        "using ablation study and leaderboard evaluation protocol.",
        "method_comparison_benchmarking",
        False,
    ),
    (
        "agentic_systems",
        "We build a multi-agent simulation with emergent behavior using "
        "agent-based model design and swarm intelligence patterns.",
        "simulation_modeling_tradition",
        True,
    ),
    (
        "agentic_systems",
        "We evaluate process improvement and workflow optimization via "
        "agent-driven task efficiency and error reduction metrics.",
        "quality_improvement",
        True,
    ),
    # --- Time-Series ---
    (
        "time_series",
        "We benchmark our forecasting model against SOTA on the Monash archive "
        "using ablation study and performance comparison protocol.",
        "method_comparison_benchmarking",
        False,
    ),
    (
        "time_series",
        "We develop clinical forecasting for patient trajectory and mortality "
        "prediction with temporal risk scoring.",
        "prediction_model_validation",
        True,
    ),
    (
        "time_series",
        "We build a dynamical system simulation with differential equation "
        "and compartmental model for system dynamics forecasting.",
        "simulation_modeling_tradition",
        True,
    ),
]


class TestVenueBranchingFixtures:
    @pytest.mark.parametrize(
        "sub_area,plan_text,expected_parent,expected_rerouted",
        VENUE_BRANCHING_FIXTURES,
        ids=[f"{sa}-{ep}" for sa, _, ep, _ in VENUE_BRANCHING_FIXTURES],
    )
    def test_venue_branching(
        self, sub_area: str, plan_text: str, expected_parent: str, expected_rerouted: bool
    ) -> None:
        matches = resolve_venue_appendices(plan_text)
        assert matches, f"resolve_venue_appendices returned empty list for plan_text={plan_text!r}"
        sub_area_matches = [m for m in matches if m.sub_area == sub_area]
        assert len(sub_area_matches) == 1, (
            f"Expected 1 match for sub_area={sub_area!r}, "
            f"got {len(sub_area_matches)}: {sub_area_matches}"
        )
        match = sub_area_matches[0]
        assert match.resolved_parent == expected_parent
        assert match.re_routed == expected_rerouted


# ---------------------------------------------------------------------------
# T4. Constraint tests
# ---------------------------------------------------------------------------


class TestConstraintLogic:
    def test_cosmin_constraint_blocks_without_explicit_construct(self) -> None:
        plan_text = (
            "We study GPT-4 using Cronbach alpha to measure internal consistency "
            "of the benchmark scores across evaluation protocol."
        )
        matches = resolve_venue_appendices(plan_text)
        fm = [m for m in matches if m.sub_area == "foundation_models"]
        assert len(fm) == 1
        assert fm[0].resolved_parent == "method_comparison_benchmarking"

    def test_cosmin_constraint_allows_with_explicit_construct(self) -> None:
        plan_text = (
            "We apply psychometric analysis with item response theory to evaluate the latent "
            "trait structure of GPT-4 outputs using Cronbach alpha."
        )
        matches = resolve_venue_appendices(plan_text)
        fm = [m for m in matches if m.sub_area == "foundation_models"]
        assert len(fm) == 1
        assert fm[0].resolved_parent == "measurement_instrument_validation_tradition"
        assert fm[0].re_routed is True


# ---------------------------------------------------------------------------
# T5. Edge case and behavioral tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_no_sub_area_detected_returns_empty(self) -> None:
        plan_text = "We study the effect of temperature on enzyme activity."
        matches = resolve_venue_appendices(plan_text)
        assert matches == []

    def test_multiple_sub_areas_detected(self) -> None:
        plan_text = (
            "We compare classification methods on medical imaging for radiology detection "
            "using ablation study and benchmark evaluation protocol."
        )
        matches = resolve_venue_appendices(plan_text)
        sub_areas = {m.sub_area for m in matches}
        assert {"supervised_classification", "computer_vision"}.issubset(sub_areas), (
            f"Expected supervised_classification and computer_vision in detected sub_areas, "
            f"got {sub_areas}"
        )

    def test_deterministic_resolution(self) -> None:
        plan_text = (
            "We benchmark our NLP model against SOTA on the GLUE leaderboard with "
            "ablation study and performance comparison protocol."
        )
        results = [resolve_venue_appendices(plan_text) for _ in range(10)]
        assert all(r == results[0] for r in results)

    def test_result_is_frozen(self) -> None:
        plan_text = (
            "We benchmark our NLP model against SOTA on the GLUE leaderboard with "
            "ablation study and performance comparison protocol."
        )
        matches = resolve_venue_appendices(plan_text)
        for m in matches:
            assert isinstance(m, VenueAppendixMatch)
            with pytest.raises(dataclasses.FrozenInstanceError):
                m.sub_area = "mutated"  # type: ignore[misc]

    def test_case_insensitive_keyword_matching(self) -> None:
        plan_text_lower = (
            "we evaluate medical imaging classification for radiology pathology detection "
            "reporting sensitivity and specificity at operating points."
        )
        plan_text_mixed = (
            "We EVALUATE Medical Imaging Classification for RADIOLOGY Pathology Detection "
            "reporting Sensitivity and Specificity at operating points."
        )
        matches_lower = resolve_venue_appendices(plan_text_lower)
        matches_mixed = resolve_venue_appendices(plan_text_mixed)
        assert {m.sub_area for m in matches_lower} == {m.sub_area for m in matches_mixed}
