from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from functools import partial
from pathlib import Path

import pytest

from autoskillit.core import ChildTaskTranscript, DiffAnchorAuthority, write_versioned_json
from autoskillit.smoke_utils import (
    REVIEW_DISPOSITION_REASON_CODES,
    ReviewAuditInputError,
    collect_review_audit,
    evaluate_review_audit_slots,
    finalize_review_audit,
    load_review_audit_manifest,
    plan_review_audit,
)
from autoskillit.smoke_utils.review._audit_manifest import (
    _content_digest,
    load_review_audit_anchor_authority,
)
from tests.smoke_utils._experimental_helpers import _experimental_candidate, _finding

pytestmark = [pytest.mark.medium]

HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40
MERGE_SHA = "c" * 40
_candidate = partial(_experimental_candidate, file="src/review.py", line=10)


def _run(
    tmp_path: Path,
    *,
    gate_state: str = "valid_true",
    dispatch_agents: object = None,
    anchor: bool = False,
    deletion: bool = False,
) -> tuple[Path, dict[str, object]]:
    review = tmp_path / "review"
    snapshot_dir = review / "gate_snapshot.x"
    root = tmp_path / "repo"
    snapshot_dir.mkdir(parents=True)
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "review.py").write_text("line\n" * 12)
    metrics_path = snapshot_dir / "metrics.json"
    diff_path = snapshot_dir / "annotated_diff.txt"
    lines_path = snapshot_dir / "valid_lines.json"
    metrics: dict[str, object] = {"_head_sha": HEAD_SHA, "dispatch_agents": dispatch_agents}
    metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
    diff_path.write_text("snapshot header\n[L1]+review\n", encoding="utf-8")
    lines_path.write_text(json.dumps({"src/review.py": [10, 11]}), encoding="utf-8")
    authority_path = snapshot_dir / "gate_authority.json"
    authority = {
        "state": gate_state,
        "reason_code": "none",
        "experimental_audit_state": "pending" if gate_state == "valid_true" else "not_required",
        "snapshot": {
            "head_sha": HEAD_SHA,
            "base_sha": BASE_SHA,
            "merge_base_sha": MERGE_SHA,
            "base_repo_full_name": "acme/repo",
            "diff_sha256": hashlib.sha256(b"diff").hexdigest(),
            "profile_id": "local_git_pinned_v1",
        },
        "annotation_generation_id": "generation-1",
        "authority_path": str(authority_path),
        "snapshot_dir": str(snapshot_dir),
        "metrics_marker_snapshot_path": str(metrics_path),
        "annotated_diff_snapshot_path": str(diff_path),
        "hunk_ranges_snapshot_path": str(snapshot_dir / "hunk_ranges.json"),
        "valid_lines_snapshot_path": str(lines_path),
        "mode": "local",
        "checkout_root": str(root),
        "pr_number": "7",
        "diff_metrics_path": str(snapshot_dir / "metrics-source.json"),
        "annotated_diff_path": str(snapshot_dir / "diff-source.txt"),
        "hunk_ranges_path": str(snapshot_dir / "ranges-source.json"),
        "valid_lines_path": str(snapshot_dir / "lines-source.json"),
    }
    authority_path.write_text(json.dumps(authority), encoding="utf-8")
    anchor_path = ""
    if anchor:
        anchor_file = review / "anchor_authority_7.json"
        anchor_value = DiffAnchorAuthority.authoritative(
            repository="acme/repo",
            pr_number=7,
            head_sha=HEAD_SHA,
            generation_id="generation-1",
            right_side_lines={"src/review.py": [10, 11]},
            left_side_lines={},
        )
        anchor_file.write_text(json.dumps(anchor_value.to_wire()), encoding="utf-8")
        anchor_path = str(anchor_file)
    plan = plan_review_audit(
        authority_path=str(authority_path),
        review_output_dir=str(review),
        deletion_merge_base=MERGE_SHA if deletion else "",
        anchor_authority_path=anchor_path,
        repository="acme/repo" if not anchor else "",
    )
    return review, plan


