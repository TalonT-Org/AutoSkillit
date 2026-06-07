"""Agent-eval canary manifest schema conformance tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.smoke_utils._eval import (
    REQUIRED_CRITERION_KEYS,
    VALID_CRITERION_TYPES,
    parse_agent_eval_manifests,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_valid_criterion_types_matches_validator():
    """VALID_CRITERION_TYPES constant matches what the validator accepts."""
    assert VALID_CRITERION_TYPES == frozenset({"precision", "recall", "recognition"})


def test_required_criterion_keys_matches_validator():
    """REQUIRED_CRITERION_KEYS constant matches what the validator checks."""
    assert REQUIRED_CRITERION_KEYS == frozenset({"text", "type"})


def test_skill_md_example_passes_validator(tmp_path: Path):
    """The canonical SKILL.md example JSON must pass parse_agent_eval_manifests."""
    fixture = Path(__file__).parent / "fixtures" / "agent_eval_prep_schema_example.json"
    variant_fixture = Path(__file__).parent / "fixtures" / "agent_eval_prep_variant_example.json"
    canary_manifest = tmp_path / "canaries.json"
    variant_manifest = tmp_path / "variants.json"
    canary_manifest.write_text(fixture.read_text())
    variant_manifest.write_text(variant_fixture.read_text())
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    result = parse_agent_eval_manifests(
        canary_manifest=str(canary_manifest),
        variant_manifest=str(variant_manifest),
        output_dir=str(output_dir),
    )
    assert result["success"] == "true", f"Fixture failed validation: {result.get('error')}"


def test_invalid_criterion_type_rejected_per_constant(tmp_path: Path):
    """A criterion type NOT in VALID_CRITERION_TYPES must be rejected."""
    invalid_type = "invalid_type_not_in_constant"
    assert invalid_type not in VALID_CRITERION_TYPES

    canaries = [
        {
            "id": "NEG-1",
            "agent_name": "test-agent",
            "prompt_template": "Test {var}",
            "prompt_vars": {"var": "value"},
            "reference_path": "test.patch",
            "reference_type": "patch",
            "gap_description": "negative test",
            "detection_criteria": [{"text": "criterion", "type": invalid_type}],
        }
    ]
    variants = [
        {"id": "baseline", "label": "Baseline", "agent_file": "test.md", "description": "test"}
    ]

    canary_path = tmp_path / "canaries.json"
    variant_path = tmp_path / "variants.json"
    canary_path.write_text(json.dumps(canaries))
    variant_path.write_text(json.dumps(variants))
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    result = parse_agent_eval_manifests(
        canary_manifest=str(canary_path),
        variant_manifest=str(variant_path),
        output_dir=str(output_dir),
    )
    assert result["success"] == "false"
    assert "invalid type" in result["error"]
