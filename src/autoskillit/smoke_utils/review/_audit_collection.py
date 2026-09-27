"""Validate auditor transcripts and record bounded relaunch history."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from autoskillit.core import (
    ChildTaskTranscript,
    get_logger,
    is_valid_child_task_id,
    read_versioned_json,
)
from autoskillit.smoke_utils.review._audit_manifest import (
    REVIEW_AUDIT_SCHEMA_VERSION,
    ReviewAuditInputError,
    _write_review_audit_artifact,
    load_review_audit_anchor_authority,
    load_review_audit_manifest,
)
from autoskillit.smoke_utils.review._auditor_output import parse_auditor_findings_output
from autoskillit.smoke_utils.review._constants import _STANDARD_REVIEW_DIMENSIONS, _bounded_utf8
from autoskillit.smoke_utils.review._validation import (
    _review_finding_schema_error,
    build_malformed_review_envelope,
    validate_experimental_auditor_outputs,
)

logger = get_logger(__name__)


def _slot_failure(
    slot: Mapping[str, object],
    *,
    handle: str,
    reason_code: str,
    detail: str,
    transcript: ChildTaskTranscript | None = None,
    findings: list[object] | None = None,
    output_form: str = "",
    closing_fence_complete: bool = True,
) -> dict[str, object]:
    record: dict[str, object] = {
        **_slot_identity(slot),
        "handle": handle,
        "transcript_locator": transcript.transcript_locator if transcript else "",
        "status": "failed",
        "reason_code": reason_code,
        "detail": _bounded_utf8(detail, 1024),
        "final_stop_reason": transcript.final_stop_reason if transcript else "",
        "output_limit_stops": transcript.output_limit_stops if transcript else 0,
        "output_form": output_form,
        "closing_fence_complete": closing_fence_complete,
        "finding_count": 0,
    }
    if findings is not None:
        record["findings"] = findings
    if transcript is not None:
        raw_output = transcript.final_text or ""
        record["malformed_envelope"] = build_malformed_review_envelope(
            producer=str(slot["producer"]),
            terminal_status=transcript.final_stop_reason or "unknown",
            raw_output=raw_output,
            errors=[detail],
            rejection_reason=reason_code,
        )
    return record


def _slot_identity(slot: Mapping[str, object]) -> dict[str, object]:
    return {key: slot[key] for key in ("slot_id", "kind", "dimension", "producer", "slot_token")}


def _load_bound_transcript(
    slot: Mapping[str, object],
    handle: str,
    duplicate_count: int,
    read_child_task: Callable[[str], ChildTaskTranscript | None],
) -> tuple[ChildTaskTranscript | None, dict[str, object] | None]:
    if not handle:
        return None, _slot_failure(
            slot,
            handle=handle,
            reason_code="missing_handle",
            detail="no child handle was supplied",
        )
    if duplicate_count > 1:
        return None, _slot_failure(
            slot,
            handle=handle,
            reason_code="duplicate_handle",
            detail="child handle is shared by multiple slots",
        )
    if not is_valid_child_task_id(handle):
        return None, _slot_failure(
            slot, handle=handle, reason_code="invalid_handle", detail="child handle is invalid"
        )
    try:
        transcript = read_child_task(handle)
    except Exception as exc:
        logger.warning(
            "failed to read review-audit child transcript for %s",
            handle,
            exc_info=True,
        )
        return None, _slot_failure(
            slot,
            handle=handle,
            reason_code="transcript_unreadable",
            detail=f"transcript read failed: {type(exc).__name__}",
        )
    if transcript is None:
        return None, _slot_failure(
            slot,
            handle=handle,
            reason_code="transcript_not_found",
            detail="child transcript was not found",
        )
    marker = str(slot["marker_line"])
    prompt_lines = {line.strip() for line in transcript.assignment_prompt.splitlines()}
    if marker not in prompt_lines and transcript.assignment_label != slot["slot_token"]:
        return transcript, _slot_failure(
            slot,
            handle=handle,
            reason_code="slot_binding_mismatch",
            detail="transcript assignment does not bind to this slot",
            transcript=transcript,
        )
    return transcript, None


def _standard_schema_error(
    slot: Mapping[str, object], findings: list[object], review_root: Path
) -> tuple[str, str] | None:
    for finding in findings:
        error = _review_finding_schema_error(
            finding,
            deletion_only=slot.get("kind") == "deletion",
            allowed_dimensions={str(slot["dimension"])},
            review_root=review_root,
        )
        if error is not None:
            return error
    return None


def _evaluate_terminal_transcript(
    slot: Mapping[str, object],
    handle: str,
    transcript: ChildTaskTranscript,
    review_root: Path,
) -> tuple[dict[str, object], list[object] | None]:
    if not transcript.terminal:
        return _slot_failure(
            slot,
            handle=handle,
            reason_code="not_terminal",
            detail="child transcript is not terminal",
            transcript=transcript,
        ), None
    if transcript.output_limit_stops > 0:
        return _slot_failure(
            slot,
            handle=handle,
            reason_code="output_limit_reached",
            detail="child output reached its configured limit",
            transcript=transcript,
        ), None
    parsed = parse_auditor_findings_output(transcript.final_text)
    if not parsed["ok"]:
        notes = cast(list[str], parsed["notes"])
        return _slot_failure(
            slot,
            handle=handle,
            reason_code=str(parsed["reason_code"]),
            detail=str(parsed["reason_code"]),
            transcript=transcript,
            output_form=str(parsed["form"]),
            closing_fence_complete="closing_fence_incomplete" not in notes,
        ), None
    findings = cast(list[object], parsed["findings"])
    if slot.get("kind") in {"standard", "deletion"}:
        error = _standard_schema_error(slot, findings, review_root)
        if error is not None:
            reason, detail = error
            return _slot_failure(
                slot,
                handle=handle,
                reason_code=reason,
                detail=detail,
                transcript=transcript,
                output_form=str(parsed["form"]),
            ), None
    record = {
        **_slot_identity(slot),
        "handle": handle,
        "transcript_locator": transcript.transcript_locator,
        "status": "validated",
        "reason_code": "accepted",
        "detail": "",
        "final_stop_reason": transcript.final_stop_reason,
        "output_limit_stops": transcript.output_limit_stops,
        "output_form": parsed["form"],
        "closing_fence_complete": "closing_fence_incomplete"
        not in cast(list[str], parsed["notes"]),
        "findings": findings,
        "finding_count": len(findings),
    }
    return record, findings


def _experimental_outputs(
    slots: list[Mapping[str, object]],
    records_by_slot: Mapping[str, dict[str, object]],
    findings_by_slot: Mapping[str, list[object]],
) -> dict[str, dict[str, object]]:
    outputs: dict[str, dict[str, object]] = {}
    for slot in slots:
        slot_id = str(slot["slot_id"])
        if records_by_slot[slot_id]["status"] == "validated":
            outputs[str(slot["producer"])] = {
                "terminal_status": "success",
                "output": findings_by_slot[slot_id],
            }
    return outputs


def _apply_experimental_validation(
    slots: list[Mapping[str, object]],
    records_by_slot: Mapping[str, dict[str, object]],
    validation: Mapping[str, object],
) -> None:
    statuses = cast(Mapping[str, Mapping[str, str]], validation["status_by_name"])
    envelopes = cast(list[dict[str, object]], validation["malformed_envelopes"])
    envelopes_by_producer = {str(item["producer"]): item for item in envelopes}
    for slot in slots:
        record = records_by_slot[str(slot["slot_id"])]
        status = statuses[str(slot["producer"])]
        if record["status"] == "validated" and status["status"] != "success":
            reason = status["reason_code"]
            record.update(status="failed", reason_code=reason, detail=_bounded_utf8(reason, 1024))
            record.pop("findings", None)
            record["finding_count"] = 0
        if record["status"] != "validated" and "malformed_envelope" not in record:
            envelope = envelopes_by_producer.get(str(slot["producer"]))
            if envelope is not None:
                record["malformed_envelope"] = envelope


def _validate_experimental_slots(
    slots: list[Mapping[str, object]],
    records: list[dict[str, object]],
    findings_by_slot: Mapping[str, list[object]],
    manifest: Mapping[str, object],
) -> tuple[list[object], list[object], str]:
    records_by_slot = {str(record["slot_id"]): record for record in records}
    outputs = _experimental_outputs(slots, records_by_slot, findings_by_slot)
    validation = validate_experimental_auditor_outputs(
        outputs=outputs,
        anchor_authority=load_review_audit_anchor_authority(manifest),
        snapshot=cast(Mapping[str, str], manifest["snapshot"]),
        review_root=cast(str, manifest["review_root"]),
    )
    _apply_experimental_validation(slots, records_by_slot, validation)
    if validation["state"] == "complete" and all(
        records_by_slot[str(slot["slot_id"])]["status"] == "validated" for slot in slots
    ):
        return (
            cast(list[object], validation["candidates"]),
            cast(list[object], validation["unpostable"]),
            "complete",
        )
    return [], [], "failed"


def _validated_findings(
    records: list[dict[str, object]], kind: str, dimension: str | None = None
) -> list[object]:
    findings: list[object] = []
    for record in records:
        if (
            record.get("kind") == kind
            and (dimension is None or record.get("dimension") == dimension)
            and record.get("status") == "validated"
        ):
            findings.extend(cast(list[object], record["findings"]))
    return findings


def evaluate_review_audit_slots(
    *,
    manifest: Mapping[str, object],
    handles: Mapping[str, str],
    read_child_task: Callable[[str], ChildTaskTranscript | None],
) -> dict[str, object]:
    """Evaluate current transcripts against the manifest's required slots."""
    raw_slots = manifest.get("slots")
    if not isinstance(raw_slots, list) or not all(isinstance(slot, Mapping) for slot in raw_slots):
        raise ReviewAuditInputError("manifest slots must be an array of objects")
    slots = cast(list[Mapping[str, object]], raw_slots)
    slot_ids = {str(slot.get("slot_id", "")) for slot in slots}
    if not set(handles).issubset(slot_ids) or any(
        not isinstance(value, str) for value in handles.values()
    ):
        raise ReviewAuditInputError("handles must map known slot ids to strings")

    by_handle = Counter(value for value in handles.values() if value)
    records: list[dict[str, object]] = []
    parsed_findings: dict[str, list[object]] = {}
    review_root = Path(cast(str, manifest["review_root"]))
    if not review_root.is_absolute():
        raise ReviewAuditInputError("manifest review_root must be absolute")
    for slot in slots:
        slot_id = str(slot["slot_id"])
        handle = handles.get(slot_id, "")
        transcript, failure = _load_bound_transcript(
            slot, handle, by_handle[handle] if handle else 0, read_child_task
        )
        if failure is not None:
            records.append(failure)
            continue
        assert transcript is not None
        record, findings = _evaluate_terminal_transcript(slot, handle, transcript, review_root)
        records.append(record)
        if findings is not None:
            parsed_findings[slot_id] = findings

    experimental_slots = [slot for slot in slots if slot.get("kind") == "experimental"]
    if experimental_slots:
        candidates, unpostable, experimental_state = _validate_experimental_slots(
            experimental_slots, records, parsed_findings, manifest
        )
    else:
        candidates = []
        unpostable = []
        experimental_state = str(manifest.get("experimental_audit_state", "degraded"))
    standard_findings = [
        finding
        for dimension in _STANDARD_REVIEW_DIMENSIONS
        for finding in _validated_findings(records, "standard", dimension)
    ]
    deletion_findings = _validated_findings(records, "deletion")
    return {
        "slot_records": records,
        "standard_findings": standard_findings,
        "deletion_findings": deletion_findings,
        "experimental_candidates": candidates,
        "experimental_unpostable": unpostable,
        "experimental_state": experimental_state,
    }


