"""Smoke-utils tests relocated from the former monolith."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

from autoskillit.smoke_utils import (
    EXPERIMENTAL_REVIEW_AUDITORS,
    aggregate_combined_review_candidates,
    build_malformed_review_envelope,
    determine_experimental_review_verdict,
    normalize_local_review_finding,
    render_review_finding_body,
    validate_experimental_auditor_outputs,
)
from tests.smoke_utils._experimental_helpers import (
    _experimental_candidate,
)

pytestmark = [pytest.mark.medium]


def test_malformed_review_envelope_bounds_untrusted_output() -> None:
    raw = "π" * 5000

    envelope = build_malformed_review_envelope(
        producer=EXPERIMENTAL_REVIEW_AUDITORS[0],
        terminal_status="success",
        raw_output=raw,
        errors=[f"{index}-{'x' * 2000}" for index in range(1000)],
        rejection_reason="schema_invalid",
    )

    raw_bytes = raw.encode()
    assert envelope["received_byte_length"] == len(raw_bytes)
    assert envelope["received_sha256"] == hashlib.sha256(raw_bytes).hexdigest()
    assert envelope["excerpt_byte_length"] <= 4096
    assert len(str(envelope["excerpt"]).encode()) <= 4096
    assert envelope["excerpt"] != raw
    assert "raw_output" not in envelope
    assert len(envelope["errors"]) == 32
    assert all(len(str(error).encode()) <= 1024 for error in envelope["errors"])


def test_experimental_output_validation_is_atomic_and_fixed_order(tmp_path: Path) -> None:
    reachability, abstraction = EXPERIMENTAL_REVIEW_AUDITORS
    input_candidates = {
        abstraction: _experimental_candidate("overengineering_abstraction_surface"),
        reachability: _experimental_candidate("overengineering_reachability"),
    }
    valid_outputs = {
        abstraction: {
            "terminal_status": "success",
            "output": [input_candidates[abstraction]],
        },
        reachability: {
            "terminal_status": "success",
            "output": [input_candidates[reachability]],
        },
    }
    kwargs = {
        "valid_diff_lines": {"src/app.py": [42]},
        "snapshot": {"head_sha": "head", "diff_sha256": "diff"},
        "review_root": str(tmp_path),
    }

    complete = validate_experimental_auditor_outputs(outputs=valid_outputs, **kwargs)
    assert complete["state"] == "complete"
    assert [candidate["auditor_name"] for candidate in complete["candidates"]] == list(
        EXPERIMENTAL_REVIEW_AUDITORS
    )
    assert [candidate["original_index"] for candidate in complete["candidates"]] == [0, 0]
    expected_identities = []
    for auditor_name in EXPERIMENTAL_REVIEW_AUDITORS:
        canonical = json.dumps(
            input_candidates[auditor_name],
            sort_keys=True,
            separators=(",", ":"),
        )
        record_digest = hashlib.sha256(canonical.encode()).hexdigest()
        identity = json.dumps(
            {
                "snapshot": kwargs["snapshot"],
                "auditor_name": auditor_name,
                "original_index": 0,
                "record_digest": record_digest,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        expected_identities.append((record_digest, hashlib.sha256(identity.encode()).hexdigest()))
    assert [
        (candidate["record_digest"], candidate["candidate_id"])
        for candidate in complete["candidates"]
    ] == expected_identities

    malformed_outputs = json.loads(json.dumps(valid_outputs))
    malformed_outputs[abstraction]["output"][0]["message"] = ""
    degraded = validate_experimental_auditor_outputs(outputs=malformed_outputs, **kwargs)
    assert degraded["state"] == "degraded"
    assert degraded["candidates"] == []
    assert degraded["status_by_name"][reachability]["status"] == "success"
    assert degraded["status_by_name"][abstraction]["reason_code"] == "schema_invalid"
    assert len(degraded["malformed_envelopes"]) == 1

    wrong_enum_type = json.loads(json.dumps(valid_outputs))
    wrong_enum_type[abstraction]["output"][0]["severity"] = []
    degraded = validate_experimental_auditor_outputs(outputs=wrong_enum_type, **kwargs)
    assert degraded["state"] == "degraded"
    assert degraded["candidates"] == []
    assert degraded["status_by_name"][abstraction]["reason_code"] == "schema_invalid"

    empty = validate_experimental_auditor_outputs(
        outputs={
            auditor: {"terminal_status": "success", "output": []}
            for auditor in EXPERIMENTAL_REVIEW_AUDITORS
        },
        **kwargs,
    )
    assert empty["state"] == "complete"
    assert empty["candidates"] == []


@pytest.mark.parametrize(
    "confidence",
    [
        10**1000,
        -(10**1000),
        math.inf,
        -math.inf,
        math.nan,
        "0.9",
        None,
    ],
)
def test_experimental_validation_degrades_extreme_confidence_without_raising(
    tmp_path: Path, confidence: object
) -> None:
    reachability, abstraction = EXPERIMENTAL_REVIEW_AUDITORS
    invalid_candidate = _experimental_candidate("overengineering_abstraction_surface")
    invalid_candidate["confidence"] = confidence
    outputs = {
        reachability: {
            "terminal_status": "success",
            "output": [_experimental_candidate("overengineering_reachability")],
        },
        abstraction: {
            "terminal_status": "success",
            "output": [invalid_candidate],
        },
    }

    result = validate_experimental_auditor_outputs(
        outputs=outputs,
        valid_diff_lines={"src/app.py": [42]},
        snapshot={"head_sha": "head", "diff_sha256": "diff"},
        review_root=str(tmp_path),
    )

    assert result["state"] == "degraded"
    assert result["candidates"] == []
    assert result["status_by_name"][reachability]["status"] == "success"
    assert result["status_by_name"][abstraction]["reason_code"] == "schema_invalid"
    assert len(result["malformed_envelopes"]) == 1
    assert len(json.dumps(result["malformed_envelopes"]).encode()) < 40_000


def test_experimental_validation_bounds_oversized_payload_and_rejects_mixed_batch(
    tmp_path: Path,
) -> None:
    reachability, abstraction = EXPERIMENTAL_REVIEW_AUDITORS
    oversized = _experimental_candidate("overengineering_abstraction_surface")
    oversized["message"] = "x" * (1024 * 1024 + 1)
    result = validate_experimental_auditor_outputs(
        outputs={
            reachability: {
                "terminal_status": "success",
                "output": [_experimental_candidate("overengineering_reachability")],
            },
            abstraction: {"terminal_status": "success", "output": [oversized]},
        },
        valid_diff_lines={"src/app.py": [42]},
        snapshot={"head_sha": "head", "diff_sha256": "diff"},
        review_root=str(tmp_path),
    )

    assert result["state"] == "degraded"
    assert result["candidates"] == []
    envelope = result["malformed_envelopes"][0]
    assert envelope["received_byte_length"] > 1024 * 1024
    assert envelope["excerpt_byte_length"] <= 4096
    assert len(envelope["errors"]) == 1


@pytest.mark.parametrize(
    ("field", "value", "expected_reason"),
    [
        ("file", "../outside.py", "path_escape"),
        ("line", 99, "not_changed_line"),
    ],
)
def test_experimental_validation_preserves_specific_reason_codes(
    tmp_path: Path,
    field: str,
    value: object,
    expected_reason: str,
) -> None:
    outputs = {}
    for auditor, dimension in zip(
        EXPERIMENTAL_REVIEW_AUDITORS,
        ("overengineering_reachability", "overengineering_abstraction_surface"),
        strict=True,
    ):
        candidate = _experimental_candidate(dimension)
        candidate[field] = value
        outputs[auditor] = {"terminal_status": "success", "output": [candidate]}

    result = validate_experimental_auditor_outputs(
        outputs=outputs,
        valid_diff_lines={"src/app.py": [42]},
        snapshot={"head_sha": "head", "diff_sha256": "diff"},
        review_root=str(tmp_path),
    )

    assert result["state"] == "degraded"
    assert result["candidates"] == []
    assert all(
        status["reason_code"] == expected_reason for status in result["status_by_name"].values()
    )
    assert all(
        envelope["rejection_reason"] == expected_reason
        for envelope in result["malformed_envelopes"]
    )


@pytest.mark.parametrize(
    "missing_facet",
    [
        "return_values",
        "exceptions_errors",
        "ordering",
        "persistence",
        "concurrency",
        "compatibility",
    ],
)
def test_experimental_validation_rejects_each_missing_behavior_facet(
    tmp_path: Path, missing_facet: str
) -> None:
    phrases = {
        "return_values": "return values",
        "exceptions_errors": "exceptions and errors",
        "ordering": "ordering",
        "persistence": "persistence",
        "concurrency": "concurrency",
        "compatibility": "compatibility",
    }
    outputs = {}
    for auditor, dimension in zip(
        EXPERIMENTAL_REVIEW_AUDITORS,
        ("overengineering_reachability", "overengineering_abstraction_surface"),
        strict=True,
    ):
        candidate = _experimental_candidate(dimension)
        candidate["simpler_behavior"] = ", ".join(
            phrase for facet, phrase in phrases.items() if facet != missing_facet
        )
        outputs[auditor] = {"terminal_status": "success", "output": [candidate]}

    result = validate_experimental_auditor_outputs(
        outputs=outputs,
        valid_diff_lines={"src/app.py": [42]},
        snapshot={"head_sha": "head", "diff_sha256": "diff"},
        review_root=str(tmp_path),
    )

    assert result["state"] == "degraded"
    assert result["candidates"] == []
    assert all(
        status["reason_code"] == "schema_invalid" for status in result["status_by_name"].values()
    )


@pytest.mark.parametrize(
    ("facet", "superstring"),
    [
        ("return_values", "returning"),
        ("exceptions_errors", "exceptional"),
        ("ordering", "reordering"),
        ("persistence", "persistent"),
        ("concurrency", "concurrency-safe"),
        ("compatibility", "non-compatibility"),
    ],
)
def test_experimental_validation_rejects_behavior_facet_superstrings(
    tmp_path: Path,
    facet: str,
    superstring: str,
) -> None:
    phrases = {
        "return_values": "return values",
        "exceptions_errors": "exceptions and errors",
        "ordering": "ordering",
        "persistence": "persistence",
        "concurrency": "concurrency",
        "compatibility": "compatibility",
    }
    phrases[facet] = superstring
    outputs = {}
    for auditor, dimension in zip(
        EXPERIMENTAL_REVIEW_AUDITORS,
        ("overengineering_reachability", "overengineering_abstraction_surface"),
        strict=True,
    ):
        candidate = _experimental_candidate(dimension)
        candidate["simpler_behavior"] = ", ".join(phrases.values())
        outputs[auditor] = {"terminal_status": "success", "output": [candidate]}

    result = validate_experimental_auditor_outputs(
        outputs=outputs,
        valid_diff_lines={"src/app.py": [42]},
        snapshot={"head_sha": "head", "diff_sha256": "diff"},
        review_root=str(tmp_path),
    )

    assert result["state"] == "degraded"
    assert all(facet in str(envelope["errors"]) for envelope in result["malformed_envelopes"])


def test_experimental_validation_accepts_boundaries_and_negated_facets(tmp_path: Path) -> None:
    outputs = {}
    for auditor, dimension in zip(
        EXPERIMENTAL_REVIEW_AUDITORS,
        ("overengineering_reachability", "overengineering_abstraction_surface"),
        strict=True,
    ):
        candidate = _experimental_candidate(dimension)
        candidate["simpler_behavior"] = (
            "Return values stay equivalent; no exceptions or errors; ordering is unchanged; "
            "persistence is unchanged; no concurrency change; no compatibility break."
        )
        outputs[auditor] = {"terminal_status": "success", "output": [candidate]}

    result = validate_experimental_auditor_outputs(
        outputs=outputs,
        valid_diff_lines={"src/app.py": [42]},
        snapshot={"head_sha": "head", "diff_sha256": "diff"},
        review_root=str(tmp_path),
    )

    assert result["state"] == "complete"


def test_experimental_candidate_closed_key_error_lists_missing_and_extra(tmp_path: Path) -> None:
    outputs = {}
    for auditor, dimension in zip(
        EXPERIMENTAL_REVIEW_AUDITORS,
        ("overengineering_reachability", "overengineering_abstraction_surface"),
        strict=True,
    ):
        candidate = _experimental_candidate(dimension)
        del candidate["message"]
        candidate["unexpected"] = True
        outputs[auditor] = {"terminal_status": "success", "output": [candidate]}

    result = validate_experimental_auditor_outputs(
        outputs=outputs,
        valid_diff_lines={"src/app.py": [42]},
        snapshot={"head_sha": "head", "diff_sha256": "diff"},
        review_root=str(tmp_path),
    )

    assert all(
        envelope["errors"]
        == ["candidate has invalid closed keys: missing=['message']; extra=['unexpected']"]
        for envelope in result["malformed_envelopes"]
    )


@pytest.mark.parametrize(
    ("failure_kind", "expected_reason"),
    [
        ("tool_failure", "tool_failure"),
        ("refusal", "refusal"),
        ("interruption", "interruption"),
        ("truncation", "truncation"),
        ("missing_result", "missing_result"),
        ("malformed_json", "malformed_json"),
        ("non_array", "non_array"),
        ("schema_invalid", "schema_invalid"),
    ],
)
def test_experimental_failure_matrix_degrades_without_partial_candidates(
    tmp_path: Path, failure_kind: str, expected_reason: str
) -> None:
    reachability, abstraction = EXPERIMENTAL_REVIEW_AUDITORS
    outputs: dict[str, dict[str, object]] = {
        reachability: {
            "terminal_status": "success",
            "output": [_experimental_candidate("overengineering_reachability")],
        }
    }
    if failure_kind in {"tool_failure", "refusal", "interruption", "truncation"}:
        outputs[abstraction] = {"terminal_status": failure_kind, "output": "[]"}
    elif failure_kind == "malformed_json":
        outputs[abstraction] = {"terminal_status": "success", "output": "["}
    elif failure_kind == "non_array":
        outputs[abstraction] = {"terminal_status": "success", "output": "{}"}
    elif failure_kind == "schema_invalid":
        candidate = _experimental_candidate("overengineering_abstraction_surface")
        candidate["message"] = ""
        outputs[abstraction] = {"terminal_status": "success", "output": [candidate]}

    result = validate_experimental_auditor_outputs(
        outputs=outputs,
        valid_diff_lines={"src/app.py": [42]},
        snapshot={"head_sha": "head", "diff_sha256": "diff"},
        review_root=str(tmp_path),
    )

    assert result["state"] == "degraded"
    assert result["candidates"] == []
    assert result["status_by_name"][reachability]["status"] == "success"
    assert result["status_by_name"][abstraction]["reason_code"] == expected_reason
    envelope = result["malformed_envelopes"][0]
    assert envelope["producer"] == abstraction
    assert envelope["rejection_reason"] == expected_reason


def test_experimental_aggregation_is_deterministic_and_retains_losers() -> None:
    reachability, abstraction = EXPERIMENTAL_REVIEW_AUDITORS
    candidates = [
        {
            "candidate_id": "suppressed",
            "auditor_name": reachability,
            "original_index": 0,
            "file": "src/old.py",
            "line": 10,
            "severity": "critical",
            "requires_decision": False,
        },
        {
            "candidate_id": "fixed-rank-winner",
            "auditor_name": reachability,
            "original_index": 2,
            "file": "src/app.py",
            "line": 42,
            "severity": "warning",
            "requires_decision": False,
        },
        {
            "candidate_id": "dedup-loser",
            "auditor_name": abstraction,
            "original_index": 0,
            "file": "src/app.py",
            "line": 42,
            "severity": "warning",
            "requires_decision": False,
        },
        {
            "candidate_id": "rejected",
            "auditor_name": reachability,
            "original_index": 1,
            "file": "src/other.py",
            "line": 7,
            "severity": "critical",
            "requires_decision": False,
        },
    ]
    dispositions = [
        {
            "candidate_id": candidate_id,
            "disposition_id": f"disposition-{candidate_id}",
            "reason_code": "accepted",
        }
        for candidate_id in ("suppressed", "fixed-rank-winner", "dedup-loser")
    ] + [
        {
            "candidate_id": "rejected",
            "disposition_id": "disposition-rejected",
            "reason_code": "insufficient_evidence",
        }
    ]
    kwargs = {
        "dispositions": dispositions,
        "prior_resolved_findings": [{"file": "src/old.py", "line": 12}],
    }

    forward = aggregate_combined_review_candidates(candidates=candidates, **kwargs)
    reverse = aggregate_combined_review_candidates(candidates=list(reversed(candidates)), **kwargs)

    assert forward == reverse
    assert [candidate["candidate_id"] for candidate in forward["survivors"]] == [
        "fixed-rank-winner"
    ]
    records = forward["aggregation_records"]
    assert any(
        record
        == {
            "candidate_id": "suppressed",
            "reason_code": "suppressed_prior_thread",
        }
        for record in records
    )
    loser = next(record for record in records if record["candidate_id"] == "dedup-loser")
    assert loser["reason_code"] == "duplicate_candidate"
    assert loser["winner_candidate_id"] == "fixed-rank-winner"
    assert loser["member_ids"] == ["fixed-rank-winner", "dedup-loser"]
    assert "rejected" not in {str(record["candidate_id"]) for record in records}


def test_experimental_aggregation_rejects_accepted_disposition_without_identity() -> None:
    candidate = {
        "candidate_id": "candidate-1",
        "auditor_name": EXPERIMENTAL_REVIEW_AUDITORS[0],
        "original_index": 0,
        "file": "src/app.py",
        "line": 42,
        "severity": "critical",
        "requires_decision": False,
    }

    result = aggregate_combined_review_candidates(
        candidates=[candidate],
        dispositions=[{"candidate_id": "candidate-1", "reason_code": "accepted"}],
        prior_resolved_findings=[],
    )

    assert result == {
        "state": "complete",
        "survivors": [],
        "aggregation_records": [],
        "validation_errors": [],
    }


def test_combined_review_aggregation_is_cross_source_and_completion_order_independent() -> None:
    reachability, abstraction = EXPERIMENTAL_REVIEW_AUDITORS
    standard = [
        {
            "file": "src/app.py",
            "line": 42,
            "dimension": "arch",
            "severity": "warning",
            "message": "Standard finding wins the collision",
            "requires_decision": False,
        }
    ]
    deletion = [
        {
            "file": "src/deleted.py",
            "line": 8,
            "dimension": "deletion_regression",
            "severity": "critical",
            "message": "Deleted behavior was restored",
            "requires_decision": False,
        }
    ]
    experimental = [
        {
            "candidate_id": "experimental-collision",
            "auditor_name": reachability,
            "original_index": 0,
            "file": "src/app.py",
            "line": 42,
            "dimension": "overengineering_reachability",
            "severity": "warning",
            "message": "No consumer is reachable",
            "requires_decision": False,
        },
        {
            "candidate_id": "experimental-abstraction",
            "auditor_name": abstraction,
            "original_index": 0,
            "file": "src/other.py",
            "line": 19,
            "dimension": "overengineering_abstraction_surface",
            "severity": "warning",
            "message": "The abstraction surface is unused",
            "requires_decision": False,
        },
    ]
    dispositions = [
        {
            "candidate_id": item["candidate_id"],
            "disposition_id": f"disposition-{item['candidate_id']}",
            "reason_code": "accepted",
        }
        for item in experimental
    ]
    kwargs = {
        "dispositions": dispositions,
        "prior_resolved_findings": [],
        "standard_findings": standard,
        "deletion_findings": deletion,
        "valid_diff_lines": {
            "src/app.py": [42],
            "src/deleted.py": [8],
            "src/other.py": [19],
        },
        "snapshot": {"head_sha": "head", "base_sha": "base"},
        "review_root": str(Path.cwd()),
    }

    forward = aggregate_combined_review_candidates(
        candidates=experimental,
        **kwargs,
    )
    reverse = aggregate_combined_review_candidates(
        candidates=list(reversed(experimental)),
        **kwargs,
    )

    assert forward == reverse
    assert [item["dimension"] for item in forward["survivors"]] == [
        "arch",
        "deletion_regression",
        "overengineering_abstraction_surface",
    ]
    standard_winner_id = str(forward["survivors"][0]["candidate_id"])
    loser = next(
        record
        for record in forward["aggregation_records"]
        if record["candidate_id"] == "experimental-collision"
    )
    assert loser["winner_candidate_id"] == standard_winner_id
    assert loser["member_ids"] == [standard_winner_id, "experimental-collision"]
    assert loser["dedup_group_id"]
    assert "fixed source rank" in loser["rationale"]


def test_review_finding_renderer_is_shared_and_preserves_proof_provenance() -> None:
    experimental = {
        "severity": "warning",
        "dimension": "overengineering_reachability",
        "message": "No consumer is reachable",
        "evidence": [
            {
                "path": "src/app.py",
                "line": 42,
                "role": "anchor",
                "claim": "Declaration",
            }
        ],
        "candidate_id": "candidate-1",
        "disposition_id": "disposition-1",
    }

    rendered = render_review_finding_body(experimental)

    assert rendered.startswith("[warning] overengineering_reachability: No consumer is reachable")
    assert "src/app.py:42 [anchor] Declaration" in rendered
    assert "candidate_id=candidate-1 disposition_id=disposition-1" in rendered
    assert (
        render_review_finding_body(
            {
                "severity": "critical",
                "dimension": "bugs",
                "message": "Standard finding",
            }
        )
        == "[critical] bugs: Standard finding"
    )


def test_review_finding_renderer_bounds_utf8_body_and_reserves_provenance() -> None:
    rendered = render_review_finding_body(
        {
            "severity": "warning",
            "dimension": "overengineering_reachability",
            "message": "π" * 100_000,
            "evidence": [
                {
                    "path": "src/" + ("nested/" * 1000) + "app.py",
                    "line": 42,
                    "role": "anchor" * 1000,
                    "claim": "claim" * 10_000,
                }
                for _index in range(20)
            ],
            "candidate_id": "candidate-1",
            "disposition_id": "disposition-1",
        }
    )

    assert len(rendered.encode("utf-8")) <= 60 * 1024
    assert rendered.endswith("Provenance: candidate_id=candidate-1 disposition_id=disposition-1")
    assert "π" * 100_000 not in rendered


def test_local_review_normalization_preserves_proof_fields_and_adds_aliases() -> None:
    finding = {
        "file": "src/app.py",
        "line": 42,
        "severity": "warning",
        "dimension": "overengineering_reachability",
        "message": "No consumer is reachable",
        "evidence": [{"path": "src/app.py", "line": 42, "role": "anchor", "claim": "declaration"}],
        "trace": [{"path": "src/app.py", "line": 42}],
        "boundary_checks": [{"boundary": "public_api", "status": "checked_absent"}],
        "confidence": 0.9,
        "simpler_behavior": "Equivalent return values and errors",
        "candidate_id": "candidate-1",
        "disposition_id": "disposition-1",
        "snapshot": {"head_sha": "head"},
    }

    normalized = normalize_local_review_finding(finding)

    assert {key: normalized[key] for key in finding} == finding
    assert normalized["path"] == finding["file"]
    assert normalized["body"] == render_review_finding_body(finding)


@pytest.mark.parametrize(
    (
        "retained_snapshot_was_valid",
        "final_snapshot_is_fresh",
        "gate_state",
        "experimental_audit_state",
        "findings",
        "expected",
    ),
    [
        (True, False, "valid_true", "complete", [], "stale_snapshot"),
        (
            True,
            True,
            "degraded",
            "degraded",
            [{"severity": "critical", "requires_decision": False}],
            "changes_requested",
        ),
        (False, False, "degraded", "not_eligible", [], "needs_human"),
        (True, True, "valid_true", "degraded", [], "needs_human"),
        (
            True,
            True,
            "valid_true",
            "complete",
            [
                {"severity": "warning", "requires_decision": False},
                {"severity": "info", "requires_decision": True},
            ],
            "approved_with_comments",
        ),
        (
            True,
            True,
            "valid_true",
            "complete",
            [{"severity": "info", "requires_decision": True}],
            "needs_human",
        ),
        (True, True, "valid_false", "not_required", [], "approved"),
    ],
)
def test_experimental_review_verdict_branches_and_precedence(
    retained_snapshot_was_valid: bool,
    final_snapshot_is_fresh: bool,
    gate_state: str,
    experimental_audit_state: str,
    findings: list[dict[str, object]],
    expected: str,
) -> None:
    assert (
        determine_experimental_review_verdict(
            retained_snapshot_was_valid=retained_snapshot_was_valid,
            final_snapshot_is_fresh=final_snapshot_is_fresh,
            gate_state=gate_state,
            experimental_audit_state=experimental_audit_state,
            findings=findings,
        )
        == expected
    )


def test_valid_retained_snapshot_movement_is_stale() -> None:
    assert (
        determine_experimental_review_verdict(
            retained_snapshot_was_valid=True,
            final_snapshot_is_fresh=False,
            gate_state="valid_true",
            experimental_audit_state="complete",
            findings=[],
        )
        == "stale_snapshot"
    )
