"""Regression coverage for audit substitutions of prescribed mechanisms."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    PRESCRIPTIVE_MECHANISM_CUES,
    SUBSTITUTION_MARKERS,
    ProbedRequirement,
    SubstitutionTrigger,
    evaluate_diff_mock_of_prescribed_symbol,
    evaluate_rationale_contradiction,
    probe_substitutions,
)
from tests._audit_substitution_fixtures import (
    INCIDENT_DIFF,
    INCIDENT_EVIDENCE,
    INCIDENT_REQUIREMENT,
    INCIDENT_REQUIREMENTS,
    LITERAL_EVIDENCE,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


@pytest.fixture
def incident_requirements() -> tuple[ProbedRequirement, ...]:
    """Committed incident requirements (no temp-file dependency)."""
    return INCIDENT_REQUIREMENTS


def test_incident_requirements_are_flagged_by_both_probe_and_server_floor(
    incident_requirements: tuple[ProbedRequirement, ...],
) -> None:
    findings = probe_substitutions(incident_requirements, INCIDENT_DIFF)
    server_floor_findings = tuple(
        evaluate_rationale_contradiction(
            requirement.requirement_id,
            requirement.requirement_text,
            requirement.evidence_summary,
        )
        for requirement in incident_requirements
    )

    assert {finding.requirement_id for finding in findings} == {
        "REQ-004",
        "REQ-006",
        "REQ-007",
    }
    assert all(
        finding.trigger is SubstitutionTrigger.RATIONALE_CONTRADICTION for finding in findings
    )
    assert {
        finding.requirement_id for finding in server_floor_findings if finding is not None
    } == {
        "REQ-004",
        "REQ-006",
        "REQ-007",
    }


def test_diff_rule_detects_mock_of_a_requirement_named_symbol() -> None:
    finding = evaluate_diff_mock_of_prescribed_symbol(
        "REQ-006",
        "Use _has_active_child_processes to observe a real child process.",
        INCIDENT_DIFF,
    )

    assert finding is not None
    assert finding.requirement_id == "REQ-006"
    assert finding.trigger is SubstitutionTrigger.DIFF_MOCK_OF_PRESCRIBED_SYMBOL


def test_literal_descendant_implementation_is_not_a_substitution(
    incident_requirements: tuple[ProbedRequirement, ...],
) -> None:
    requirement = incident_requirements[1]
    literal_diff = "+    assert child.cpu_percent(interval=0.1) >= 0\n"

    assert (
        evaluate_rationale_contradiction(
            requirement.requirement_id,
            requirement.requirement_text,
            LITERAL_EVIDENCE,
        )
        is None
    )
    assert (
        probe_substitutions(
            (
                ProbedRequirement(
                    requirement_id=requirement.requirement_id,
                    requirement_text=requirement.requirement_text,
                    evidence_summary=LITERAL_EVIDENCE,
                ),
            ),
            literal_diff,
        )
        == ()
    )


def test_mock_evidence_without_a_prescribed_mechanism_is_not_flagged() -> None:
    requirement = ProbedRequirement(
        requirement_id="REQ-RENAME",
        requirement_text="Rename the diagnostic docstring for the audit result.",
        evidence_summary="The tests use a mock result while checking the renamed docstring.",
    )

    assert probe_substitutions((requirement,), INCIDENT_DIFF) == ()


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
    assert INCIDENT_DIFF.startswith('"""')
    assert all(req.requirement_id for req in INCIDENT_REQUIREMENTS)
