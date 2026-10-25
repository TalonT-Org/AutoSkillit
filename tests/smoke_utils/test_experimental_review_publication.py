from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from autoskillit.core import DiffAnchorAuthority, github_review_receipt_filename
from autoskillit.smoke_utils import (
    EXPERIMENTAL_REVIEW_AUDITORS,
    aggregate_combined_review_candidates,
    prepare_experimental_review_publication,
    publish_experimental_review_artifacts,
    render_review_finding_body,
    render_unpostable_review_section,
    validate_experimental_auditor_outputs,
)
from tests._github_review_fixtures import authoritative_receipt
from tests.smoke_utils._experimental_helpers import (
    _experimental_candidate,
)
from tests.smoke_utils._review_publication_fixtures import (
    _GENERATION_IDENTITY_KEYS,
    _REVIEW_BASE,
    _REVIEW_HEAD,
    _prepared_publication,
    _publish,
    _write_server_receipt,
)

pytestmark = [pytest.mark.medium]


def _write_receipt_payload(output_dir: Path, payload: bytes, *, pr_number: int = 42) -> Path:
    receipt_path = output_dir / github_review_receipt_filename(pr_number)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_bytes(payload)
    receipt_path.chmod(0o600)
    return receipt_path


@pytest.mark.parametrize("diff_context", [[], {}, {"_head_sha": 42}])
def test_github_publication_rejects_malformed_diff_context_before_writes(
    tmp_path: Path,
    diff_context: object,
) -> None:
    publication = _prepared_publication("github")
    publication["artifacts"]["diff_context"] = diff_context
    output_dir = tmp_path / "review-output"

    with pytest.raises(ValueError, match="diff_context must contain a non-empty _head_sha"):
        publish_experimental_review_artifacts(
            publication=publication,
            output_dir=str(output_dir),
            pr_number="42",
        )

    assert not output_dir.exists()


@pytest.mark.parametrize("mode", ["local", "github"])
def test_publication_rejects_mode_incompatible_marker_before_writes(
    tmp_path: Path,
    mode: str,
) -> None:
    publication = _prepared_publication(mode)
    marker = "github_review_binding" if mode == "local" else "local_findings"
    publication["artifacts"].pop("local_findings", None)
    publication["artifacts"][marker] = {}
    publication["artifact_order"] = ["raw_findings", "diff_context", marker]
    output_dir = tmp_path / "review-output"

    with pytest.raises(ValueError, match=f"{marker} is incompatible with {mode} mode"):
        publish_experimental_review_artifacts(
            publication=publication,
            output_dir=str(output_dir),
            pr_number="42",
        )

    assert not output_dir.exists()


def _authority(right_side_lines: dict[str, list[int]]) -> DiffAnchorAuthority:
    return DiffAnchorAuthority.authoritative(
        repository="openai/autoskillit",
        pr_number=1,
        head_sha="a" * 40,
        generation_id="generation",
        right_side_lines=right_side_lines,
        left_side_lines={},
    )


