"""Independent boundary checks for the declared capture capacity envelope."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

import autoskillit.hooks._capture._artifact_setup as capture_artifact_setup
import autoskillit.hooks._capture._capacity as capture_capacity
import autoskillit.hooks._capture._lifecycle_policy as capture_lifecycle_policy
import autoskillit.hooks._capture._lifecycle_record as capture_lifecycle_record
import autoskillit.hooks._capture._reconcile as capture_reconcile
import autoskillit.hooks._capture._snapshot as capture_snapshot
import autoskillit.hooks._capture._types as capture_types
import autoskillit.hooks._capture_lifecycle as capture_lifecycle
from autoskillit.hooks._capture_artifacts import create_capture_artifact
from tests.hooks.test_capture_lifecycle import (
    _Clock,
    _close_artifact,
    _open_store,
    _verified_snapshot,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

_PROFILE_RECORDS = 976
_PROFILE_LOW_BYTES = 3_878_096
_PRE_ASSIST_RECORDS = 1231


def _capacity_spec(**changes: int) -> capture_types.CaptureCapacitySpec:
    base = replace(
        capture_types.CaptureCapacitySpec(),
        max_operational_records=_PRE_ASSIST_RECORDS,
        max_retained_records=_PRE_ASSIST_RECORDS,
        max_evidence_records=_PRE_ASSIST_RECORDS,
        compaction_low_bytes=_PROFILE_LOW_BYTES,
    )
    return replace(base, **changes)


def test_envelope_counts_tombstones_and_pre_assist_debt() -> None:
    spec = _capacity_spec()
    assert capture_types.PLANNED_RECORD_FRAME_BYTES == 2608
    assert (
        _PROFILE_RECORDS + spec.reclamation_debt_assist_records - 1 + spec.max_tombstones
    ) * capture_types.PLANNED_RECORD_FRAME_BYTES == _PROFILE_LOW_BYTES
    assert capture_types.capacity_envelope_violations(spec) == ()
    assert capture_types.capacity_envelope_violations(
        replace(spec, compaction_low_bytes=_PROFILE_LOW_BYTES - 1)
    ) == ("compaction_low_bytes",)

    added_tombstones = 3
    shifted_requirement = _PROFILE_LOW_BYTES + (
        added_tombstones * capture_types.PLANNED_RECORD_FRAME_BYTES
    )
    more_tombstones = replace(spec, max_tombstones=spec.max_tombstones + added_tombstones)
    assert capture_types.capacity_envelope_violations(
        replace(more_tombstones, compaction_low_bytes=shifted_requirement - 1)
    ) == ("compaction_low_bytes",)
    assert (
        capture_types.capacity_envelope_violations(
            replace(more_tombstones, compaction_low_bytes=shifted_requirement)
        )
        == ()
    )

    higher_assist = replace(spec, reclamation_debt_assist_records=257)
    assert set(capture_types.capacity_envelope_violations(higher_assist)) == {
        "compaction_low_bytes",
        "max_operational_records",
        "max_retained_records",
        "max_evidence_records",
    }


@pytest.mark.parametrize(
    "field",
    ("max_operational_records", "max_retained_records", "max_evidence_records"),
)
def test_record_caps_follow_the_same_envelope(field: str) -> None:
    changes = {field: _PRE_ASSIST_RECORDS - 1}
    if field == "max_operational_records":
        changes["max_retained_records"] = _PRE_ASSIST_RECORDS - 1
    assert field in capture_types.capacity_envelope_violations(_capacity_spec(**changes))


def test_one_hour_success_path_hold_violates_the_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(capture_lifecycle_policy, "REFERENCE_LIFETIME_SECONDS", 3600.0)
    assert capture_types.capacity_envelope_violations(capture_types.CaptureCapacitySpec())


def test_override_rejected_when_it_breaks_the_envelope() -> None:
    assert capture_artifact_setup._policy_capacity({"max_tombstones": 2000}) is None
    assert capture_artifact_setup._policy_capacity({"compaction_low_bytes": 1_000_000}) is None
    assert capture_artifact_setup._policy_capacity({"max_tombstones": 128}) is not None


def _finalize_template(store, root, clock: _Clock, capture_id: str, *, delivered: bool):
    artifact = create_capture_artifact(root, capture_id, store)
    try:
        data = b"capacity template"
        os.write(artifact.fd, data)
        finalized = store.commit_verified_snapshot(
            _verified_snapshot(store, artifact, data, clock),
            issue_reference=False,
        )
        if delivered:
            store.transition_delivery(
                finalized,
                expected=capture_lifecycle.CaptureDeliveryStatus.NOT_ATTEMPTED,
                target=capture_lifecycle.CaptureDeliveryStatus.ATTEMPTING,
            )
            return store.transition_delivery(
                finalized,
                expected=capture_lifecycle.CaptureDeliveryStatus.ATTEMPTING,
                target=capture_lifecycle.CaptureDeliveryStatus.DELIVERED,
            )
        return store.get_record(capture_id)
    finally:
        _close_artifact(artifact)


def _replicate_record(
    record: capture_lifecycle.CaptureLifecycleRecord,
    capture_id: str,
) -> capture_lifecycle.CaptureLifecycleRecord:
    assert record.manifest is not None
    incarnation = f"{int(capture_id, 16):032x}"
    wire = capture_snapshot.decode_capture_manifest_wire(record.manifest_bytes)
    manifest = capture_snapshot._restore_capture_final_manifest(
        replace(
            wire,
            capture_id=capture_id,
            incarnation=incarnation,
            carrier_name=f"shell_{capture_id}.log",
        )
    )
    candidate = replace(
        record,
        capture_id=capture_id,
        staging_name=f".capture-staging-{capture_id}-{incarnation[:16]}",
        public_name=f"shell_{capture_id}.log",
        incarnation=incarnation,
        manifest=manifest,
        manifest_bytes=capture_snapshot.encode_capture_final_manifest(manifest),
    )
    capture_lifecycle_record.validate_record(candidate)
    return candidate


def _admission_decision(
    records: dict[str, capture_lifecycle.CaptureLifecycleRecord],
    candidate: capture_lifecycle.CaptureLifecycleRecord,
    spec: capture_types.CaptureCapacitySpec,
    clock: _Clock,
):
    return capture_capacity.admission_reason(
        records,
        candidate,
        compaction_epoch=1,
        spec=spec,
        active_limit=min(
            capture_lifecycle.MAX_ACTIVE_RECORDS,
            spec.max_operational_records,
        ),
        sizer=capture_capacity.CompactedFrameSizer(),
        now=clock.wall(),
        sweep_active=False,
    )


@pytest.mark.parametrize(
    ("reason", "limit_field", "existing_count"),
    [
        (
            capture_lifecycle.CaptureCapacityReason.ACTIVE_CAPACITY,
            "max_operational_records",
            3,
        ),
        (
            capture_lifecycle.CaptureCapacityReason.RETENTION_CAPACITY,
            "max_retained_records",
            1,
        ),
        (
            capture_lifecycle.CaptureCapacityReason.EVIDENCE_CAPACITY,
            "max_evidence_records",
            2,
        ),
    ],
    ids=("active", "retention", "evidence"),
)
def test_admission_reason_reports_record_count_and_limit(
    tmp_path: Path,
    reason: capture_lifecycle.CaptureCapacityReason,
    limit_field: str,
    existing_count: int,
) -> None:
    spec = _capacity_spec(
        max_operational_records=4,
        max_retained_records=4,
        max_evidence_records=4,
        reclamation_debt_assist_records=1,
        reclamation_debt_stall_records=2,
    )
    clock = _Clock()
    anchor, root, store = _open_store(tmp_path / "project", clock)
    try:
        candidate = _finalize_template(
            store,
            root,
            clock,
            "aaaaaaaaaaaaaaa1",
            delivered=False,
        )
        assert candidate is not None
        records = {
            f"{index:016x}": _replicate_record(candidate, f"{index:016x}")
            for index in range(1, existing_count + 1)
        }
        decision = _admission_decision(
            records,
            candidate,
            replace(spec, **{limit_field: existing_count}),
            clock,
        )
    finally:
        root.close()
        anchor.close()

    assert decision.reason is reason
    assert decision.record_count == existing_count + 1
    assert decision.record_limit == existing_count


def test_pre_assist_debt_is_refused_by_assist_not_bytes(tmp_path: Path) -> None:
    clock = _Clock()
    anchor, root, store = _open_store(tmp_path / "project", clock)
    try:
        held_template = _finalize_template(
            store,
            root,
            clock,
            "aaaaaaaaaaaaaaa1",
            delivered=False,
        )
        due_template = _finalize_template(
            store,
            root,
            clock,
            "aaaaaaaaaaaaaaa2",
            delivered=True,
        )
        assert held_template is not None
        assert due_template is not None
        assert due_template.next_attempt_at <= clock.wall()
        assert store.sweep(capture_reconcile.RUNNER_TAIL_BUDGET).deleted == 1
        tombstone_template = store.get_record("aaaaaaaaaaaaaaa2")
        assert tombstone_template is not None
        assert tombstone_template.state is capture_lifecycle.CaptureState.DELETED
        reserved = store.reserve_capture("ffffffffffffffff")
    finally:
        root.close()
        anchor.close()

    tombstones = {
        capture_id: _replicate_record(tombstone_template, capture_id)
        for capture_id in (f"{index:016x}" for index in range(20_000, 20_256))
    }
    held = {
        capture_id: _replicate_record(held_template, capture_id)
        for capture_id in (f"{index:016x}" for index in range(1, 976))
    }
    due = {
        capture_id: _replicate_record(due_template, capture_id)
        for capture_id in (f"{index:016x}" for index in range(1_000, 1_255))
    }
    spec = _capacity_spec()
    baseline = tombstones | held | due
    assert len(baseline) == 1486
    assert _admission_decision(baseline, reserved, spec, clock).reason is None

    one_more_due = dict(baseline)
    new_due_id = "00000000000004e7"
    one_more_due[new_due_id] = _replicate_record(due_template, new_due_id)
    # Replace one held entry so the live-record count stays at the envelope cap.
    del one_more_due["0000000000000001"]
    assert len(one_more_due) == len(baseline)
    decision = _admission_decision(one_more_due, reserved, spec, clock)
    assert decision.reason is capture_lifecycle.CaptureCapacityReason.RECLAMATION_DEBT_ASSIST
    assert decision.reason is not capture_lifecycle.CaptureCapacityReason.PROJECTED_COMPACTED_BYTES
    assert decision.assist_transition_limit == 1
