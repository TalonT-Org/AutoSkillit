"""Tests for experiment type registry loader."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autoskillit.recipe.experiment_type_registry import (
    ExperimentTypeSpec,
    load_all_experiment_types,
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

EXPECTED_TYPES = {
    "benchmark",
    "configuration_study",
    "causal_inference",
    "robustness_audit",
    "exploratory",
    "evidence_synthesis",
    "factorial_design",
    "simulation_modeling",
    "instrument_validation",
    "single_subject",
    "observational_correlational",
    "qualitative_interpretive",
}

VALID_WEIGHT_VALUES = {"H", "M", "L", "S"}

EXPECTED_DIMENSIONS = {
    "causal_structure",
    "variance_protocol",
    "statistical_corrections",
    "ecological_validity",
    "measurement_alignment",
    "resource_proportionality",
    "data_acquisition",
    "agent_implementability",
}


def test_all_bundled_types_present() -> None:
    """All 12 bundled types load without error."""
    types = load_all_experiment_types()
    assert set(types.keys()) == EXPECTED_TYPES


def test_each_type_has_required_fields() -> None:
    """Each type spec has all required fields with correct structure."""
    types = load_all_experiment_types()
    for name, spec in types.items():
        assert spec.name == name, f"{name}: spec.name mismatch"
        assert isinstance(spec.classification_triggers, list), f"{name}: triggers not list"
        assert len(spec.classification_triggers) >= 1, f"{name}: no triggers"
        assert isinstance(spec.dimension_weights, dict), f"{name}: weights not dict"
        assert isinstance(spec.applicable_lenses, dict), f"{name}: lenses not dict"
        assert isinstance(spec.red_team_focus, dict), f"{name}: red_team_focus not dict"
        assert isinstance(spec.l1_severity, dict), f"{name}: l1_severity not dict"


def test_all_weight_values_are_valid() -> None:
    """All dimension_weights values are one of H, M, L, S."""
    types = load_all_experiment_types()
    for name, spec in types.items():
        for dim, weight in spec.dimension_weights.items():
            assert weight in VALID_WEIGHT_VALUES, (
                f"{name}.dimension_weights[{dim!r}] = {weight!r} — not in {VALID_WEIGHT_VALUES}"
            )


def test_all_eight_dimensions_present() -> None:
    """All 8 dimensions from the SKILL.md matrix are present in each bundled type."""
    types = load_all_experiment_types()
    for name, spec in types.items():
        missing = EXPECTED_DIMENSIONS - set(spec.dimension_weights.keys())
        assert not missing, f"{name} missing dimensions: {missing}"


def test_dimension_weights_match_skill_matrix() -> None:
    """Spot-check dimension weights against the values in SKILL.md."""
    types = load_all_experiment_types()
    bench = types["benchmark"]
    assert bench.dimension_weights["causal_structure"] == "S"
    assert bench.dimension_weights["variance_protocol"] == "H"
    assert bench.dimension_weights["agent_implementability"] == "H"
    assert bench.dimension_weights["statistical_corrections"] == "M"

    causal = types["causal_inference"]
    assert causal.dimension_weights["causal_structure"] == "H"
    assert causal.dimension_weights["statistical_corrections"] == "H"
    assert causal.dimension_weights["variance_protocol"] == "L"

    config = types["configuration_study"]
    assert config.dimension_weights["causal_structure"] == "S"
    assert config.dimension_weights["statistical_corrections"] == "H"

    robust = types["robustness_audit"]
    assert robust.dimension_weights["ecological_validity"] == "H"
    assert robust.dimension_weights["data_acquisition"] == "H"

    exploratory = types["exploratory"]
    assert exploratory.dimension_weights["statistical_corrections"] == "S"
    assert exploratory.dimension_weights["agent_implementability"] == "L"


def test_red_team_severity_caps_match_skill() -> None:
    """Red-team severity caps match values in SKILL.md Step 7 RT_MAX_SEVERITY."""
    types = load_all_experiment_types()
    assert types["causal_inference"].red_team_focus["severity_cap"] == "critical"
    assert types["benchmark"].red_team_focus["severity_cap"] == "warning"
    assert types["configuration_study"].red_team_focus["severity_cap"] == "warning"
    assert types["robustness_audit"].red_team_focus["severity_cap"] == "warning"
    assert types["exploratory"].red_team_focus["severity_cap"] == "info"


def test_red_team_type_specific_focus_present() -> None:
    """Each type has a type-specific red_team_focus.specific value."""
    types = load_all_experiment_types()
    assert "asymmetric effort" in types["benchmark"].red_team_focus["specific"]
    assert "overfitting" in types["configuration_study"].red_team_focus["specific"]
    assert "backdoor" in types["causal_inference"].red_team_focus["specific"]
    assert "threat distribution" in types["robustness_audit"].red_team_focus["specific"]
    assert "HARKing" in types["exploratory"].red_team_focus["specific"]


def test_l1_severity_values() -> None:
    """l1_severity values are one of: critical, warning, info."""
    valid_severities = {"critical", "warning", "info"}
    types = load_all_experiment_types()
    for name, spec in types.items():
        for dim, sev in spec.l1_severity.items():
            assert sev in valid_severities, f"{name}.l1_severity[{dim!r}] = {sev!r}"


def test_l1_severity_causal_inference_is_critical() -> None:
    """causal_inference has critical l1_severity for both L1 dimensions."""
    types = load_all_experiment_types()
    causal = types["causal_inference"]
    assert causal.l1_severity["estimand_clarity"] == "critical"
    assert causal.l1_severity["hypothesis_falsifiability"] == "critical"


def test_l1_severity_exploratory_is_info() -> None:
    """exploratory has info l1_severity for both L1 dimensions."""
    types = load_all_experiment_types()
    exp = types["exploratory"]
    assert exp.l1_severity["estimand_clarity"] == "info"
    assert exp.l1_severity["hypothesis_falsifiability"] == "info"


def test_no_project_dir_returns_bundled_only() -> None:
    """With project_dir=None, only bundled types are returned."""
    types = load_all_experiment_types(project_dir=None)
    assert set(types.keys()) == EXPECTED_TYPES


def test_user_override_replaces_bundled_type(tmp_path: Path) -> None:
    """User-defined type with same name fully replaces bundled type (no field merging)."""
    user_dir = tmp_path / ".autoskillit" / "experiment-types"
    user_dir.mkdir(parents=True)
    (user_dir / "benchmark.yaml").write_text(
        yaml.dump(
            {
                "name": "benchmark",
                "classification_triggers": ["custom trigger only"],
                "dimension_weights": {"causal_structure": "H"},
                "applicable_lenses": {"primary": "custom-lens", "secondary": None},
                "red_team_focus": {"specific": "custom focus", "severity_cap": "critical"},
                "l1_severity": {
                    "estimand_clarity": "critical",
                    "hypothesis_falsifiability": "critical",
                },
            }
        )
    )
    types = load_all_experiment_types(project_dir=tmp_path)
    bench = types["benchmark"]
    # Custom values take effect
    assert bench.classification_triggers == ["custom trigger only"]
    assert bench.dimension_weights == {"causal_structure": "H"}
    # Bundled fields (variance_protocol, etc.) are NOT present — full replacement
    assert "variance_protocol" not in bench.dimension_weights
    # Other bundled types remain intact
    assert "causal_inference" in types
    assert len(types) == len(EXPECTED_TYPES)


def test_user_new_type_is_added(tmp_path: Path) -> None:
    """User-defined type with a new name is added alongside bundled types."""
    user_dir = tmp_path / ".autoskillit" / "experiment-types"
    user_dir.mkdir(parents=True)
    (user_dir / "network_analysis.yaml").write_text(
        yaml.dump(
            {
                "name": "network_analysis",
                "classification_triggers": ["IVs are graph topology parameters"],
                "dimension_weights": {
                    "causal_structure": "M",
                    "variance_protocol": "M",
                    "statistical_corrections": "M",
                    "ecological_validity": "M",
                    "measurement_alignment": "H",
                    "resource_proportionality": "L",
                    "data_acquisition": "H",
                    "agent_implementability": "M",
                },
                "applicable_lenses": {"primary": "exp-lens-estimand-clarity", "secondary": None},
                "red_team_focus": {"specific": "connectivity bias", "severity_cap": "warning"},
                "l1_severity": {
                    "estimand_clarity": "warning",
                    "hypothesis_falsifiability": "warning",
                },
            }
        )
    )
    types = load_all_experiment_types(project_dir=tmp_path)
    assert "network_analysis" in types
    assert len(types) == 13  # 12 bundled + 1 user


def test_missing_user_override_dir_is_silent(tmp_path: Path) -> None:
    """A project_dir with no .autoskillit/experiment-types/ is fine — bundled only returned."""
    types = load_all_experiment_types(project_dir=tmp_path)
    assert set(types.keys()) == EXPECTED_TYPES


def test_returns_dict_of_experiment_type_spec() -> None:
    """load_all_experiment_types returns dict[str, ExperimentTypeSpec]."""
    types = load_all_experiment_types()
    for _name, spec in types.items():
        assert isinstance(spec, ExperimentTypeSpec)


def test_causal_inference_classification_triggers_require_manipulation() -> None:
    """causal_inference triggers require explicit manipulation/randomization.

    Not just causal language alone.
    """
    types = load_all_experiment_types()
    causal = types["causal_inference"]
    assert causal.classification_triggers == [
        (
            "Explicit randomization, manipulation, or intervention assignment: "
            "'randomly assigned', 'intervention applied', 'treatment group', "
            "'control group', 'manipulated X', 'assigned to condition'"
        ),
        (
            "Causal claim ('test whether X causes Y') AND explicit confounder "
            "adjustment or treatment assignment described"
        ),
    ]


def test_causal_inference_trigger_covers_rct_language() -> None:
    """causal_inference triggers cover RCT keywords: randomized assignment, treatment/control."""
    types = load_all_experiment_types()
    triggers_text = " ".join(types["causal_inference"].classification_triggers)
    assert "randomly assigned" in triggers_text
    assert "treatment group" in triggers_text
    assert "control group" in triggers_text


def test_no_citation_markers_in_yaml_files() -> None:
    """Experiment-type YAML files must not contain synthetic citation markers."""
    from autoskillit.recipe.experiment_type_registry import BUNDLED_EXPERIMENT_TYPES_DIR

    for yaml_path in sorted(BUNDLED_EXPERIMENT_TYPES_DIR.glob("*.yaml")):
        content = yaml_path.read_text()
        assert chr(0x3010) not in content, f"{yaml_path.name} contains synthetic citation marker"
        assert "†L" not in content, f"{yaml_path.name} contains synthetic citation marker"


def test_all_types_have_schema_version() -> None:
    """Every bundled type has schema_version == '1.0'."""
    types = load_all_experiment_types()
    for name, spec in types.items():
        assert spec.schema_version == "1.0", f"{name}: schema_version = {spec.schema_version!r}"


def test_priority_values_are_unique_except_fallback() -> None:
    """No duplicate priority values among non-fallback types; exploratory has priority 999."""
    types = load_all_experiment_types()
    non_fallback_priorities = [spec.priority for spec in types.values() if not spec.is_fallback]
    assert len(non_fallback_priorities) == len(set(non_fallback_priorities)), (
        "Duplicate priority values among non-fallback types"
    )
    assert types["exploratory"].priority == 999


def test_only_exploratory_is_fallback() -> None:
    """exploratory is the sole fallback type; all others have is_fallback=False."""
    types = load_all_experiment_types()
    assert types["exploratory"].is_fallback is True
    for name, spec in types.items():
        if name != "exploratory":
            assert spec.is_fallback is False, f"{name}: is_fallback should be False"


def test_priority_assignments_match_contract() -> None:
    """Each type's priority matches the contract from the implementation plan."""
    EXPECTED_PRIORITIES = {
        "causal_inference": 1,
        "evidence_synthesis": 2,
        "benchmark": 3,
        "factorial_design": 4,
        "configuration_study": 5,
        "simulation_modeling": 6,
        "instrument_validation": 7,
        "robustness_audit": 8,
        "single_subject": 9,
        "observational_correlational": 10,
        "qualitative_interpretive": 11,
        "exploratory": 999,
    }
    types = load_all_experiment_types()
    for name, expected_priority in EXPECTED_PRIORITIES.items():
        assert types[name].priority == expected_priority, (
            f"{name}: expected priority {expected_priority}, got {types[name].priority}"
        )