def test_experimental_publication_preserves_provenance_and_suppresses_stale_effects(
    tmp_path: Path,
) -> None:
    reachability, abstraction = EXPERIMENTAL_REVIEW_AUDITORS
    snapshot = {
        "head_sha": "head",
        "base_sha": "base",
        "merge_base_sha": "merge-base",
        "diff_sha256": "diff",
    }
    validation = validate_experimental_auditor_outputs(
        outputs={
            reachability: {
                "terminal_status": "success",
                "output": [_experimental_candidate("overengineering_reachability")],
            },
            abstraction: {
                "terminal_status": "success",
                "output": [
                    _experimental_candidate(
                        "overengineering_abstraction_surface",
                        line=43,
                    )
                ],
            },
        },
        anchor_authority=_authority({"src/app.py": [42, 43]}),
        snapshot=snapshot,
        review_root=str(tmp_path),
    )
    accepted, rejected = validation["candidates"]
    dispositions = [
        {
            "candidate_id": accepted["candidate_id"],
            "disposition_id": "accepted-disposition",
            "reason_code": "accepted",
        },
        {
            "candidate_id": rejected["candidate_id"],
            "disposition_id": "rejected-disposition",
            "reason_code": "simpler_behavior_not_equivalent",
        },
    ]
    aggregation = aggregate_combined_review_candidates(
        candidates=validation["candidates"],
        dispositions=dispositions,
        prior_resolved_findings=[],
        anchor_authority=_authority({}),
    )
    assert [item["candidate_id"] for item in aggregation["survivors"]] == [
        accepted["candidate_id"]
    ]
    survivor = aggregation["survivors"][0]
    assert survivor["disposition_id"] == "accepted-disposition"

    ledger = {
        "candidates": validation["candidates"],
        "disposition_records": dispositions,
        "aggregation_records": aggregation["aggregation_records"],
    }
    publication = prepare_experimental_review_publication(
        raw_ledger=ledger,
        survivors=aggregation["survivors"],
        snapshot=snapshot,
        annotation_generation_id="annotation-generation",
        mode="local",
        snapshot_is_fresh=True,
    )

    assert publication["artifact_order"] == [
        "raw_findings",
        "diff_context",
        "local_findings",
    ]
    assert "effect_artifacts" not in publication
    artifacts = publication["artifacts"]
    identity_keys = {
        "_head_sha",
        "_base_sha",
        "_merge_base_sha",
        "annotation_generation_id",
        "review_generation_id",
    }
    identities = [{key: artifact[key] for key in identity_keys} for artifact in artifacts.values()]
    assert identities[1:] == identities[:-1]
    for artifact_name, field in (
        ("diff_context", "context_entries"),
        ("local_findings", "findings"),
    ):
        finding = artifacts[artifact_name][field][0]
        assert {key: finding[key] for key in survivor} == survivor
        assert finding["path"] == survivor["file"]
        assert finding["side"] == "RIGHT"
        assert finding["code_region"] == ""
        assert finding["body"] == render_review_finding_body(survivor)
        assert finding["candidate_id"] == accepted["candidate_id"]
        assert finding["disposition_id"] == "accepted-disposition"
        assert finding["evidence"] == accepted["evidence"]
        assert finding["trace"] == accepted["trace"]
        assert finding["boundary_checks"] == accepted["boundary_checks"]
        assert rejected["candidate_id"] not in {
            item["candidate_id"] for item in artifacts[artifact_name][field]
        }

    stale = prepare_experimental_review_publication(
        raw_ledger=ledger,
        survivors=aggregation["survivors"],
        snapshot=snapshot,
        annotation_generation_id="annotation-generation",
        mode="local",
        snapshot_is_fresh=False,
    )
    assert stale["state"] == "stale_snapshot"
    assert stale["artifact_order"] == ["raw_findings"]
    assert set(stale["artifacts"]) == {"raw_findings"}
    assert "effect_artifacts" not in stale
    assert stale["artifacts"]["raw_findings"]["survivors"] == []
    assert (
        stale["artifacts"]["raw_findings"]["review_generation_id"]
        != publication["artifacts"]["raw_findings"]["review_generation_id"]
    )

    no_survivors = prepare_experimental_review_publication(
        raw_ledger=ledger,
        survivors=[],
        snapshot=snapshot,
        annotation_generation_id="annotation-generation",
        mode="local",
        snapshot_is_fresh=True,
    )
    assert (
        no_survivors["artifacts"]["raw_findings"]["review_generation_id"]
        != publication["artifacts"]["raw_findings"]["review_generation_id"]
    )


