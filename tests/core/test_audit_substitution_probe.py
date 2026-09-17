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

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


_INCIDENT_REQUIREMENTS = (
    ProbedRequirement(
        requirement_id="REQ-004",
        requirement_text=(
            "Keep the parent process alive while its child process remains CPU-active."
        ),
        evidence_summary=(
            "Mocks the child-process liveness predicate to return True while the parent waits."
        ),
    ),
    ProbedRequirement(
        requirement_id="REQ-006",
        requirement_text=(
            "For a CPU-active child that never stops, child_deferral_ceiling=1.0, "
            "assert kill happens within ~1s of ceiling expiry."
        ),
        evidence_summary=(
            "Uses a persistently-active child (_has_active_child_processes mocked to "
            "always return True) with child_deferral_ceiling=1.0."
        ),
    ),
    ProbedRequirement(
        requirement_id="REQ-007",
        requirement_text=(
            "Spawn a real descendant process and assert its parent is terminated after "
            "the child deferral ceiling."
        ),
        evidence_summary=(
            "The test patches child liveness instead of spawning a real descendant."
        ),
    ),
)

_INCIDENT_DIFF = '''\
"""Child-process liveness is simulated via a mock on
``_has_active_child_processes`` rather than relying on real psutil CPU-percent
sampling."""
+    monkeypatch.setattr(
+        _patch_process__termination,
+        "_has_active_child_processes",
+        lambda pid: True,
+    )
'''


def test_incident_requirements_are_flagged_by_both_probe_and_server_floor() -> None:
    findings = probe_substitutions(_INCIDENT_REQUIREMENTS, _INCIDENT_DIFF)
    server_floor_findings = tuple(
        evaluate_rationale_contradiction(
            requirement.requirement_id,
            requirement.requirement_text,
            requirement.evidence_summary,
        )
        for requirement in _INCIDENT_REQUIREMENTS
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
        _INCIDENT_DIFF,
    )

    assert finding is not None
    assert finding.requirement_id == "REQ-006"
    assert finding.trigger is SubstitutionTrigger.DIFF_MOCK_OF_PRESCRIBED_SYMBOL


def test_literal_descendant_implementation_is_not_a_substitution() -> None:
    requirement = _INCIDENT_REQUIREMENTS[1]
    literal_evidence = (
        'Starts subprocess.Popen(["sh", "-c", "sleep 30 & wait"]) and asserts with '
        "psutil that the real child process remains active until termination."
    )
    literal_diff = "+    assert child.cpu_percent(interval=0.1) >= 0\n"

    assert (
        evaluate_rationale_contradiction(
            requirement.requirement_id,
            requirement.requirement_text,
            literal_evidence,
        )
        is None
    )
    assert (
        probe_substitutions(
            (
                ProbedRequirement(
                    requirement_id=requirement.requirement_id,
                    requirement_text=requirement.requirement_text,
                    evidence_summary=literal_evidence,
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

    assert probe_substitutions((requirement,), _INCIDENT_DIFF) == ()


def test_substitution_vocabulary_is_exact() -> None:
    assert SUBSTITUTION_MARKERS == frozenset(
        {
            "mock",
            "mocks",
            "mocked",
            "monkeypatch",
            "patch",
            "patched",
            "stub",
            "stubbed",
            "side_effect",
            "fake",
            "faked",
            "simulated",
            "simulates",
            "instead of",
            "in lieu of",
            "rather than",
        }
    )
    assert PRESCRIPTIVE_MECHANISM_CUES == frozenset(
        {
            "spawn",
            "real",
            "actual",
            "topology",
            "parent",
            "child",
            "descendant",
            "process",
            "end-to-end",
            "integration",
            "live",
            "genuine",
            "not mocked",
        }
    )