def _transcripts(
    manifest: dict[str, object],
    *,
    outputs: dict[str, str] | None = None,
    limited: set[str] | None = None,
) -> tuple[dict[str, str], dict[str, ChildTaskTranscript]]:
    output_by_slot = outputs or {}
    limited_slots = limited or set()
    handles: dict[str, str] = {}
    transcripts: dict[str, ChildTaskTranscript] = {}
    for index, slot in enumerate(manifest["slots"]):
        slot_id = str(slot["slot_id"])
        handle = f"child-{index:02d}"
        handles[slot_id] = handle
        transcripts[handle] = ChildTaskTranscript(
            child_id=handle,
            transcript_locator=f"test://{handle}",
            assignment_prompt=f"audit task\n{slot['marker_line']}\n",
            assignment_label="",
            terminal=True,
            final_text=output_by_slot.get(slot_id, "[]"),
            final_stop_reason="success",
            output_limit_stops=1 if slot_id in limited_slots else 0,
        )
    return handles, transcripts


def test_plan_derives_slots_and_manifest_integrity(tmp_path: Path) -> None:
    review, planned = _run(
        tmp_path,
        dispatch_agents=["tests", "cohesion"],
        deletion=True,
    )
    assert [slot["slot_id"] for slot in planned["slots"]] == [
        "tests",
        "cohesion",
        "deletion_regression",
        "overengineering_reachability",
        "overengineering_abstraction_surface",
    ]
    tokens = [slot["slot_token"] for slot in planned["slots"]]
    assert len(tokens) == len(set(tokens))
    assert all(re.fullmatch(r"rva[0-9a-f]{16}[0-9]{2}", token) for token in tokens)
    manifest = load_review_audit_manifest(str(planned["manifest_path"]))
    assert manifest["audit_run_id"] == planned["audit_run_id"]
    assert load_review_audit_anchor_authority(manifest) == DiffAnchorAuthority.unavailable(
        repository="acme/repo", pr_number=7, head_sha=HEAD_SHA
    )
    data = Path(str(planned["manifest_path"]))
    data.write_text(data.read_text().replace('"mode": "local"', '"mode": "Local"'))
    with pytest.raises(ReviewAuditInputError, match="digest"):
        load_review_audit_manifest(str(data))

    authority = review / "gate_snapshot.x" / "gate_authority.json"
    next_plan = plan_review_audit(
        authority_path=str(authority),
        review_output_dir=str(review),
        deletion_merge_base=MERGE_SHA,
        repository="acme/repo",
    )
    assert next_plan["audit_run_id"] != planned["audit_run_id"]
    assert Path(str(next_plan["manifest_path"])).is_file()


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [("audit_run_id", None), ("gate_state", []), ("snapshot", [])],
)
def test_manifest_rejects_invalid_finalization_fields(
    tmp_path: Path, field: str, invalid_value: object
) -> None:
    _, planned = _run(tmp_path)
    manifest_path = str(planned["manifest_path"])
    manifest = load_review_audit_manifest(manifest_path)
    manifest[field] = invalid_value
    manifest["content_sha256"] = _content_digest(manifest)
    write_versioned_json(Path(manifest_path), manifest, schema_version=manifest["schema_version"])
    with pytest.raises(ReviewAuditInputError, match=field):
        load_review_audit_manifest(manifest_path)


@pytest.mark.parametrize("anchor", [False, True])
def test_manifest_rejects_changed_gate_or_anchor_authority(tmp_path: Path, anchor: bool) -> None:
    _, planned = _run(tmp_path, anchor=anchor)
    manifest_path = str(planned["manifest_path"])
    manifest = load_review_audit_manifest(manifest_path)
    authority_key = "anchor_authority_path" if anchor else "authority_path"
    authority = Path(str(manifest[authority_key]))
    authority.write_text(authority.read_text() + " ")
    with pytest.raises(ReviewAuditInputError, match="authority digest changed"):
        load_review_audit_manifest(manifest_path)