def test_experimental_publication_retires_obsolete_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "review-output"
    _, receipt_path = _write_server_receipt(output_dir, pr_number=18)
    receipt_bytes = receipt_path.read_bytes()
    receipt_inode = receipt_path.stat().st_ino
    assert receipt_path.stat().st_mode & 0o777 == 0o600

    local = _publish(output_dir, "local", pr_number="18")
    assert {path.name for path in output_dir.iterdir()} == {
        "raw_findings_18.json",
        "diff_context_18.json",
        "local_findings_18.json",
        receipt_path.name,
    }
    assert receipt_path.read_bytes() == receipt_bytes
    assert receipt_path.stat().st_ino == receipt_inode

    github = _publish(output_dir, "github", pr_number="18")
    binding_path = output_dir / "github_review_binding_18.json"
    assert {path.name for path in output_dir.iterdir()} == {
        "raw_findings_18.json",
        "diff_context_18.json",
        binding_path.name,
        receipt_path.name,
    }
    binding_bytes = binding_path.read_bytes()
    assert github["publication_records"][-1]["artifact"] == "github_review_binding"
    assert receipt_path.read_bytes() == receipt_bytes
    assert receipt_path.stat().st_ino == receipt_inode

    local = _publish(output_dir, "local", pr_number="18", written_at="t1")
    assert {path.name for path in output_dir.iterdir()} == {
        "raw_findings_18.json",
        "diff_context_18.json",
        "local_findings_18.json",
        receipt_path.name,
    }
    assert local["publication_records"][-1]["artifact"] == "local_findings"
    assert receipt_path.read_bytes() == receipt_bytes
    assert receipt_path.stat().st_ino == receipt_inode

    binding_path.write_bytes(binding_bytes)
    stale = _publish(output_dir, "github", pr_number="18", fresh=False, written_at="t2")
    assert {path.name for path in output_dir.iterdir()} == {
        "raw_findings_18.json",
        receipt_path.name,
    }
    assert stale["publication_records"][-1]["artifact"] == "raw_findings"
    assert receipt_path.read_bytes() == receipt_bytes
    assert receipt_path.stat().st_ino == receipt_inode


def _combined_review_survivors(gate_state: str) -> list[dict[str, object]]:
    reachability, abstraction = EXPERIMENTAL_REVIEW_AUDITORS
    standard = [
        {
            "file": "src/app.py",
            "line": 40,
            "dimension": "bugs",
            "severity": "critical",
            "message": "Standard behavior regressed",
            "requires_decision": False,
        }
    ]
    deletion = [
        {
            "file": "src/deleted.py",
            "line": 7,
            "dimension": "deletion_regression",
            "severity": "critical",
            "message": "Deleted symbol was restored",
            "requires_decision": False,
        }
    ]
    experimental = [
        {
            "candidate_id": "reachability",
            "auditor_name": reachability,
            "original_index": 0,
            **_experimental_candidate("overengineering_reachability", line=42),
        },
        {
            "candidate_id": "abstraction",
            "auditor_name": abstraction,
            "original_index": 0,
            **_experimental_candidate(
                "overengineering_abstraction_surface",
                line=43,
            ),
        },
    ]
    eligible_experimental = experimental if gate_state == "valid_true" else []
    dispositions = [
        {
            "candidate_id": finding["candidate_id"],
            "disposition_id": f"disposition-{finding['candidate_id']}",
            "reason_code": "accepted",
        }
        for finding in eligible_experimental
    ]
    result = aggregate_combined_review_candidates(
        candidates=eligible_experimental,
        dispositions=dispositions,
        prior_resolved_findings=[],
        standard_findings=standard,
        deletion_findings=deletion,
        anchor_authority=_authority({"src/app.py": [40], "src/deleted.py": [7]}),
        snapshot={"head_sha": "head", "base_sha": "base"},
        review_root=str(Path.cwd()),
    )
    assert result["state"] == "complete"
    return [dict(finding) for finding in result["survivors"]]


@pytest.mark.parametrize(
    "invalid_finding",
    [
        {
            "file": "src/app.py",
            "line": "40",
            "dimension": "bugs",
            "severity": "critical",
            "message": "Malformed line",
            "requires_decision": False,
        },
        {
            "file": "../escape.py",
            "line": 40,
            "dimension": "bugs",
            "severity": "critical",
            "message": "Escaping path",
            "requires_decision": False,
        },
        {
            "file": "src/app.py",
            "line": 40,
            "dimension": "bugs",
            "severity": "critical",
            "message": "Unexpected field",
            "requires_decision": False,
            "opaque": True,
        },
    ],
)
def test_malformed_standard_finding_degrades_aggregate(
    invalid_finding: dict[str, object],
) -> None:
    valid_finding = {
        "file": "src/valid.py",
        "line": 7,
        "dimension": "tests",
        "severity": "warning",
        "message": "Valid sibling",
        "requires_decision": False,
    }
    result = aggregate_combined_review_candidates(
        candidates=[],
        dispositions=[],
        prior_resolved_findings=[],
        standard_findings=[valid_finding, invalid_finding],
        anchor_authority=_authority({"src/valid.py": [7], "src/app.py": [40]}),
        snapshot={"head_sha": "head", "base_sha": "base"},
        review_root=str(Path.cwd()),
    )
    assert result["state"] == "degraded"
    assert [(item["file"], item["line"]) for item in result["survivors"]] == [("src/valid.py", 7)]
    assert result["validation_errors"]


