"""Sustained capture admission under readability-based retention."""

from __future__ import annotations

import math
import os
from dataclasses import replace
from pathlib import Path

import pytest

import autoskillit.hooks._capture._lifecycle_policy as capture_lifecycle_policy
import autoskillit.hooks._capture._reconcile as capture_reconcile
import autoskillit.hooks._capture_lifecycle as capture_lifecycle
from autoskillit.hooks._capture._snapshot import (
    CaptureMeasurement,
    CommandOutcome,
    verify_capture_snapshot,
)
from autoskillit.hooks._capture._types import CaptureCapacitySpec
from autoskillit.hooks._capture_artifacts import create_capture_artifact
from tests.hooks.test_capture_lifecycle import (
    _Clock,
    _close_artifact,
    _open_store,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]


def _begin_capture(
    store,
    anchor,
    root,
    clock: _Clock,
    capture_id: str,
    *,
    issue_reference: bool = False,
):
    artifact = create_capture_artifact(root, capture_id, store)
    try:
        data = b"captured output"
        os.write(artifact.fd, data)
        snapshot = verify_capture_snapshot(
            fd=artifact.fd,
            capture_id=artifact.authority.capture_id,
            incarnation=artifact.authority.incarnation,
            project_identity=(anchor.identity.device, anchor.identity.inode),
            root_identity=(root.identity.device, root.identity.inode),
            carrier_name=artifact.name,
            carrier_identity=(artifact.identity.device, artifact.identity.inode),
            measurement=CaptureMeasurement.from_bytes(data, inline_bytes=len(data)),
            command_outcome=CommandOutcome.exited(0),
            expected_revision=artifact.authority.expected_revision,
            finalized_at=clock.wall(),
            retention_deadline=clock.wall() + 3600.0,
        )
        finalized = store.commit_verified_snapshot(
            snapshot,
            issue_reference=issue_reference,
        )
        return artifact, finalized
    except BaseException:
        _close_artifact(artifact)
        raise


def _deliver_capture(store, value) -> None:
    store.transition_delivery(
        value,
        expected=capture_lifecycle.CaptureDeliveryStatus.NOT_ATTEMPTED,
        target=capture_lifecycle.CaptureDeliveryStatus.ATTEMPTING,
    )
    store.transition_delivery(
        value,
        expected=capture_lifecycle.CaptureDeliveryStatus.ATTEMPTING,
        target=capture_lifecycle.CaptureDeliveryStatus.DELIVERED,
    )


def _observe_capacity_and_deletions(
    store,
    monkeypatch: pytest.MonkeyPatch,
    active_ids: set[str],
    reference_expiries: dict[str, float],
    clock: _Clock,
) -> list[capture_lifecycle.CaptureCapacityReason]:
    debt_reasons = []
    real_admission_reason = store._admission_reason

    def observe_admission_reason(records, candidate, compaction_epoch, now):
        decision = real_admission_reason(records, candidate, compaction_epoch, now)
        if decision.reason in {
            capture_lifecycle.CaptureCapacityReason.RECLAMATION_DEBT_ASSIST,
            capture_lifecycle.CaptureCapacityReason.RECLAMATION_DEBT_STALL,
        }:
            debt_reasons.append(decision.reason)
        return decision

    real_sweep_one = store._sweep_one

    def observe_sweep_one(capture_id: str):
        result = real_sweep_one(capture_id)
        if result[0] is capture_lifecycle.SweepAttempt.DELETED:
            if capture_id in reference_expiries:
                assert clock.wall() >= reference_expiries[capture_id]
            active_ids.discard(capture_id)
        return result

    monkeypatch.setattr(store, "_admission_reason", observe_admission_reason)
    monkeypatch.setattr(store, "_sweep_one", observe_sweep_one)
    return debt_reasons