def test_new_types_dimension_weight_rationale_coverage() -> None:
    """For each new type, every H or M dimension weight has a rationale entry."""
    new_types = {
        "evidence_synthesis",
        "factorial_design",
        "simulation_modeling",
        "instrument_validation",
        "single_subject",
        "observational_correlational",
        "qualitative_interpretive",
    }
    types = load_all_experiment_types()
    for name in new_types:
        spec = types[name]
        for dim, weight in spec.dimension_weights.items():
            if weight in ("H", "M"):
                assert dim in spec.dimension_weight_rationale, (
                    f"{name}: dimension '{dim}' has weight '{weight}' but no rationale entry"
                )
                assert spec.dimension_weight_rationale[dim], (
                    f"{name}: dimension '{dim}' rationale is empty"
                )


def test_new_types_red_team_severity_caps() -> None:
    """evidence_synthesis has severity_cap=critical; all other new types have warning."""
    types = load_all_experiment_types()
    assert types["evidence_synthesis"].red_team_focus["severity_cap"] == "critical"
    for name in (
        "factorial_design",
        "simulation_modeling",
        "instrument_validation",
        "single_subject",
        "observational_correlational",
        "qualitative_interpretive",
    ):
        assert types[name].red_team_focus["severity_cap"] == "warning", (
            f"{name}: expected severity_cap=warning"
        )