@pytest.mark.parametrize(
    "malformed_finding",
    [
        {
            "file": "src/app.py",
            "line": "40",
            "dimension": "bugs",
            "severity": "warning",
            "message": "Non-integer line",
            "requires_decision": False,
        },
        {
            "file": "src/app.py",
            "line": 40,
            "dimension": "bugs",
            "severity": "warning",
            "message": "Closed-key violation",
            "requires_decision": False,
            "unexpected": True,
        },
    ],
)
def test_malformed_schema_still_degrades_but_unpostable_does_not(
    malformed_finding: dict[str, object],
) -> None:
    malformed = aggregate_combined_review_candidates(
        candidates=[],
        dispositions=[],
        prior_resolved_findings=[],
        standard_findings=[malformed_finding],
        anchor_authority=_authority({"src/app.py": [40]}),
        snapshot={"head_sha": "head", "base_sha": "base"},
        review_root=str(Path.cwd()),
    )
    assert malformed["state"] == "degraded"
    assert malformed["validation_errors"]

    out_of_authority = aggregate_combined_review_candidates(
        candidates=[],
        dispositions=[],
        prior_resolved_findings=[],
        standard_findings=[
            {
                "file": "src/app.py",
                "line": 41,
                "dimension": "bugs",
                "severity": "warning",
                "message": "Body-only finding",
                "requires_decision": False,
            }
        ],
        anchor_authority=_authority({"src/app.py": [40]}),
        snapshot={"head_sha": "head", "base_sha": "base"},
        review_root=str(Path.cwd()),
    )
    assert out_of_authority["state"] == "complete"
    assert out_of_authority["validation_errors"] == []
    assert out_of_authority["survivors"] == []
    assert out_of_authority["unpostable"][0]["admission_reason"] == ("REJECTED_LINE_NOT_IN_DIFF")


def test_mixed_batch_retains_valid_siblings_and_surfaces_rejects() -> None:
    findings = [
        {
            "file": "src/app.py",
            "line": 40,
            "dimension": "bugs",
            "severity": "critical",
            "message": "Admitted",
            "requires_decision": False,
        },
        {
            "file": "src/app.py",
            "line": 41,
            "dimension": "bugs",
            "severity": "warning",
            "message": "Body only",
            "requires_decision": False,
        },
    ]

    result = aggregate_combined_review_candidates(
        candidates=[],
        dispositions=[],
        prior_resolved_findings=[],
        anchor_authority=_authority({"src/app.py": [40]}),
        standard_findings=findings,
        snapshot={"head_sha": "head", "base_sha": "base"},
        review_root=str(Path.cwd()),
    )

    assert result["state"] == "complete"
    assert [(item["file"], item["line"], item["message"]) for item in result["survivors"]] == [
        ("src/app.py", 40, "Admitted")
    ]
    assert [
        (item["file"], item["line"], item["message"], item["admission_reason"])
        for item in result["unpostable"]
    ] == [("src/app.py", 41, "Body only", "REJECTED_LINE_NOT_IN_DIFF")]
    assert result["validation_errors"] == []

    rendered = render_unpostable_review_section(result["unpostable"])
    assert rendered.startswith("## Outside Diff Range")
    assert "src/app.py:41" in rendered
    assert "REJECTED_LINE_NOT_IN_DIFF" in rendered

    publication = prepare_experimental_review_publication(
        raw_ledger={},
        survivors=result["survivors"],
        unpostable=result["unpostable"],
        snapshot={"head_sha": "head", "base_sha": "base"},
        annotation_generation_id="generation",
        mode="local",
        snapshot_is_fresh=True,
    )
    raw_findings = publication["artifacts"]["raw_findings"]
    assert raw_findings["unpostable"][0]["admission_reason"] == ("REJECTED_LINE_NOT_IN_DIFF")
    assert raw_findings["outside_diff_range"] == rendered


