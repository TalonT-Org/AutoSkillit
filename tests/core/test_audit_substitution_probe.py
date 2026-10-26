"""Regression coverage for audit substitutions of prescribed mechanisms."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    PRESCRIPTIVE_MECHANISM_CUES,
    SUBSTITUTION_MARKERS,
    evaluate_rationale_contradiction,
)
from tests._audit_substitution_fixtures import (
    INCIDENT_EVIDENCE,
    INCIDENT_REQUIREMENT,
    INCIDENT_REQUIREMENTS,
    LITERAL_EVIDENCE,
    IncidentRequirement,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


@pytest.fixture
def incident_requirements() -> tuple[IncidentRequirement, ...]:
    """Committed incident requirements (no temp-file dependency)."""
    return INCIDENT_REQUIREMENTS


def test_incident_requirements_are_flagged_by_server_floor(
    incident_requirements: tuple[IncidentRequirement, ...],
) -> None:
    server_floor_findings = tuple(
        evaluate_rationale_contradiction(
            requirement.requirement_id,
            requirement.requirement_text,
            requirement.evidence_summary,
        )
        for requirement in incident_requirements
    )

    assert {
        finding.requirement_id for finding in server_floor_findings if finding is not None
    } == {
        "REQ-004",
        "REQ-006",
        "REQ-007",
    }


def test_literal_descendant_implementation_is_not_a_substitution(
    incident_requirements: tuple[IncidentRequirement, ...],
) -> None:
    requirement = incident_requirements[1]

    assert (
        evaluate_rationale_contradiction(
            requirement.requirement_id,
            requirement.requirement_text,
            LITERAL_EVIDENCE,
        )
        is None
    )


def test_mock_evidence_without_a_prescribed_mechanism_is_not_flagged() -> None:
    assert (
        evaluate_rationale_contradiction(
            "REQ-RENAME",
            "Rename the diagnostic docstring for the audit result.",
            "The tests use a mock result while checking the renamed docstring.",
        )
        is None
    )


def test_substitution_vocabulary_spot_check_preserves_known_markers_and_cues() -> None:
    # Spot-check the canonical marker and cue vocabulary. The full literal
    # sets are covered by tests/arch/test_audit_blocking_literal_inventory.py;
    # this assertion guards against accidental removal of well-known members.
    expected_markers = frozenset(
        {"mock", "monkeypatch", "patch", "stub", "fake", "simulated", "instead of"}
    )
    expected_cues = frozenset(
        {"spawn", "real", "actual", "parent", "child", "process", "integration"}
    )

    assert expected_markers <= SUBSTITUTION_MARKERS
    assert expected_cues <= PRESCRIPTIVE_MECHANISM_CUES


def test_incident_constants_cover_the_floor_rule_inputs() -> None:
    """The shared fixtures must keep the floor rule inputs reachable."""
    assert INCIDENT_REQUIREMENT
    assert INCIDENT_EVIDENCE
    assert all(req.requirement_id for req in INCIDENT_REQUIREMENTS)
