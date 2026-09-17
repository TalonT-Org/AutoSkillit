"""Regression coverage for audit substitutions of prescribed mechanisms."""

from __future__ import annotations

import re
from pathlib import Path

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


_INCIDENT_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[2] / ".autoskillit/temp/rectify/incident_evidence.txt"
)
_INCIDENT_REQUIREMENT_IDS = ("REQ-004", "REQ-006", "REQ-007")

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


def _normalize_evidence_excerpt(value: str) -> str:
    return re.sub(r"\s*\n\s*", " ", value).strip()


def _extract_incident_excerpt(text: str, pattern: str) -> str:
    match = re.search(pattern, text, flags=re.DOTALL)
    assert match is not None, f"incident evidence did not match {pattern!r}"
    return _normalize_evidence_excerpt(match.group("value"))


def _load_incident_requirements(path: Path) -> tuple[ProbedRequirement, ...]:
    text = path.read_text(encoding="utf-8")
    requirements = []
    for requirement_id in _INCIDENT_REQUIREMENT_IDS:
        requirement_text = _extract_incident_excerpt(
            text,
            rf"{requirement_id} \(plan text.*?:\s*\"(?P<value>.*?)\"",
        )
        evidence_summary = _extract_incident_excerpt(
            text,
            rf"{requirement_id} VERDICT.*?:\s*\"{requirement_id} — COVERED\. "
            rf"(?P<value>.*?)\"\n(?:  ->|\n)",
        )
        requirements.append(
            ProbedRequirement(
                requirement_id=requirement_id,
                requirement_text=requirement_text,
                evidence_summary=evidence_summary,
            )
        )
    return tuple(requirements)


@pytest.fixture
def incident_requirements() -> tuple[ProbedRequirement, ...]:
    if not _INCIDENT_EVIDENCE_PATH.is_file():
        pytest.skip(f"incident evidence is unavailable: {_INCIDENT_EVIDENCE_PATH}")
    return _load_incident_requirements(_INCIDENT_EVIDENCE_PATH)


def test_incident_requirements_are_flagged_by_both_probe_and_server_floor(
    incident_requirements: tuple[ProbedRequirement, ...],
) -> None:
    findings = probe_substitutions(incident_requirements, _INCIDENT_DIFF)
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
        _INCIDENT_DIFF,
    )

    assert finding is not None
    assert finding.requirement_id == "REQ-006"
    assert finding.trigger is SubstitutionTrigger.DIFF_MOCK_OF_PRESCRIBED_SYMBOL


def test_literal_descendant_implementation_is_not_a_substitution(
    incident_requirements: tuple[ProbedRequirement, ...],
) -> None:
    requirement = incident_requirements[1]
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