def test_standard_finding_closed_key_error_lists_missing_and_extra() -> None:
    finding = {
        "file": "src/app.py",
        "line": 40,
        "dimension": "bugs",
        "severity": "warning",
        "requires_decision": False,
        "unexpected": True,
    }

    result = aggregate_combined_review_candidates(
        candidates=[],
        dispositions=[],
        prior_resolved_findings=[],
        standard_findings=[finding],
        anchor_authority=_authority({"src/app.py": [40]}),
        snapshot={"head_sha": "head", "base_sha": "base"},
        review_root=str(Path.cwd()),
    )

    assert result["validation_errors"] == [
        "standard[0]: finding has invalid closed keys: missing=['message']; extra=['unexpected']"
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [("dimension", []), ("severity", {})],
)
def test_standard_review_findings_reject_unhashable_enum_values(
    field: str,
    value: object,
) -> None:
    finding = {
        "file": "src/app.py",
        "line": 40,
        "dimension": "bugs",
        "severity": "critical",
        "message": "Malformed enum value",
        "requires_decision": False,
    }
    finding[field] = value

    result = aggregate_combined_review_candidates(
        candidates=[],
        dispositions=[],
        prior_resolved_findings=[],
        standard_findings=[finding],
        anchor_authority=_authority({"src/app.py": [40]}),
        snapshot={"head_sha": "head", "base_sha": "base"},
        review_root=str(Path.cwd()),
    )

    assert result["state"] == "degraded"
    assert result["survivors"] == []
    assert result["validation_errors"] == [f"standard[0]: {field} must be a non-empty string"]


def test_authoritative_empty_map_rejects_without_hunk_range_fallback() -> None:
    finding = {
        "file": "src/app.py",
        "line": 40,
        "dimension": "bugs",
        "severity": "warning",
        "message": "Valid hunk-range finding",
        "requires_decision": False,
    }

    result = aggregate_combined_review_candidates(
        candidates=[],
        dispositions=[],
        prior_resolved_findings=[],
        standard_findings=[finding],
        anchor_authority=_authority({}),
        snapshot={"head_sha": "head", "base_sha": "base"},
        review_root=str(Path.cwd()),
    )

    assert result["state"] == "complete"
    assert result["survivors"] == []
    assert [item["admission_reason"] for item in result["unpostable"]] == [
        "REJECTED_PATH_NOT_IN_DIFF"
    ]
    assert result["validation_errors"] == []


def test_unavailable_authority_produces_body_only_disposition() -> None:
    finding = {
        "file": "src/app.py",
        "line": 40,
        "dimension": "bugs",
        "severity": "warning",
        "message": "Authority was unavailable",
        "requires_decision": False,
    }

    result = aggregate_combined_review_candidates(
        candidates=[],
        dispositions=[],
        prior_resolved_findings=[],
        standard_findings=[finding],
        anchor_authority=DiffAnchorAuthority.unavailable(
            repository="openai/autoskillit",
            pr_number=1,
            head_sha="a" * 40,
        ),
        snapshot={"head_sha": "head", "base_sha": "base"},
        review_root=str(Path.cwd()),
    )

    assert result["state"] == "complete"
    assert result["survivors"] == []
    assert [item["admission_reason"] for item in result["unpostable"]] == [
        "REJECTED_AUTHORITY_UNAVAILABLE"
    ]
    assert result["validation_errors"] == []


def test_standard_review_findings_require_snapshot_authority() -> None:
    result = aggregate_combined_review_candidates(
        candidates=[],
        dispositions=[],
        prior_resolved_findings=[],
        standard_findings=[
            {
                "file": "src/app.py",
                "line": 40,
                "dimension": "bugs",
                "severity": "critical",
                "message": "Missing snapshot",
                "requires_decision": False,
            }
        ],
        anchor_authority=_authority({"src/app.py": [40]}),
        review_root=str(Path.cwd()),
    )
    assert result["state"] == "degraded"
    assert result["survivors"] == []
    assert result["validation_errors"] == ["snapshot head/base authority must be non-empty"]