@pytest.mark.parametrize(
    ("gate_state", "dispatch", "expected_count", "expected_audit"),
    [
        ("valid_false", ["bogus"], 6, "not_required"),
        ("degraded", None, 6, "degraded"),
    ],
)
def test_plan_falls_back_to_standard_allowlist_and_gate_state(
    tmp_path: Path, gate_state: str, dispatch: object, expected_count: int, expected_audit: str
) -> None:
    _, planned = _run(tmp_path, gate_state=gate_state, dispatch_agents=dispatch)
    assert len(planned["slots"]) == expected_count
    assert planned["experimental_audit_state"] == expected_audit


def test_plan_rejects_missing_non_degraded_snapshots_and_bad_authority_paths(
    tmp_path: Path,
) -> None:
    review, planned = _run(tmp_path)
    authority = review / "gate_snapshot.x" / "gate_authority.json"
    current = json.loads(authority.read_text())
    Path(current["metrics_marker_snapshot_path"]).unlink()
    with pytest.raises(ReviewAuditInputError, match="missing snapshot"):
        plan_review_audit(authority_path=str(authority), review_output_dir=str(review))
    current["state"] = "degraded"
    authority.write_text(json.dumps(current))
    degraded = plan_review_audit(
        authority_path=str(authority), review_output_dir=str(review), repository="acme/repo"
    )
    assert degraded["experimental_audit_state"] == "degraded"
    with pytest.raises(ReviewAuditInputError, match="inside review_output_dir"):
        plan_review_audit(
            authority_path=str(tmp_path / "outside.json"), review_output_dir=str(review)
        )
    with pytest.raises(ReviewAuditInputError, match="repository"):
        plan_review_audit(
            authority_path=str(authority), review_output_dir=str(review), repository=""
        )
    assert Path(str(planned["manifest_path"])).exists()


def test_incident_malformed_and_limited_children_never_approve(tmp_path: Path) -> None:
    _, planned = _run(tmp_path, deletion=True)
    manifest = load_review_audit_manifest(str(planned["manifest_path"]))
    handles, transcripts = _transcripts(
        manifest,
        outputs={"arch": "[", "deletion_regression": "[", "bugs": "[]"},
        limited={"bugs"},
    )
    collect = collect_review_audit(
        manifest_path=str(planned["manifest_path"]),
        handles=handles,
        read_child_task=transcripts.get,
    )
    assert collect["state"] == "relaunch_required"
    assert {row["slot_id"] for row in collect["relaunch"]} == {
        "arch",
        "deletion_regression",
        "bugs",
    }
    assert {row["reason_code"] for row in collect["relaunch"]} == {
        "malformed_json",
        "output_limit_reached",
    }
    final = finalize_review_audit(
        manifest_path=str(planned["manifest_path"]),
        handles=handles,
        dispositions=[],
        prior_resolved_findings=[],
        final_snapshot_state="fresh",
        read_child_task=transcripts.get,
    )
    assert final["audit_state"] == "degraded"
    assert final["verdict"] == "needs_human"
    assert final["verdict"] not in {"approved", "approved_with_comments"}


@pytest.mark.parametrize(
    ("transcript_state", "reason"),
    [("unreadable", "transcript_unreadable"), ("nonterminal", "not_terminal")],
)
def test_transcript_read_and_terminal_failures_are_not_validated(
    tmp_path: Path, transcript_state: str, reason: str
) -> None:
    _, planned = _run(tmp_path, gate_state="valid_false")
    manifest = load_review_audit_manifest(str(planned["manifest_path"]))
    handles, transcripts = _transcripts(manifest)
    arch_handle = handles["arch"]
    if transcript_state == "nonterminal":
        transcripts[arch_handle] = replace(transcripts[arch_handle], terminal=False)

    def read(child_id: str) -> ChildTaskTranscript | None:
        if transcript_state == "unreadable" and child_id == arch_handle:
            raise OSError("transcript source is unavailable")
        return transcripts.get(child_id)

    result = evaluate_review_audit_slots(manifest=manifest, handles=handles, read_child_task=read)
    arch = next(row for row in result["slot_records"] if row["slot_id"] == "arch")
    assert arch["status"] == "failed"
    assert arch["reason_code"] == reason