def _read_ledger(path: Path, audit_run_id: str) -> dict[str, object]:
    try:
        value = read_versioned_json(
            path, REVIEW_AUDIT_SCHEMA_VERSION, logger=logger, raise_io_errors=True
        )
    except OSError as exc:
        raise ReviewAuditInputError("review-audit ledger is unreadable") from exc
    if value is None:
        if not path.exists():
            return {"audit_run_id": audit_run_id, "slots": {}}
        raise ReviewAuditInputError("review-audit ledger schema is invalid")
    if value.get("audit_run_id") != audit_run_id or not isinstance(value.get("slots"), dict):
        raise ReviewAuditInputError("review-audit ledger shape is invalid")
    return value


def _update_slot_ledger(ledger_slots: dict[str, object], record: dict[str, object]) -> None:
    slot_id = str(record["slot_id"])
    entry = ledger_slots.setdefault(
        slot_id, {"handles": [], "records_by_handle": {}, "failed_rounds": 0}
    )
    if not isinstance(entry, dict):
        raise ReviewAuditInputError("review-audit ledger slot shape is invalid")
    seen_handles = entry.setdefault("handles", [])
    records_by_handle = entry.setdefault("records_by_handle", {})
    if not isinstance(seen_handles, list) or not isinstance(records_by_handle, dict):
        raise ReviewAuditInputError("review-audit ledger slot history is invalid")
    handle = str(record.get("handle", ""))
    if handle:
        if handle not in seen_handles:
            seen_handles.append(handle)
        records_by_handle[handle] = {
            key: value
            for key, value in record.items()
            if key not in {"findings", "malformed_envelope"}
        }
    if record.get("status") == "failed":
        entry["failed_rounds"] = int(str(entry.get("failed_rounds", 0))) + 1