@pytest.mark.parametrize(
    ("gate_state", "expected_dimensions"),
    [
        (
            "valid_true",
            {
                "bugs",
                "deletion_regression",
                "overengineering_reachability",
                "overengineering_abstraction_surface",
            },
        ),
        ("valid_false", {"bugs", "deletion_regression"}),
        ("degraded", {"bugs", "deletion_regression"}),
    ],
)
def test_combined_findings_survive_local_publication_for_every_gate_state(
    tmp_path: Path,
    gate_state: str,
    expected_dimensions: set[str],
) -> None:
    survivors = _combined_review_survivors(gate_state)
    publication = prepare_experimental_review_publication(
        raw_ledger={
            "candidate_records": survivors,
            "verdict_use_records": [
                {"candidate_id": item["candidate_id"], "used": True} for item in survivors
            ],
        },
        survivors=survivors,
        snapshot={"head_sha": "head", "base_sha": "base", "merge_base_sha": "merge"},
        annotation_generation_id="annotation",
        mode="local",
        snapshot_is_fresh=True,
        handoff_metadata={
            "summary": "AutoSkillit review",
            "verdict": "changes_requested",
            "pr_number": 17,
            "iteration": 2,
            "schema_version": 1,
        },
    )
    result = publish_experimental_review_artifacts(
        publication=publication,
        output_dir=str(tmp_path / gate_state),
        pr_number="17",
    )

    local_document = json.loads(Path(result["published_paths"]["local_findings"]).read_text())
    findings = local_document["findings"]
    assert {finding["dimension"] for finding in findings} == expected_dimensions
    assert all(finding["path"] == finding["file"] for finding in findings)
    assert all(finding["body"] == render_review_finding_body(finding) for finding in findings)
    assert local_document["iteration"] == 2
    assert local_document["verdict"] == "changes_requested"
    standard_finding = next(finding for finding in findings if finding["dimension"] == "bugs")
    assert standard_finding["record_digest"]
    assert standard_finding["snapshot"] == {"head_sha": "head", "base_sha": "base"}


def test_github_binding_marker_is_published_last_and_binds_server_receipt(
    tmp_path: Path,
) -> None:
    survivors = _combined_review_survivors("valid_true")
    ledger = {
        "candidate_records": survivors,
        "verdict_use_records": [
            {"candidate_id": item["candidate_id"], "verdict": "changes_requested"}
            for item in survivors
        ],
    }
    output_dir = tmp_path / "github"
    receipt, receipt_path = _write_server_receipt(
        output_dir,
        pr_number=23,
        head_sha=_REVIEW_HEAD,
    )
    receipt_bytes = receipt_path.read_bytes()
    receipt_inode = receipt_path.stat().st_ino
    metadata = {"pr_number": 23, "schema_version": 1}
    snapshot = {
        "head_sha": _REVIEW_HEAD,
        "base_sha": _REVIEW_BASE,
        "merge_base_sha": "c" * 40,
    }
    publication = prepare_experimental_review_publication(
        raw_ledger=ledger,
        survivors=survivors,
        snapshot=snapshot,
        annotation_generation_id="annotation",
        mode="github",
        snapshot_is_fresh=True,
        handoff_metadata=metadata,
    )
    generation_id = publication["artifacts"]["raw_findings"]["review_generation_id"]

    assert publication["artifact_order"] == [
        "raw_findings",
        "diff_context",
    ]
    result = publish_experimental_review_artifacts(
        publication=publication,
        output_dir=str(output_dir),
        pr_number="23",
    )

    assert [Path(record["path"]).name for record in result["publication_records"]] == [
        "raw_findings_23.json",
        "diff_context_23.json",
        "github_review_binding_23.json",
    ]
    documents = {
        name: json.loads(Path(path).read_text())
        for name, path in result["published_paths"].items()
    }
    assert set(documents) == {"raw_findings", "diff_context", "github_review_binding"}
    assert {document["review_generation_id"] for document in documents.values()} == {generation_id}
    consumer_index = {
        (entry["path"], entry["line"]): entry
        for entry in documents["diff_context"]["context_entries"]
    }
    assert ("src/app.py", 42) in consumer_index
    assert ("src/app.py", 43) in consumer_index
    assert {consumer_index[("src/app.py", line)]["dimension"] for line in (42, 43)} == {
        "overengineering_reachability",
        "overengineering_abstraction_surface",
    }
    assert consumer_index[("src/app.py", 42)]["disposition_id"] == ("disposition-reachability")
    binding = documents["github_review_binding"]
    assert binding == {
        **{
            key: documents["diff_context"][key]
            for key in _GENERATION_IDENTITY_KEYS
            if key in documents["diff_context"]
        },
        "receipt_operation_key": receipt.operation_key,
    }
    assert receipt_path.read_bytes() == receipt_bytes
    assert receipt_path.stat().st_ino == receipt_inode