def test_well_formed_empty_findings_remain_valid(tmp_path: Path) -> None:
    _, planned = _run(tmp_path, gate_state="valid_false")
    manifest = load_review_audit_manifest(str(planned["manifest_path"]))
    handles, transcripts = _transcripts(manifest, outputs={"tests": "```json\n[]\n```"})
    collect = collect_review_audit(
        manifest_path=str(planned["manifest_path"]),
        handles=handles,
        read_child_task=transcripts.get,
    )
    assert collect["state"] == "complete"
    final = finalize_review_audit(
        manifest_path=str(planned["manifest_path"]),
        handles=handles,
        dispositions=[],
        prior_resolved_findings=[],
        final_snapshot_state="fresh",
        read_child_task=transcripts.get,
    )
    assert final["verdict"] == "approved"


def test_handle_binding_duplicate_and_unknown_key_checks(tmp_path: Path) -> None:
    _, planned = _run(tmp_path, gate_state="valid_false")
    manifest = load_review_audit_manifest(str(planned["manifest_path"]))
    handles, transcripts = _transcripts(manifest)
    arch_handle = handles["arch"]
    transcripts[arch_handle] = replace(
        transcripts[arch_handle], assignment_prompt="unbound prompt", assignment_label=""
    )
    binding = collect_review_audit(
        manifest_path=str(planned["manifest_path"]),
        handles=handles,
        read_child_task=transcripts.get,
    )
    unbound = next(row for row in binding["slots"] if row["slot_id"] == "arch")
    assert unbound["status"] == "failed"
    assert unbound["reason_code"] == "slot_binding_mismatch"
    handles["tests"] = arch_handle
    result = collect_review_audit(
        manifest_path=str(planned["manifest_path"]),
        handles=handles,
        read_child_task=transcripts.get,
    )
    reasons = {row["slot_id"]: row["reason_code"] for row in result["slots"]}
    assert reasons["arch"] == reasons["tests"] == "duplicate_handle"
    duplicate_invalid = dict(handles)
    duplicate_invalid["arch"] = duplicate_invalid["tests"] = "bad/handle"
    invalid_result = evaluate_review_audit_slots(
        manifest=manifest,
        handles=duplicate_invalid,
        read_child_task=transcripts.get,
    )
    invalid_reasons = {
        row["slot_id"]: row["reason_code"] for row in invalid_result["slot_records"]
    }
    assert invalid_reasons["arch"] == invalid_reasons["tests"] == "duplicate_handle"
    empty_handles = dict(handles)
    empty_handles["tests"] = empty_handles["cohesion"] = ""
    empty_result = evaluate_review_audit_slots(
        manifest=manifest,
        handles=empty_handles,
        read_child_task=transcripts.get,
    )
    empty_reasons = {row["slot_id"]: row["reason_code"] for row in empty_result["slot_records"]}
    assert empty_reasons["tests"] == empty_reasons["cohesion"] == "missing_handle"
    codex_handles, codex_transcripts = _transcripts(manifest)
    codex_slot = next(slot for slot in manifest["slots"] if slot["slot_id"] == "arch")
    codex_handle = codex_handles["arch"]
    codex_transcripts[codex_handle] = replace(
        codex_transcripts[codex_handle],
        assignment_prompt="",
        assignment_label=codex_slot["slot_token"],
    )
    codex_result = evaluate_review_audit_slots(
        manifest=manifest,
        handles=codex_handles,
        read_child_task=codex_transcripts.get,
    )
    assert (
        next(row for row in codex_result["slot_records"] if row["slot_id"] == "arch")["status"]
        == "validated"
    )
    with pytest.raises(ReviewAuditInputError, match="known slot"):
        collect_review_audit(
            manifest_path=str(planned["manifest_path"]),
            handles={**handles, "unknown": "child-z"},
            read_child_task=transcripts.get,
        )


