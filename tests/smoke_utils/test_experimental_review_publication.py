"""Smoke-utils tests relocated from the former monolith."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from autoskillit.smoke_utils import (
    EXPERIMENTAL_REVIEW_AUDITORS,
    aggregate_combined_review_candidates,
    prepare_experimental_review_publication,
    publish_experimental_review_artifacts,
    render_review_finding_body,
    validate_experimental_auditor_outputs,
)
from tests.smoke_utils._experimental_helpers import (
    _experimental_candidate,
)

pytestmark = [pytest.mark.medium]


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
        valid_diff_lines={"src/app.py": [42, 43]},
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


def _prepared_local_experimental_publication() -> dict[str, object]:
    return prepare_experimental_review_publication(
        raw_ledger={"candidate_records": [{"candidate_id": "candidate-1"}]},
        survivors=[
            {
                "candidate_id": "candidate-1",
                "disposition_id": "disposition-1",
                "file": "src/app.py",
                "line": 42,
            }
        ],
        snapshot={
            "head_sha": "head",
            "base_sha": "base",
            "merge_base_sha": "merge",
            "diff_sha256": "diff",
        },
        annotation_generation_id="annotation-generation",
        mode="local",
        snapshot_is_fresh=True,
    )


def test_experimental_publication_retires_obsolete_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "review-output"
    base_arguments = {
        "raw_ledger": {"candidate_records": [{"candidate_id": "candidate-1"}]},
        "survivors": [{"file": "src/app.py", "line": 42}],
        "snapshot": {"head_sha": "head", "base_sha": "base"},
        "annotation_generation_id": "annotation-generation",
    }

    local = prepare_experimental_review_publication(
        **base_arguments,
        mode="local",
        snapshot_is_fresh=True,
    )
    publish_experimental_review_artifacts(
        publication=local,
        output_dir=str(output_dir),
        pr_number="18",
    )
    assert {path.name for path in output_dir.iterdir()} == {
        "raw_findings_18.json",
        "diff_context_18.json",
        "local_findings_18.json",
    }

    github_with_receipt = prepare_experimental_review_publication(
        **base_arguments,
        mode="github",
        snapshot_is_fresh=True,
        receipt={"posted": True, "http_status": 200},
    )
    publish_experimental_review_artifacts(
        publication=github_with_receipt,
        output_dir=str(output_dir),
        pr_number="18",
    )
    assert {path.name for path in output_dir.iterdir()} == {
        "raw_findings_18.json",
        "diff_context_18.json",
        "batch_review_response_18.json",
    }

    github_without_receipt = prepare_experimental_review_publication(
        **base_arguments,
        mode="github",
        snapshot_is_fresh=True,
    )
    publish_experimental_review_artifacts(
        publication=github_without_receipt,
        output_dir=str(output_dir),
        pr_number="18",
    )
    assert {path.name for path in output_dir.iterdir()} == {
        "raw_findings_18.json",
        "diff_context_18.json",
    }

    stale = prepare_experimental_review_publication(
        **base_arguments,
        mode="github",
        snapshot_is_fresh=False,
    )
    publish_experimental_review_artifacts(
        publication=stale,
        output_dir=str(output_dir),
        pr_number="18",
    )
    assert {path.name for path in output_dir.iterdir()} == {"raw_findings_18.json"}


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
        valid_diff_lines={"src/app.py": [40], "src/deleted.py": [7]},
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
            "line": 41,
            "dimension": "bugs",
            "severity": "critical",
            "message": "Unchanged line",
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
def test_standard_review_findings_degrade_atomically(
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
        valid_diff_lines={"src/valid.py": [7], "src/app.py": [40]},
        snapshot={"head_sha": "head", "base_sha": "base"},
        review_root=str(Path.cwd()),
    )
    assert result["state"] == "degraded"
    assert result["survivors"] == []
    assert result["validation_errors"]


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
        valid_diff_lines={"src/app.py": [40]},
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
        valid_diff_lines={"src/app.py": [40]},
        snapshot={"head_sha": "head", "base_sha": "base"},
        review_root=str(Path.cwd()),
    )

    assert result["state"] == "degraded"
    assert result["survivors"] == []
    assert result["validation_errors"] == [f"standard[0]: {field} must be a non-empty string"]


def test_standard_review_findings_accept_hunk_range_fallback() -> None:
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
        valid_diff_lines={},
        valid_line_ranges={"src/app.py": [[38, 42]]},
        snapshot={"head_sha": "head", "base_sha": "base"},
        review_root=str(Path.cwd()),
    )

    assert result["state"] == "complete"
    assert [item["line"] for item in result["survivors"]] == [40]
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
        valid_diff_lines={"src/app.py": [40]},
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


def test_github_receipt_shares_prederived_generation_and_is_published_last(
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
    metadata = {"pr_number": 23, "schema_version": 1}
    snapshot = {"head_sha": "head", "base_sha": "base", "merge_base_sha": "merge"}
    seed = prepare_experimental_review_publication(
        raw_ledger=ledger,
        survivors=survivors,
        snapshot=snapshot,
        annotation_generation_id="annotation",
        mode="github",
        snapshot_is_fresh=True,
        handoff_metadata=metadata,
    )
    generation_id = seed["artifacts"]["raw_findings"]["review_generation_id"]
    publication = prepare_experimental_review_publication(
        raw_ledger=ledger,
        survivors=survivors,
        snapshot=snapshot,
        annotation_generation_id="annotation",
        mode="github",
        snapshot_is_fresh=True,
        handoff_metadata=metadata,
        receipt={
            "posted": True,
            "http_status": 200,
            "commit_id": "head",
            "review_generation_id": "must-be-overridden",
        },
    )

    assert publication["artifact_order"] == [
        "raw_findings",
        "diff_context",
        "review_receipt",
    ]
    assert publication["artifacts"]["review_receipt"]["review_generation_id"] == generation_id
    result = publish_experimental_review_artifacts(
        publication=publication,
        output_dir=str(tmp_path / "github"),
        pr_number="23",
    )

    assert [Path(record["path"]).name for record in result["publication_records"]] == [
        "raw_findings_23.json",
        "diff_context_23.json",
        "batch_review_response_23.json",
    ]
    documents = {
        name: json.loads(Path(path).read_text())
        for name, path in result["published_paths"].items()
    }
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
    assert documents["review_receipt"]["commit_id"] == "head"


def test_write_temp_bytes_closes_descriptor_when_fdopen_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import autoskillit.smoke_utils._experimental_review as experimental_review

    real_close = os.close
    opened_fd = -1
    closed_fds: list[int] = []

    def failing_fdopen(fd: int, mode: str) -> None:
        nonlocal opened_fd
        opened_fd = fd
        raise OSError("injected fdopen failure")

    def recording_close(fd: int) -> None:
        closed_fds.append(fd)
        real_close(fd)

    monkeypatch.setattr(experimental_review.os, "fdopen", failing_fdopen)
    monkeypatch.setattr(experimental_review.os, "close", recording_close)

    with pytest.raises(OSError, match="injected fdopen failure"):
        experimental_review._write_temp_bytes(tmp_path, "artifact.json", b"payload")

    assert closed_fds == [opened_fd]
    assert not list(tmp_path.iterdir())


def test_experimental_publication_executes_same_directory_marker_last_renames(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import autoskillit.smoke_utils._experimental_review as experimental_review

    publication = _prepared_local_experimental_publication()
    output_dir = tmp_path / "review-output"
    real_replace = os.replace
    rename_calls: list[tuple[Path, Path]] = []

    def recording_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]):
        rename_calls.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(experimental_review.os, "replace", recording_replace)
    result = publish_experimental_review_artifacts(
        publication=publication,
        output_dir=str(output_dir),
        pr_number="17",
    )

    expected_names = [
        "raw_findings_17.json",
        "diff_context_17.json",
        "local_findings_17.json",
    ]
    assert [destination.name for _, destination in rename_calls] == expected_names
    assert all(
        source.parent == destination.parent == output_dir for source, destination in rename_calls
    )
    assert all(source.name.endswith(".tmp") for source, _ in rename_calls)
    assert list(result["published_paths"]) == [
        "raw_findings",
        "diff_context",
        "local_findings",
    ]
    for artifact_name, path in result["published_paths"].items():
        assert json.loads(Path(path).read_text()) == publication["artifacts"][artifact_name]
    assert result["publication_records"][-1]["artifact"] == "local_findings"
    assert not list(output_dir.glob(".*.tmp"))


@pytest.mark.parametrize("failure_index", [0, 1, 2])
def test_experimental_publication_rolls_back_each_write_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_index: int,
) -> None:
    import autoskillit.smoke_utils._experimental_review as experimental_review

    publication = _prepared_local_experimental_publication()
    output_dir = tmp_path / "review-output"
    output_dir.mkdir()
    final_paths = [
        output_dir / "raw_findings_18.json",
        output_dir / "diff_context_18.json",
        output_dir / "local_findings_18.json",
    ]
    for index, path in enumerate(final_paths):
        path.write_text(f"old-{index}")
    original_write = experimental_review._write_temp_bytes
    write_index = 0

    def failing_write(directory: Path, final_name: str, content: bytes) -> Path:
        nonlocal write_index
        current_index = write_index
        write_index += 1
        if current_index == failure_index:
            raise OSError("injected write failure")
        return original_write(directory, final_name, content)

    monkeypatch.setattr(experimental_review, "_write_temp_bytes", failing_write)
    with pytest.raises(RuntimeError, match="publication failed"):
        publish_experimental_review_artifacts(
            publication=publication,
            output_dir=str(output_dir),
            pr_number="18",
        )

    assert [path.read_text() for path in final_paths] == ["old-0", "old-1", "old-2"]
    assert not list(output_dir.glob(".*.tmp"))


@pytest.mark.parametrize("failure_index", [0, 1, 2])
def test_experimental_publication_rolls_back_each_rename_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_index: int,
) -> None:
    import autoskillit.smoke_utils._experimental_review as experimental_review

    publication = _prepared_local_experimental_publication()
    output_dir = tmp_path / "review-output"
    output_dir.mkdir()
    final_paths = [
        output_dir / "raw_findings_19.json",
        output_dir / "diff_context_19.json",
        output_dir / "local_findings_19.json",
    ]
    for index, path in enumerate(final_paths):
        path.write_text(f"old-{index}")
    real_replace = os.replace
    rename_index = 0
    failure_injected = False

    def failing_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]):
        nonlocal failure_injected, rename_index
        if not failure_injected:
            current_index = rename_index
            rename_index += 1
            if current_index == failure_index:
                failure_injected = True
                raise OSError("injected rename failure")
        real_replace(source, destination)

    monkeypatch.setattr(experimental_review.os, "replace", failing_replace)
    with pytest.raises(RuntimeError, match="publication failed"):
        publish_experimental_review_artifacts(
            publication=publication,
            output_dir=str(output_dir),
            pr_number="19",
        )

    assert [path.read_text() for path in final_paths] == ["old-0", "old-1", "old-2"]
    assert not list(output_dir.glob(".*.tmp"))


def test_experimental_publication_preserves_publication_and_rollback_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import autoskillit.smoke_utils._experimental_review as experimental_review

    publication = _prepared_local_experimental_publication()
    output_dir = tmp_path / "review-output"
    output_dir.mkdir()
    final_paths = [
        output_dir / "raw_findings_20.json",
        output_dir / "diff_context_20.json",
        output_dir / "local_findings_20.json",
    ]
    for index, path in enumerate(final_paths):
        path.write_text(f"old-{index}")
    real_replace = os.replace
    replace_count = 0

    def failing_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]):
        nonlocal replace_count
        replace_count += 1
        if replace_count == 2:
            raise OSError("injected publication failure")
        if replace_count == 3:
            raise OSError("injected rollback failure")
        real_replace(source, destination)

    monkeypatch.setattr(experimental_review.os, "replace", failing_replace)
    with pytest.raises(ExceptionGroup) as exc_info:
        publish_experimental_review_artifacts(
            publication=publication,
            output_dir=str(output_dir),
            pr_number="20",
        )

    errors = exc_info.value.exceptions
    assert [str(error) for error in errors] == [
        "injected publication failure",
        "injected rollback failure",
    ]
    assert [path.read_text() for path in final_paths[1:]] == ["old-1", "old-2"]