@pytest.mark.parametrize(
    ("receipt_case", "expected_error"),
    [
        ("missing", "receipt unreadable"),
        ("invalid_json", "receipt malformed"),
        ("duplicate_keys", "receipt malformed"),
        ("empty", "receipt malformed"),
        ("overflow_created_at", "receipt malformed"),
        ("dry_run", "receipt is not a final successful review"),
        ("missing_review_id", "receipt is not a final successful review"),
        ("different_pr", "receipt is not a final successful review"),
        ("different_head", "receipt head does not match the review generation"),
        ("noncanonical_pr", "pr_number must be canonical decimal"),
    ],
)
def test_github_publication_rejects_unbindable_receipt(
    tmp_path: Path,
    receipt_case: str,
    expected_error: str,
) -> None:
    output_dir = tmp_path / "github"
    pr_number = "042" if receipt_case == "noncanonical_pr" else "42"
    if receipt_case == "invalid_json":
        _write_receipt_payload(output_dir, b"not json")
    elif receipt_case == "empty":
        _write_receipt_payload(output_dir, b"{}")
    elif receipt_case not in {"missing", "noncanonical_pr"}:
        receipt_pr = 43 if receipt_case == "different_pr" else 42
        receipt_head = "c" * 40 if receipt_case == "different_head" else _REVIEW_HEAD
        payload = authoritative_receipt(
            pr_number=receipt_pr,
            head_sha=receipt_head,
        ).to_dict()
        if receipt_case == "overflow_created_at":
            payload["created_at"] = int("9" * 400)
        elif receipt_case == "dry_run":
            payload["state"] = "DRY_RUN"
        elif receipt_case == "missing_review_id":
            payload["review_id"] = None
        data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        if receipt_case == "duplicate_keys":
            data = b'{"pr_number":42,' + data[1:]
        _write_receipt_payload(output_dir, data)

    with pytest.raises(ValueError, match=f"^{re.escape(expected_error)}"):
        publish_experimental_review_artifacts(
            publication=_prepared_publication("github"),
            output_dir=str(output_dir),
            pr_number=pr_number,
        )


@pytest.mark.parametrize("mode_state", ["missing", "unsupported"])
def test_github_publication_rejects_invalid_mode_before_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode_state: str,
) -> None:
    import autoskillit.smoke_utils.review._publication as publication_module

    publication = _prepared_publication("github")
    if mode_state == "missing":
        publication.pop("mode")
    else:
        publication["mode"] = "unsupported"
    writes: list[str] = []
    replacements: list[str] = []
    original_write = publication_module._write_temp_bytes
    original_replace = os.replace

    def recording_write(directory: Path, final_name: str, content: bytes) -> Path:
        writes.append(final_name)
        return original_write(directory, final_name, content)

    def recording_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]):
        replacements.append(Path(destination).name)
        original_replace(source, destination)

    monkeypatch.setattr(publication_module, "_write_temp_bytes", recording_write)
    monkeypatch.setattr(publication_module.os, "replace", recording_replace)
    output_dir = tmp_path / "github"

    with pytest.raises(ValueError, match="mode"):
        publish_experimental_review_artifacts(
            publication=publication,
            output_dir=str(output_dir),
            pr_number="42",
        )

    assert writes == []
    assert replacements == []
    assert not output_dir.exists()


@pytest.mark.parametrize(
    ("artifact", "corruption"),
    [("diff_context", "missing"), ("github_review_binding", "malformed")],
)
def test_github_handoff_reports_unreadable_artifact(
    tmp_path: Path,
    artifact: str,
    corruption: str,
) -> None:
    from autoskillit.smoke_utils import github_review_handoff_error

    output_dir = tmp_path / "github"
    _write_server_receipt(output_dir)
    result = _publish(output_dir, "github")
    artifact_path = Path(result["published_paths"][artifact])
    if corruption == "missing":
        artifact_path.unlink()
    else:
        artifact_path.write_text("not json")

    error = github_review_handoff_error(output_dir=str(output_dir), pr_number="42")

    assert error is not None
    assert error.startswith(f"{artifact} unreadable:")
