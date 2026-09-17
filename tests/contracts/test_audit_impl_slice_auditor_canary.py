"""Committed agent-eval canaries for audit-impl-slice-auditor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.smoke_utils import VALID_CRITERION_TYPES, parse_agent_eval_manifests

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_PROJECT_ROOT = Path(__file__).parents[2]
_MANIFESTS_DIR = _PROJECT_ROOT / ".autoskillit" / "recipes" / "eval" / "manifests"
_CANARY_MANIFEST = _MANIFESTS_DIR / "audit-impl-slice-auditor-canaries.json"
_VARIANT_MANIFEST = _MANIFESTS_DIR / "audit-impl-slice-auditor-variants.json"


def test_committed_audit_slice_canaries_parse(tmp_path: Path) -> None:
    """The committed canary and variant manifests satisfy the eval parser."""
    result = parse_agent_eval_manifests(
        canary_manifest=str(_CANARY_MANIFEST),
        variant_manifest=str(_VARIANT_MANIFEST),
        output_dir=str(tmp_path),
    )

    assert result["success"] == "true", result.get("error")


def test_audit_slice_canaries_cover_incident_and_negative_controls() -> None:
    """The fixture retains the incident and both false-NO-GO controls."""
    canaries = json.loads(_CANARY_MANIFEST.read_text())
    variants = json.loads(_VARIANT_MANIFEST.read_text())

    assert {canary["id"] for canary in canaries} == {"AS01", "AS02", "AS03"}
    for canary in canaries:
        criterion_types = {criterion["type"] for criterion in canary["detection_criteria"]}
        assert "recall" in criterion_types
        assert criterion_types <= VALID_CRITERION_TYPES

    baseline = next(variant for variant in variants if variant["id"] == "baseline")
    assert {"id", "label", "agent_file", "description"} <= baseline.keys()
    assert baseline["agent_file"] == "src/autoskillit/agents/audit-impl-slice-auditor.md"