@pytest.mark.parametrize(
    ("slot_id", "finding", "reason"),
    [
        ("arch", _finding(dimension="tests"), "schema_invalid"),
        ("arch", _finding(file="../outside.py"), "path_escape"),
    ],
)
def test_standard_findings_are_validated_per_slot(
    tmp_path: Path, slot_id: str, finding: dict[str, object], reason: str
) -> None:
    _, planned = _run(tmp_path, gate_state="valid_false")
    manifest = load_review_audit_manifest(str(planned["manifest_path"]))
    handles, transcripts = _transcripts(manifest, outputs={slot_id: json.dumps([finding])})
    result = collect_review_audit(
        manifest_path=str(planned["manifest_path"]),
        handles=handles,
        read_child_task=transcripts.get,
    )
    assert (
        next(row["reason_code"] for row in result["slots"] if row["slot_id"] == slot_id) == reason
    )


def test_deletion_findings_flow_through_aggregation(tmp_path: Path) -> None:
    _, planned = _run(tmp_path, gate_state="valid_false", anchor=True, deletion=True)
    manifest = load_review_audit_manifest(str(planned["manifest_path"]))
    deletion = _finding(dimension="deletion_regression")
    handles, transcripts = _transcripts(
        manifest, outputs={"deletion_regression": json.dumps([deletion])}
    )
    result = finalize_review_audit(
        manifest_path=str(planned["manifest_path"]),
        handles=handles,
        dispositions=[],
        prior_resolved_findings=[],
        final_snapshot_state="fresh",
        read_child_task=transcripts.get,
    )
    assert any(item["dimension"] == "deletion_regression" for item in result["survivors"])
    assert result["ledger_records"]["aggregation_records"] == []


def test_experimental_pair_relaunches_together_and_candidate_records_keep_digests(
    tmp_path: Path,
) -> None:
    _, planned = _run(tmp_path, anchor=True)
    manifest = load_review_audit_manifest(str(planned["manifest_path"]))
    outputs = {
        "overengineering_reachability": json.dumps([_candidate("overengineering_reachability")]),
        "overengineering_abstraction_surface": "[",
    }
    handles, transcripts = _transcripts(manifest, outputs=outputs)
    collection = collect_review_audit(
        manifest_path=str(planned["manifest_path"]),
        handles=handles,
        read_child_task=transcripts.get,
    )
    experiment_relaunch = [row for row in collection["relaunch"] if row["kind"] == "experimental"]
    assert len(experiment_relaunch) == 2
    assert (
        next(
            row for row in experiment_relaunch if row["slot_id"] == "overengineering_reachability"
        )["reason_code"]
        == "fixed_set_relaunch"
    )
    assert (
        next(
            row for row in experiment_relaunch if row["slot_id"] == "overengineering_reachability"
        )["attempts_used"]
        == 0
    )

    good_outputs = {
        "overengineering_reachability": json.dumps([_candidate("overengineering_reachability")]),
        "overengineering_abstraction_surface": json.dumps(
            [_candidate("overengineering_abstraction_surface")]
        ),
    }
    good_handles, good_transcripts = _transcripts(manifest, outputs=good_outputs)
    collected = collect_review_audit(
        manifest_path=str(planned["manifest_path"]),
        handles=good_handles,
        read_child_task=good_transcripts.get,
    )
    candidates = collected["experimental_candidates"]
    dispositions = [
        {
            "candidate_id": item["candidate_id"],
            "reason_code": "accepted",
            "explanation": "verified",
        }
        for item in candidates
    ]
    assert all(item["reason_code"] in REVIEW_DISPOSITION_REASON_CODES for item in dispositions)
    finalized = finalize_review_audit(
        manifest_path=str(planned["manifest_path"]),
        handles=good_handles,
        dispositions=dispositions,
        prior_resolved_findings=[],
        final_snapshot_state="fresh",
        read_child_task=good_transcripts.get,
    )
    records = finalized["ledger_records"]["candidate_records"]
    assert len(finalized["survivors"]) == 1
    duplicates = [
        row
        for row in finalized["aggregation_records"]
        if row["reason_code"] == "duplicate_candidate"
    ]
    assert len(duplicates) == 1
    loser = next(
        item for item in candidates if item["candidate_id"] == duplicates[0]["candidate_id"]
    )
    assert loser["candidate_id"] != finalized["survivors"][0]["candidate_id"]
    assert {
        "candidate_id": loser["candidate_id"],
        "record_digest": loser["record_digest"],
    } in records
    assert {item["candidate_id"] for item in records} == {
        item["candidate_id"] for item in candidates
    }
    assert {item["record_digest"] for item in records} == {
        item["record_digest"] for item in candidates
    }