def _capacity_for_measured_frame(tmp_path: Path, clock: _Clock) -> CaptureCapacitySpec:
    anchor, root, store = _open_store(tmp_path / "bootstrap", clock)
    artifact, _finalized = _begin_capture(
        store,
        anchor,
        root,
        clock,
        "ffffffffffffffff",
    )
    try:
        frame_record = store.get_record("ffffffffffffffff")
        assert isinstance(frame_record, capture_lifecycle.CaptureLifecycleRecord)
        assert frame_record.state is capture_lifecycle.CaptureState.FINALIZED
        frame = len(
            capture_lifecycle._capture_ledger.encode_frame(
                capture_lifecycle._capture_lifecycle_record.record_to_dict(frame_record),
                compaction_epoch=1,
            )
        )
    finally:
        _close_artifact(artifact)
        root.close()
        anchor.close()

    base = CaptureCapacitySpec()
    low_bytes = frame * 150
    high_bytes = low_bytes + 4096
    return replace(
        base,
        max_tombstones=32,
        compaction_low_bytes=low_bytes,
        compaction_high_bytes=high_bytes,
        hard_ledger_bytes=high_bytes + base.recovery_headroom_bytes,
    )


def _produce_capture(
    store,
    anchor,
    root,
    clock: _Clock,
    capture_id: str,
    *,
    reference: bool,
    active_ids: set[str],
    reference_expiries: dict[str, float],
):
    active_ids.add(capture_id)
    artifact, finalized = _begin_capture(
        store,
        anchor,
        root,
        clock,
        capture_id,
        issue_reference=reference,
    )
    try:
        value = store.publish_reference(finalized) if reference else finalized
        if reference:
            record = store.get_record(capture_id)
            assert record is not None
            assert record.manifest is not None
            assert record.manifest.reference_expiry is not None
            reference_expiries[capture_id] = record.manifest.reference_expiry
        _deliver_capture(store, value)
    finally:
        _close_artifact(artifact)


def _sweep_capture(store, capture_id: str) -> None:
    store.sweep(
        capture_reconcile.RUNNER_TAIL_BUDGET,
        priority_capture_id=capture_id,
    )


def test_issue_5282_inline_burst_does_not_exhaust_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    capacity = _capacity_for_measured_frame(tmp_path, clock)
    anchor, root, store = _open_store(tmp_path / "workload", clock, capacity=capacity)
    active_ids: set[str] = set()
    reference_expiries: dict[str, float] = {}
    debt_reasons = _observe_capacity_and_deletions(
        store,
        monkeypatch,
        active_ids,
        reference_expiries,
        clock,
    )
    max_non_deleted = 0
    capture_number = 0
    try:
        producers = 4
        for _ in range(5400 // 30):
            clock.advance(30)
            tick_capture_ids = []
            for _ in range(producers):
                capture_id = f"{capture_number:016x}"
                _produce_capture(
                    store,
                    anchor,
                    root,
                    clock,
                    capture_id,
                    reference=capture_number % 12 == 0,
                    active_ids=active_ids,
                    reference_expiries=reference_expiries,
                )
                tick_capture_ids.append(capture_id)
                capture_number += 1
                max_non_deleted = max(max_non_deleted, len(active_ids))
            for capture_id in tick_capture_ids:
                _sweep_capture(store, capture_id)
                max_non_deleted = max(max_non_deleted, len(active_ids))

        referenced_per_second = 1 / 90
        allowed_non_deleted = (
            math.ceil(referenced_per_second * capture_lifecycle_policy.REFERENCE_LIFETIME_SECONDS)
            + producers
            + 1
        )
        assert capture_number == 720
        assert max_non_deleted <= allowed_non_deleted
        assert not debt_reasons
    finally:
        root.close()
        anchor.close()


def test_referenced_load_never_reclaims_before_expiry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    capacity = _capacity_for_measured_frame(tmp_path, clock)
    anchor, root, store = _open_store(tmp_path / "workload", clock, capacity=capacity)
    active_ids: set[str] = set()
    reference_expiries: dict[str, float] = {}
    debt_reasons = _observe_capacity_and_deletions(
        store,
        monkeypatch,
        active_ids,
        reference_expiries,
        clock,
    )
    max_non_deleted = 0
    try:
        for capture_number in range(5400 // 30):
            clock.advance(30)
            capture_id = f"{capture_number:016x}"
            _produce_capture(
                store,
                anchor,
                root,
                clock,
                capture_id,
                reference=True,
                active_ids=active_ids,
                reference_expiries=reference_expiries,
            )
            _sweep_capture(store, capture_id)
            max_non_deleted = max(max_non_deleted, len(active_ids))

        max_live_references = math.ceil(capture_lifecycle_policy.REFERENCE_LIFETIME_SECONDS / 30)
        assert max_non_deleted <= max_live_references + 1
        assert not debt_reasons
    finally:
        root.close()
        anchor.close()