def _update_audit_ledger(
    ledger_slots: dict[str, object], records: list[dict[str, object]]
) -> None:
    for record in records:
        _update_slot_ledger(ledger_slots, record)


def _mark_exhausted_slots(
    slots: list[Mapping[str, object]],
    ledger_slots: Mapping[str, object],
    records_by_id: Mapping[str, dict[str, object]],
    max_attempts: int,
) -> bool:
    exhausted_experimental = False
    for slot in slots:
        slot_id = str(slot["slot_id"])
        record = records_by_id[slot_id]
        entry = cast(dict[str, object], ledger_slots[slot_id])
        attempts = int(str(entry["failed_rounds"]))
        if record.get("status") == "failed" and attempts >= max_attempts:
            record["status"] = "exhausted"
            if slot.get("kind") == "experimental":
                exhausted_experimental = True
    return exhausted_experimental


def _relaunch_entry(
    slot: Mapping[str, object],
    record: Mapping[str, object],
    ledger_slots: Mapping[str, object],
    *,
    reason_code: object | None = None,
) -> dict[str, object]:
    entry = cast(dict[str, object], ledger_slots[str(slot["slot_id"])])
    return {
        "slot_id": slot["slot_id"],
        "dimension": slot["dimension"],
        "kind": slot["kind"],
        "slot_token": slot["slot_token"],
        "marker_line": slot["marker_line"],
        "reason_code": record["reason_code"] if reason_code is None else reason_code,
        "attempts_used": entry["failed_rounds"],
    }