def test_experimental_schema_failure_is_attributed_to_its_producer(tmp_path: Path) -> None:
    _, planned = _run(tmp_path, anchor=True)
    manifest = load_review_audit_manifest(str(planned["manifest_path"]))
    outputs = {
        "overengineering_reachability": json.dumps(
            [_candidate("overengineering_abstraction_surface")]
        ),
        "overengineering_abstraction_surface": "[]",
    }
    handles, transcripts = _transcripts(manifest, outputs=outputs)
    result = collect_review_audit(
        manifest_path=str(planned["manifest_path"]),
        handles=handles,
        read_child_task=transcripts.get,
    )
    reachability = next(
        row for row in result["slots"] if row["slot_id"] == "overengineering_reachability"
    )
    abstraction = next(
        row for row in result["slots"] if row["slot_id"] == "overengineering_abstraction_surface"
    )
    assert reachability["status"] == "failed"
    assert reachability["reason_code"] == "schema_invalid"
    assert abstraction["status"] == "validated"


def test_experimental_pair_stays_closed_after_one_slot_is_exhausted(
    tmp_path: Path,
) -> None:
    _, planned = _run(tmp_path, anchor=True)
    manifest = load_review_audit_manifest(str(planned["manifest_path"]))
    handles, transcripts = _transcripts(
        manifest, outputs={"overengineering_abstraction_surface": "["}
    )
    for _ in range(3):
        result = collect_review_audit(
            manifest_path=str(planned["manifest_path"]),
            handles=handles,
            read_child_task=transcripts.get,
        )
    abstraction = next(
        row for row in result["slots"] if row["slot_id"] == "overengineering_abstraction_surface"
    )
    assert abstraction["status"] == "exhausted"
    reachability_slot = next(
        slot for slot in manifest["slots"] if slot["slot_id"] == "overengineering_reachability"
    )
    fresh_handle = "reach-failed-after-exhaustion"
    handles["overengineering_reachability"] = fresh_handle
    transcripts[fresh_handle] = ChildTaskTranscript(
        child_id=fresh_handle,
        transcript_locator=f"test://{fresh_handle}",
        assignment_prompt=f"{reachability_slot['marker_line']}\n",
        assignment_label="",
        terminal=True,
        final_text="[",
        final_stop_reason="success",
        output_limit_stops=0,
    )
    result = collect_review_audit(
        manifest_path=str(planned["manifest_path"]),
        handles=handles,
        read_child_task=transcripts.get,
    )
    assert not any(row["kind"] == "experimental" for row in result["relaunch"])
    finalized = finalize_review_audit(
        manifest_path=str(planned["manifest_path"]),
        handles=handles,
        dispositions=[],
        prior_resolved_findings=[],
        final_snapshot_state="fresh",
        read_child_task=transcripts.get,
    )
    assert finalized["experimental_audit_state"] == "degraded"


