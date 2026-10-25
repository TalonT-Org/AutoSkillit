"""Real-encoder profile that pins the capture frame planning allowance."""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

import autoskillit.hooks._capture._capacity as capture_capacity
import autoskillit.hooks._capture._failure_policy as capture_failure_policy
import autoskillit.hooks._capture._lifecycle_record as capture_lifecycle_record
import autoskillit.hooks._capture._reconcile as capture_reconcile
import autoskillit.hooks._capture._snapshot as capture_snapshot
import autoskillit.hooks._capture._types as capture_types
import autoskillit.hooks._capture_lifecycle as capture_lifecycle
from autoskillit.hooks._capture_artifacts import create_capture_artifact
from tests.hooks.test_capture_lifecycle import _Clock, _open_store, _verified_snapshot

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

_MAX_NUMERIC = 2**63 - 1
_COMPACTION_EPOCH = _MAX_NUMERIC
_REFERENCE_CAPTURE_ID = "0000000000000001"
_FAILURE_CAPTURE_ID = "0000000000000002"


def _close_artifact(artifact) -> None:
    try:
        artifact.close_artifact_fd()
    finally:
        artifact.release_lease()


def _profile_record(
    record: capture_lifecycle.CaptureLifecycleRecord,
) -> capture_lifecycle.CaptureLifecycleRecord:
    identity = (_MAX_NUMERIC, _MAX_NUMERIC)
    manifest = None
    manifest_bytes = b""
    finalized_at_revision = record.finalized_at_revision
    if record.manifest is not None:
        wire = capture_snapshot.decode_capture_manifest_wire(record.manifest_bytes)
        manifest = capture_snapshot._restore_capture_final_manifest(
            replace(
                wire,
                finalized_at_revision=_MAX_NUMERIC,
                project_identity=identity,
                root_identity=identity,
                carrier_identity=identity,
                total_bytes=_MAX_NUMERIC,
                inline_length=_MAX_NUMERIC,
                head_length=_MAX_NUMERIC,
                tail_length=_MAX_NUMERIC,
            )
        )
        manifest_bytes = capture_snapshot.encode_capture_final_manifest(manifest)
        finalized_at_revision = _MAX_NUMERIC
    candidate = replace(
        record,
        project_identity=identity,
        root_identity=identity,
        artifact_identity=identity if record.artifact_identity is not None else None,
        observed_size=_MAX_NUMERIC,
        revision=_MAX_NUMERIC,
        finalized_at_revision=finalized_at_revision,
        compaction_epoch=_MAX_NUMERIC,
        retry_count=_MAX_NUMERIC,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
    )
    capture_lifecycle_record.validate_record(candidate)
    return candidate