def _experimental_relaunches(
    slots: list[Mapping[str, object]],
    records_by_id: Mapping[str, dict[str, object]],
    ledger_slots: Mapping[str, object],
    *,
    exhausted_experimental: bool,
    default_state: str,
) -> tuple[list[dict[str, object]], str]:
    if not slots:
        return [], default_state
    failed = any(records_by_id[str(slot["slot_id"])]["status"] != "validated" for slot in slots)
    if exhausted_experimental:
        return [], "failed"
    if not failed:
        return [], "complete"
    relaunch = [
        _relaunch_entry(
            slot,
            records_by_id[str(slot["slot_id"])],
            ledger_slots,
            reason_code=(
                records_by_id[str(slot["slot_id"])]["reason_code"]
                if records_by_id[str(slot["slot_id"])]["status"] == "failed"
                else "fixed_set_relaunch"
            ),
        )
        for slot in slots
        if records_by_id[str(slot["slot_id"])]["status"] != "exhausted"
    ]
    return relaunch, "failed"


def _standard_relaunches(
    slots: list[Mapping[str, object]],
    records_by_id: Mapping[str, dict[str, object]],
    ledger_slots: Mapping[str, object],
) -> list[dict[str, object]]:
    return [
        _relaunch_entry(slot, records_by_id[str(slot["slot_id"])], ledger_slots)
        for slot in slots
        if slot.get("kind") != "experimental"
        and records_by_id[str(slot["slot_id"])].get("status") == "failed"
    ]