@pytest.mark.parametrize(
    ("invalid_kind", "expected_error"),
    [
        ("malformed", "invalid reason_code"),
        ("unknown_candidate", "unknown candidate"),
        ("duplicate", "duplicate dispositions"),
        ("missing", "no disposition"),
        ("oversized", "at most 1 KiB"),
    ],
)
def test_dispositions_are_validated_before_identity_is_minted(
    tmp_path: Path,
    invalid_kind: str,
    expected_error: str,
) -> None:
    _, planned = _run(tmp_path, anchor=True)
    manifest = load_review_audit_manifest(str(planned["manifest_path"]))
    outputs = {
        "overengineering_reachability": json.dumps([_candidate("overengineering_reachability")]),
        "overengineering_abstraction_surface": json.dumps(
            [_candidate("overengineering_abstraction_surface", line=11)]
        ),
    }
    handles, transcripts = _transcripts(manifest, outputs=outputs)
    collection = collect_review_audit(
        manifest_path=str(planned["manifest_path"]),
        handles=handles,
        read_child_task=transcripts.get,
    )
    candidates = collection["experimental_candidates"]
    first = candidates[0]
    valid = [
        {"candidate_id": item["candidate_id"], "reason_code": "accepted", "explanation": "ok"}
        for item in candidates
    ]
    malformed = [
        {"candidate_id": first["candidate_id"], "reason_code": "unknown", "explanation": "x"},
        {"candidate_id": {"not": "hashable"}, "reason_code": [], "explanation": {}},
        {
            "candidate_id": candidates[1]["candidate_id"],
            "reason_code": "accepted",
            "explanation": "x",
            "disposition_id": "caller-controlled",
        },
    ]
    result = finalize_review_audit(
        manifest_path=str(planned["manifest_path"]),
        handles=handles,
        dispositions={
            "malformed": malformed,
            "unknown_candidate": [{**valid[0], "candidate_id": "unknown-cand"}, valid[1]],
            "duplicate": [valid[0], valid[0]],
            "missing": valid[:-1],
            "oversized": [{**valid[0], "explanation": "é" * 513}, valid[1]],
        }[invalid_kind],
        prior_resolved_findings=[],
        final_snapshot_state="fresh",
        read_child_task=transcripts.get,
    )
    assert result["disposition_errors"]
    assert any(expected_error in error for error in result["disposition_errors"])
    assert result["audit_state"] == "degraded"
    assert result["verdict"] == "needs_human"

    accepted = finalize_review_audit(
        manifest_path=str(planned["manifest_path"]),
        handles=handles,
        dispositions=valid,
        prior_resolved_findings=[],
        final_snapshot_state="fresh",
        read_child_task=transcripts.get,
    )
    records = accepted["ledger_records"]["disposition_records"]
    for record in records:
        payload = {
            "audit_run_id": planned["audit_run_id"],
            "candidate_id": record["candidate_id"],
            "reason_code": record["reason_code"],
            "explanation": record["explanation"],
        }
        expected_id = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert record["disposition_id"] == expected_id