def test_new_types_dimension_weights_spot_check() -> None:
    """Spot-check key distinguishing weights for each new type."""
    types = load_all_experiment_types()
    assert types["evidence_synthesis"].dimension_weights["measurement_alignment"] == "H"
    assert types["evidence_synthesis"].dimension_weights["causal_structure"] == "L"

    assert types["factorial_design"].dimension_weights["causal_structure"] == "H"
    assert types["factorial_design"].dimension_weights["ecological_validity"] == "L"

    assert types["qualitative_interpretive"].dimension_weights["causal_structure"] == "S"
    assert types["qualitative_interpretive"].dimension_weights["statistical_corrections"] == "S"

    assert types["single_subject"].dimension_weights["statistical_corrections"] == "S"
    assert types["single_subject"].dimension_weights["variance_protocol"] == "H"

    assert types["simulation_modeling"].dimension_weights["data_acquisition"] == "L"
    assert types["simulation_modeling"].dimension_weights["agent_implementability"] == "H"

    assert types["instrument_validation"].dimension_weights["causal_structure"] == "S"
    assert types["instrument_validation"].dimension_weights["measurement_alignment"] == "H"

    assert types["observational_correlational"].dimension_weights["ecological_validity"] == "H"
    assert types["observational_correlational"].dimension_weights["variance_protocol"] == "L"


def test_qualitative_interpretive_falsifiability_is_info() -> None:
    """qualitative_interpretive has hypothesis_falsifiability=info (unique among all types)."""
    types = load_all_experiment_types()
    assert types["qualitative_interpretive"].l1_severity["hypothesis_falsifiability"] == "info"


def test_evidence_synthesis_estimand_clarity_is_critical() -> None:
    """evidence_synthesis has estimand_clarity=critical."""
    types = load_all_experiment_types()
    assert types["evidence_synthesis"].l1_severity["estimand_clarity"] == "critical"