@pytest.fixture(scope="module")
def profiled_records(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[capture_lifecycle.CaptureLifecycleRecord, ...]:
    clock = _Clock()
    anchor, root, store = _open_store(tmp_path_factory.mktemp("capture-frame-bound"), clock)
    try:
        reference_artifact = create_capture_artifact(
            root,
            _REFERENCE_CAPTURE_ID,
            store,
        )
        try:
            data = b"published frame"
            os.write(reference_artifact.fd, data)
            verified = _verified_snapshot(store, reference_artifact, data, clock)
            finalized = store.commit_verified_snapshot(verified, issue_reference=True)
            published = store.publish_reference(finalized)
            store.transition_delivery(
                published,
                expected=capture_lifecycle.CaptureDeliveryStatus.NOT_ATTEMPTED,
                target=capture_lifecycle.CaptureDeliveryStatus.ATTEMPTING,
            )
            store.transition_delivery(
                published,
                expected=capture_lifecycle.CaptureDeliveryStatus.ATTEMPTING,
                target=capture_lifecycle.CaptureDeliveryStatus.DELIVERED,
            )
        finally:
            _close_artifact(reference_artifact)

        finalized_record = store.get_record(_REFERENCE_CAPTURE_ID)
        assert finalized_record is not None
        assert finalized_record.state is capture_lifecycle.CaptureState.FINALIZED
        profiled_finalized = _profile_record(finalized_record)

        assert finalized_record.manifest is not None
        assert finalized_record.manifest.reference_expiry is not None
        clock.advance(finalized_record.manifest.reference_expiry - clock.wall() + 1)
        assert store.sweep(capture_reconcile.RUNNER_TAIL_BUDGET).deleted == 1
        deleted_record = store.get_record(_REFERENCE_CAPTURE_ID)
        assert deleted_record is not None
        assert deleted_record.state is capture_lifecycle.CaptureState.DELETED
        profiled_deleted = _profile_record(deleted_record)

        failure_artifact = create_capture_artifact(root, _FAILURE_CAPTURE_ID, store)
        try:
            detail = capture_failure_policy.normalize_failure_detail("\x01" * 240)
            assert detail == "\x01" * 240
            failed_record = store.commit_capture_failure(
                failure_artifact.authority,
                capture_lifecycle.CaptureFailureEvidence(
                    stage="frame_bound",
                    detail=detail,
                ),
                observed_size=0,
            )
        finally:
            _close_artifact(failure_artifact)
        assert failed_record.state is capture_lifecycle.CaptureState.FAILED
        profiled_failed = _profile_record(failed_record)
        return profiled_finalized, profiled_deleted, profiled_failed
    finally:
        root.close()
        anchor.close()


def _encoded_size(record: capture_lifecycle.CaptureLifecycleRecord) -> int:
    return len(
        capture_lifecycle._capture_ledger.encode_frame(
            capture_lifecycle._capture_lifecycle_record.record_to_dict(record),
            compaction_epoch=_COMPACTION_EPOCH,
        )
    )


def test_real_frame_shapes_fit_the_planning_allowance(
    profiled_records: tuple[capture_lifecycle.CaptureLifecycleRecord, ...],
) -> None:
    sizes = tuple(_encoded_size(record) for record in profiled_records)
    measured_max = max(sizes)
    violations = capture_types.capacity_envelope_violations(capture_types.CaptureCapacitySpec())
    diagnostic = (
        f"measured_max={measured_max}, planned={capture_types.PLANNED_RECORD_FRAME_BYTES}, "
        f"default_low={capture_types.CaptureCapacitySpec().compaction_low_bytes}, "
        f"default_violations={violations}"
    )
    assert measured_max <= capture_types.PLANNED_RECORD_FRAME_BYTES <= measured_max + 64, (
        diagnostic
    )


def test_larger_valid_frame_is_charged_at_its_actual_size(
    profiled_records: tuple[capture_lifecycle.CaptureLifecycleRecord, ...],
) -> None:
    oversized = replace(
        profiled_records[0],
        revision=10**1000,
        next_attempt_at=profiled_records[0].next_attempt_at + 3600,
    )
    capture_lifecycle_record.validate_record(oversized)
    encoded_size = _encoded_size(oversized)
    assert encoded_size > capture_types.PLANNED_RECORD_FRAME_BYTES

    base = capture_types.CaptureCapacitySpec()
    low_bytes = encoded_size - 1
    high_bytes = encoded_size + 1024
    spec = replace(
        base,
        compaction_low_bytes=low_bytes,
        compaction_high_bytes=high_bytes,
        hard_ledger_bytes=high_bytes + base.recovery_headroom_bytes,
    )
    decision = capture_capacity.admission_reason(
        {},
        oversized,
        compaction_epoch=_COMPACTION_EPOCH,
        spec=spec,
        active_limit=min(capture_lifecycle.MAX_ACTIVE_RECORDS, spec.max_operational_records),
        sizer=capture_capacity.CompactedFrameSizer(),
        now=profiled_records[0].created_at,
        sweep_active=False,
    )
    assert decision.reason is capture_lifecycle.CaptureCapacityReason.PROJECTED_COMPACTED_BYTES