def test_anchor_identity_is_bound_to_manifest_and_stale_snapshot_is_reported(
    tmp_path: Path,
) -> None:
    _, planned = _run(tmp_path, anchor=True)
    manifest_path = Path(str(planned["manifest_path"]))
    manifest = json.loads(manifest_path.read_text())
    manifest["repository"] = "other/repo"
    body = {key: value for key, value in manifest.items() if key != "content_sha256"}
    manifest["content_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ReviewAuditInputError, match="identity"):
        load_review_audit_anchor_authority(load_review_audit_manifest(str(manifest_path)))

    manifest["repository"] = "acme/repo"
    snapshot = manifest["snapshot"]
    assert isinstance(snapshot, dict)
    snapshot["head_sha"] = "d" * 40
    body = {key: value for key, value in manifest.items() if key != "content_sha256"}
    manifest["content_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ReviewAuditInputError, match="identity"):
        load_review_audit_anchor_authority(load_review_audit_manifest(str(manifest_path)))

    _, stale_plan = _run(tmp_path / "stale", gate_state="valid_false")
    stale_manifest = load_review_audit_manifest(str(stale_plan["manifest_path"]))
    handles, transcripts = _transcripts(stale_manifest)
    stale = finalize_review_audit(
        manifest_path=str(stale_plan["manifest_path"]),
        handles=handles,
        dispositions=[],
        prior_resolved_findings=[],
        final_snapshot_state="stale",
        read_child_task=transcripts.get,
    )
    assert stale["verdict"] == "stale_snapshot"


def test_attempt_bound_counts_rounds_and_snapshot_states_fail_closed(tmp_path: Path) -> None:
    _, planned = _run(tmp_path, gate_state="valid_false")
    manifest = load_review_audit_manifest(str(planned["manifest_path"]))
    handles, transcripts = _transcripts(manifest, outputs={"arch": "["})
    for _ in range(3):
        result = collect_review_audit(
            manifest_path=str(planned["manifest_path"]),
            handles=handles,
            read_child_task=transcripts.get,
        )
    arch = next(row for row in result["slots"] if row["slot_id"] == "arch")
    assert arch["status"] == "exhausted"
    assert not any(row["slot_id"] == "arch" for row in result["relaunch"])
    ledger = json.loads(Path(str(result["ledger_path"])).read_text())
    assert ledger["slots"]["arch"]["failed_rounds"] == 3
    final = finalize_review_audit(
        manifest_path=str(planned["manifest_path"]),
        handles=handles,
        dispositions=[],
        prior_resolved_findings=[],
        final_snapshot_state="authority_degraded",
        read_child_task=transcripts.get,
    )
    assert final["verdict"] == "needs_human"
    with pytest.raises(ReviewAuditInputError, match="final_snapshot_state"):
        finalize_review_audit(
            manifest_path=str(planned["manifest_path"]),
            handles=handles,
            dispositions=[],
            prior_resolved_findings=[],
            final_snapshot_state="bogus",
            read_child_task=transcripts.get,
        )


def test_attempt_bound_counts_distinct_failed_handles(tmp_path: Path) -> None:
    _, planned = _run(tmp_path, gate_state="valid_false")
    manifest = load_review_audit_manifest(str(planned["manifest_path"]))
    handles, transcripts = _transcripts(manifest)
    for attempt in range(3):
        failing_handle = f"arch-failed-{attempt}"
        arch_slot = next(slot for slot in manifest["slots"] if slot["slot_id"] == "arch")
        handles["arch"] = failing_handle
        transcripts[failing_handle] = ChildTaskTranscript(
            child_id=failing_handle,
            transcript_locator=f"test://{failing_handle}",
            assignment_prompt=f"{arch_slot['marker_line']}\n",
            assignment_label="",
            terminal=True,
            final_text="[",
            final_stop_reason="success",
            output_limit_stops=0,
        )
        result = collect_review_audit(
            manifest_path=str(planned["manifest_path"]),
            handles=handles,
            read_child_task=transcripts.get,
        )
    arch = next(row for row in result["slots"] if row["slot_id"] == "arch")
    ledger = json.loads(Path(str(result["ledger_path"])).read_text())
    assert arch["status"] == "exhausted"
    assert ledger["slots"]["arch"]["failed_rounds"] == 3
    assert ledger["slots"]["arch"]["handles"] == [
        "arch-failed-0",
        "arch-failed-1",
        "arch-failed-2",
    ]
