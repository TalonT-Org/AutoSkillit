"""Tests for methodology tradition registry loader."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autoskillit.recipe.methodology_tradition_registry import (
    BUNDLED_METHODOLOGY_TRADITIONS_DIR,
    MethodologyTraditionSpec,
    get_methodology_tradition_by_name,
    is_out_of_scope_tradition,
    load_all_methodology_traditions,
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

EXPECTED_TRADITIONS = {
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
}


def test_all_bundled_traditions_present() -> None:
    traditions = load_all_methodology_traditions()
    assert {s.name for s in traditions} == EXPECTED_TRADITIONS


def test_each_tradition_has_required_fields() -> None:
    traditions = load_all_methodology_traditions()
    for spec in traditions:
        assert isinstance(spec.name, str), f"{spec.name}: name not str"
        assert spec.name, f"{spec.name}: name is empty"
        assert isinstance(spec.display_name, str), f"{spec.name}: display_name not str"
        assert isinstance(spec.schema_version, str), f"{spec.name}: schema_version not str"
        assert isinstance(spec.priority, int), f"{spec.name}: priority not int"
        assert isinstance(spec.canonical_guideline, dict), (
            f"{spec.name}: canonical_guideline not dict"
        )
        assert isinstance(spec.fields_spanned, list), f"{spec.name}: fields_spanned not list"
        assert len(spec.fields_spanned) >= 1, f"{spec.name}: fields_spanned is empty"
        assert isinstance(spec.detection_keywords, list), (
            f"{spec.name}: detection_keywords not list"
        )
        assert len(spec.detection_keywords) >= 1, f"{spec.name}: detection_keywords is empty"
        assert isinstance(spec.mandatory_figures, list), f"{spec.name}: mandatory_figures not list"
        assert isinstance(spec.strongly_expected_figures, list), (
            f"{spec.name}: strongly_expected_figures not list"
        )
        assert isinstance(spec.anti_patterns, list), f"{spec.name}: anti_patterns not list"
        assert len(spec.anti_patterns) >= 1, f"{spec.name}: anti_patterns is empty"


def test_all_traditions_have_schema_version_1_0() -> None:
    traditions = load_all_methodology_traditions()
    for spec in traditions:
        assert spec.schema_version == "1.0", (
            f"{spec.name}: schema_version = {spec.schema_version!r}"
        )


def test_priority_values_are_unique() -> None:
    traditions = load_all_methodology_traditions()
    priorities = [s.priority for s in traditions]
    assert len(priorities) == len(set(priorities)), "Duplicate priority values found"
    assert sorted(priorities) == list(range(1, 13)), "Priority values should be 1-12"


def test_evaluation_order_matches_priority() -> None:
    traditions = load_all_methodology_traditions()
    names = [s.name for s in traditions]
    sorted_by_priority = sorted(traditions, key=lambda s: (s.priority, s.name))
    assert names == [s.name for s in sorted_by_priority]


def test_no_fallback_entries() -> None:
    traditions = load_all_methodology_traditions()
    for spec in traditions:
        assert not hasattr(spec, "is_fallback"), (
            f"{spec.name}: should not have is_fallback attribute"
        )


def test_qualitative_interpretive_has_empty_mandatory_figures() -> None:
    traditions = load_all_methodology_traditions()
    by_name = {s.name: s for s in traditions}
    qual = by_name["qualitative_interpretive_tradition"]
    assert qual.mandatory_figures == []


def test_is_out_of_scope_tradition_qualitative() -> None:
    traditions = load_all_methodology_traditions()
    by_name = {s.name: s for s in traditions}
    qual = by_name["qualitative_interpretive_tradition"]
    assert is_out_of_scope_tradition(qual) is True


def test_is_out_of_scope_tradition_normal() -> None:
    traditions = load_all_methodology_traditions()
    by_name = {s.name: s for s in traditions}
    for name, spec in by_name.items():
        if name != "qualitative_interpretive_tradition":
            assert is_out_of_scope_tradition(spec) is False, f"{name}: should not be out of scope"


def test_user_override_replaces_bundled(tmp_path: Path) -> None:
    user_dir = tmp_path / ".autoskillit" / "methodology-traditions"
    user_dir.mkdir(parents=True)
    (user_dir / "controlled_intervention.yaml").write_text(
        yaml.dump(
            {
                "name": "controlled_intervention",
                "schema_version": "1.0",
                "priority": 1,
                "display_name": "My Custom RCT",
                "canonical_guideline": {
                    "name": "CUSTOM",
                    "governing_body": "custom",
                    "stable_for_decade": False,
                    "canonical": False,
                },
                "fields_spanned": ["custom_field"],
                "detection_keywords": ["custom keyword"],
                "mandatory_figures": [],
                "strongly_expected_figures": [],
                "anti_patterns": [],
            }
        )
    )
    traditions = load_all_methodology_traditions(project_dir=tmp_path)
    by_name = {s.name: s for s in traditions}
    assert by_name["controlled_intervention"].display_name == "My Custom RCT"
    assert len(traditions) == 12


def test_user_new_tradition_is_added(tmp_path: Path) -> None:
    user_dir = tmp_path / ".autoskillit" / "methodology-traditions"
    user_dir.mkdir(parents=True)
    (user_dir / "my_new_tradition.yaml").write_text(
        yaml.dump(
            {
                "name": "my_new_tradition",
                "schema_version": "1.0",
                "priority": 99,
                "display_name": "My New Tradition",
                "canonical_guideline": {
                    "name": "CUSTOM",
                    "governing_body": "custom",
                    "stable_for_decade": True,
                    "canonical": True,
                },
                "fields_spanned": ["custom_field"],
                "detection_keywords": ["custom keyword"],
                "mandatory_figures": [],
                "strongly_expected_figures": [],
                "anti_patterns": [],
            }
        )
    )
    traditions = load_all_methodology_traditions(project_dir=tmp_path)
    by_name = {s.name: s for s in traditions}
    assert "my_new_tradition" in by_name
    assert len(traditions) == 13


def test_missing_user_override_dir_is_silent(tmp_path: Path) -> None:
    traditions = load_all_methodology_traditions(project_dir=tmp_path)
    assert {s.name for s in traditions} == EXPECTED_TRADITIONS


def test_no_project_dir_returns_bundled_only() -> None:
    traditions = load_all_methodology_traditions(project_dir=None)
    assert {s.name for s in traditions} == EXPECTED_TRADITIONS


def test_schema_mismatch_warns(tmp_path: Path) -> None:
    import structlog.testing

    user_dir = tmp_path / ".autoskillit" / "methodology-traditions"
    user_dir.mkdir(parents=True)
    (user_dir / "future_tradition.yaml").write_text(
        yaml.dump(
            {
                "name": "future_tradition",
                "schema_version": "2.0",
                "priority": 99,
                "display_name": "Future Tradition",
                "canonical_guideline": {
                    "name": "FUTURE",
                    "governing_body": "future",
                    "stable_for_decade": True,
                    "canonical": True,
                },
                "fields_spanned": ["future_field"],
                "detection_keywords": ["future"],
                "mandatory_figures": [],
                "strongly_expected_figures": [],
                "anti_patterns": [],
            }
        )
    )
    with structlog.testing.capture_logs() as cap_logs:
        traditions = load_all_methodology_traditions(project_dir=tmp_path)

    assert any(entry.get("schema_version") == "2.0" for entry in cap_logs), (
        "Expected WARNING about schema_version mismatch"
    )
    by_name = {s.name: s for s in traditions}
    assert "future_tradition" in by_name


def test_get_methodology_tradition_by_name_found() -> None:
    spec = get_methodology_tradition_by_name("controlled_intervention")
    assert spec is not None
    assert spec.name == "controlled_intervention"
    assert isinstance(spec, MethodologyTraditionSpec)


def test_get_methodology_tradition_by_name_not_found() -> None:
    spec = get_methodology_tradition_by_name("nonexistent_tradition")
    assert spec is None


def test_no_citation_markers_in_yaml_files() -> None:
    for yaml_path in sorted(BUNDLED_METHODOLOGY_TRADITIONS_DIR.glob("*.yaml")):
        content = yaml_path.read_text()
        assert chr(0x3010) not in content, f"{yaml_path.name} contains synthetic citation marker"
        assert "daggerL" not in content, f"{yaml_path.name} contains synthetic citation marker"


def test_canonical_guideline_fields_complete() -> None:
    traditions = load_all_methodology_traditions()
    required_subkeys = {"name", "governing_body", "stable_for_decade", "canonical"}
    for spec in traditions:
        assert set(spec.canonical_guideline.keys()) == required_subkeys, (
            f"{spec.name}: canonical_guideline missing fields"
        )


def test_controlled_intervention_is_priority_1() -> None:
    traditions = load_all_methodology_traditions()
    by_name = {s.name: s for s in traditions}
    assert by_name["controlled_intervention"].priority == 1


def test_detection_keywords_are_distinct() -> None:
    traditions = load_all_methodology_traditions()
    by_name = {s.name: s for s in traditions}
    for name1, spec1 in by_name.items():
        for name2, spec2 in by_name.items():
            if name1 != name2:
                assert spec1.detection_keywords != spec2.detection_keywords, (
                    f"{name1} and {name2} have identical detection_keywords"
                )
