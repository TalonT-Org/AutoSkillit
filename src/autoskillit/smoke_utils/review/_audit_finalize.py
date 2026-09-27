"""Re-evaluate the audit source and derive the only publishable review verdict."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast

from autoskillit.core import ChildTaskTranscript
from autoskillit.core.io import atomic_write
from autoskillit.smoke_utils.review._aggregation import (
    aggregate_combined_review_candidates,
    determine_experimental_review_verdict,
)
from autoskillit.smoke_utils.review._audit_collection import evaluate_review_audit_slots
from autoskillit.smoke_utils.review._audit_manifest import (
    ReviewAuditInputError,
    load_review_audit_anchor_authority,
    load_review_audit_manifest,
)
from autoskillit.smoke_utils.review._publication import render_review_finding_body

REVIEW_DISPOSITION_REASON_CODES = (
    "accepted",
    "schema_invalid",
    "path_escape",
    "not_changed_line",
    "stale_snapshot",
    "insufficient_evidence",
    "boundary_unchecked",
    "reachable_counterexample",
    "simpler_behavior_not_equivalent",
    "suppressed_prior_thread",
    "duplicate_candidate",
    "publication_failed",
)

_DISPOSITION_FIELDS = {"candidate_id", "reason_code", "explanation"}


def _disposition_records(
    *,
    audit_run_id: str,
    candidates: Sequence[Mapping[str, object]],
    dispositions: Sequence[Mapping[str, object]],
) -> tuple[list[dict[str, object]], list[str]]:
    if not candidates:
        return [], []
    candidate_ids = {str(candidate.get("candidate_id", "")) for candidate in candidates}
    counts: Counter[str] = Counter()
    valid: list[dict[str, object]] = []
    errors: list[str] = []
    for index, item in enumerate(dispositions):
        if not isinstance(item, Mapping) or set(item) != _DISPOSITION_FIELDS:
            errors.append(f"disposition {index} has an invalid field set")
            continue
        candidate_id = item.get("candidate_id")
        reason_code = item.get("reason_code")
        explanation = item.get("explanation")
        if not isinstance(candidate_id, str) or candidate_id not in candidate_ids:
            errors.append(f"disposition {index} references an unknown candidate")
            continue
        counts[candidate_id] += 1
        if reason_code not in REVIEW_DISPOSITION_REASON_CODES:
            errors.append(f"disposition {index} has an invalid reason_code")
            continue
        if not isinstance(explanation, str) or len(explanation.encode("utf-8")) > 1024:
            errors.append(f"disposition {index} explanation must be at most 1 KiB of UTF-8")
            continue
        payload = {
            "audit_run_id": audit_run_id,
            "candidate_id": candidate_id,
            "reason_code": reason_code,
            "explanation": explanation,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        valid.append({**dict(item), "disposition_id": digest})
    for candidate_id in sorted(candidate_ids):
        if counts[candidate_id] == 0:
            errors.append(f"candidate {candidate_id} has no disposition")
        elif counts[candidate_id] > 1:
            errors.append(f"candidate {candidate_id} has duplicate dispositions")
    return valid, errors


def _candidate_ledger_records(
    candidates: Sequence[Mapping[str, object]],
    survivors: Sequence[Mapping[str, object]],
    unpostable: Sequence[Mapping[str, object]],
    aggregation_records: Sequence[Mapping[str, object]],
) -> list[dict[str, str]]:
    by_id: dict[str, str] = {}
    for item in (*candidates, *survivors, *unpostable):
        candidate_id = item.get("candidate_id")
        record_digest = item.get("record_digest")
        if isinstance(candidate_id, str) and isinstance(record_digest, str):
            by_id[candidate_id] = record_digest
    result = [
        {"candidate_id": candidate_id, "record_digest": record_digest}
        for candidate_id, record_digest in by_id.items()
    ]
    for record in aggregation_records:
        candidate_id = record.get("candidate_id")
        if isinstance(candidate_id, str) and candidate_id in by_id:
            ledger_record = {"candidate_id": candidate_id, "record_digest": by_id[candidate_id]}
            if ledger_record not in result:
                result.append(ledger_record)
    return result


def finalize_review_audit(
    *,
    manifest_path: str,
    handles: Mapping[str, str],
    dispositions: Sequence[Mapping[str, object]],
    prior_resolved_findings: Sequence[Mapping[str, object]],
    final_snapshot_state: str,
    read_child_task: Callable[[str], ChildTaskTranscript | None],
) -> dict[str, object]:
    """Revalidate transcripts, aggregate accepted findings, and derive verdict."""
    if final_snapshot_state not in {"fresh", "stale", "authority_degraded"}:
        raise ReviewAuditInputError("final_snapshot_state is invalid")
    manifest = load_review_audit_manifest(manifest_path)
    evaluated = evaluate_review_audit_slots(
        manifest=manifest, handles=handles, read_child_task=read_child_task
    )
    slot_records = evaluated["slot_records"]
    assert isinstance(slot_records, list)
    unvalidated = [
        {
            key: value
            for key, value in record.items()
            if key not in {"findings", "malformed_envelope"}
        }
        for record in slot_records
        if record.get("status") != "validated"
    ]
    candidate_mappings = cast(list[Mapping[str, object]], evaluated["experimental_candidates"])
    experimental_unpostable = cast(
        list[Mapping[str, object]], evaluated["experimental_unpostable"]
    )
    standard_findings = cast(list[Mapping[str, object]], evaluated["standard_findings"])
    deletion_findings = cast(list[Mapping[str, object]], evaluated["deletion_findings"])
    server_dispositions, disposition_errors = _disposition_records(
        audit_run_id=str(manifest["audit_run_id"]),
        candidates=candidate_mappings,
        dispositions=dispositions,
    )
    anchor_authority = load_review_audit_anchor_authority(manifest)
    aggregation = aggregate_combined_review_candidates(
        candidates=candidate_mappings,
        dispositions=server_dispositions,
        prior_resolved_findings=prior_resolved_findings,
        anchor_authority=anchor_authority,
        standard_findings=standard_findings,
        deletion_findings=deletion_findings,
        snapshot=cast(Mapping[str, str], manifest["snapshot"]),
        review_root=str(manifest["review_root"]),
    )
    survivors = cast(list[Mapping[str, object]], aggregation.get("survivors", []))
    aggregate_unpostable = cast(list[Mapping[str, object]], aggregation.get("unpostable", []))
    aggregation_records = cast(
        list[Mapping[str, object]], aggregation.get("aggregation_records", [])
    )
    all_unpostable = [*aggregate_unpostable, *experimental_unpostable]
    experimental_state = str(evaluated["experimental_state"])
    if experimental_state == "failed":
        final_experimental_state = "degraded"
    else:
        final_experimental_state = experimental_state
    audit_state = (
        "degraded"
        if (
            unvalidated
            or disposition_errors
            or aggregation.get("state") == "degraded"
            or final_experimental_state in {"failed", "degraded"}
        )
        else "complete"
    )
    findings_for_verdict = [*survivors, *all_unpostable]
    verdict = determine_experimental_review_verdict(
        retained_snapshot_was_valid=manifest["gate_state"] in {"valid_true", "valid_false"},
        final_snapshot_is_fresh=final_snapshot_state == "fresh",
        gate_state=str(manifest["gate_state"]),
        experimental_audit_state=audit_state,
        findings=findings_for_verdict,
    )
    if final_snapshot_state == "authority_degraded":
        verdict = "needs_human"
    rendered_survivors = [
        {**dict(item), "rendered_body": render_review_finding_body(item)} for item in survivors
    ]
    auditor_records = [
        {
            key: value
            for key, value in record.items()
            if key not in {"findings", "malformed_envelope"}
        }
        for record in slot_records
    ]
    malformed_envelopes = [
        record["malformed_envelope"] for record in slot_records if "malformed_envelope" in record
    ]
    candidate_records = _candidate_ledger_records(
        candidates=candidate_mappings,
        survivors=survivors,
        unpostable=all_unpostable,
        aggregation_records=aggregation_records,
    )
    ledger_records = {
        "candidate_records": candidate_records,
        "validation_records": auditor_records,
        "disposition_records": server_dispositions,
        "aggregation_records": aggregation_records,
        "malformed_envelopes": malformed_envelopes,
    }
    finalization = {
        "verdict": verdict,
        "audit_state": audit_state,
        "gate_state": manifest["gate_state"],
        "experimental_audit_state": final_experimental_state,
        "auditor_records": auditor_records,
        "unvalidated": unvalidated,
        "disposition_errors": disposition_errors,
        "survivors": rendered_survivors,
        "unpostable": all_unpostable,
        "review_level_findings": [],
        "aggregation_records": aggregation_records,
        "ledger_records": ledger_records,
    }
    path = Path(manifest_path).with_name(
        f"review_audit_finalization_{manifest['audit_run_id']}.json"
    )
    atomic_write(
        path, json.dumps(finalization, sort_keys=True, indent=2) + "\n", strict_durability=True
    )
    return {**finalization, "finalization_path": str(path)}