def _collection_state(
    records: list[dict[str, object]], relaunch: list[dict[str, object]], experimental_state: str
) -> str:
    if all(
        record.get("status") == "validated" for record in records
    ) and experimental_state not in {
        "failed",
        "degraded",
    }:
        return "complete"
    return "relaunch_required" if relaunch else "degraded"


def _public_slot_records(records: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            key: value
            for key, value in record.items()
            if key not in {"findings", "malformed_envelope"}
        }
        for record in records
    ]


def collect_review_audit(
    *,
    manifest_path: str,
    handles: Mapping[str, str],
    read_child_task: Callable[[str], ChildTaskTranscript | None],
) -> dict[str, object]:
    """Evaluate, persist relaunch history, and return slots needing another round.

    The ledger read-modify-write assumes sequential calls for an audit_run_id;
    review-pr invokes each collection after its preceding relaunch wave. Finalize
    always re-evaluates transcript sources, so ledger state cannot validate a slot.
    """
    manifest = load_review_audit_manifest(manifest_path)
    evaluated = evaluate_review_audit_slots(
        manifest=manifest, handles=handles, read_child_task=read_child_task
    )
    audit_run_id = str(manifest["audit_run_id"])
    path = Path(manifest_path).with_name(f"review_audit_ledger_{audit_run_id}.json")
    ledger = _read_ledger(path, audit_run_id)
    ledger_slots = cast(dict[str, object], ledger["slots"])
    slot_records = cast(list[dict[str, object]], evaluated["slot_records"])
    _update_audit_ledger(ledger_slots, slot_records)

    raw_slots = cast(list[Mapping[str, object]], manifest["slots"])
    records_by_id = {str(record["slot_id"]): record for record in slot_records}
    max_attempts = cast(int, manifest["max_attempts"])
    exhausted_experimental = _mark_exhausted_slots(
        raw_slots, ledger_slots, records_by_id, max_attempts
    )
    experimental_slots = [slot for slot in raw_slots if slot.get("kind") == "experimental"]
    experimental_relaunch, experimental_state = _experimental_relaunches(
        experimental_slots,
        records_by_id,
        ledger_slots,
        exhausted_experimental=exhausted_experimental,
        default_state=str(manifest.get("experimental_audit_state", "degraded")),
    )
    relaunch = [
        *experimental_relaunch,
        *_standard_relaunches(raw_slots, records_by_id, ledger_slots),
    ]

    _write_review_audit_artifact(path, ledger)
    state = _collection_state(slot_records, relaunch, experimental_state)
    return {
        "audit_run_id": audit_run_id,
        "state": state,
        "slots": _public_slot_records(slot_records),
        "relaunch": relaunch,
        "experimental_candidates": evaluated["experimental_candidates"],
        "experimental_unpostable": evaluated["experimental_unpostable"],
        "ledger_path": str(path),
    }
