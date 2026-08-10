from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Literal

import psutil
import pytest

from autoskillit.core import (
    ArtifactLease,
    ArtifactLeaseContention,
    TrackerAuthorityReadResult,
    TrackerAuthorityTarget,
    TrackerParticipantKey,
    initialize_kitchen_tracker,
    initialize_manual_tracker,
    mutate_tracker,
    pipeline_tracker_path,
    read_tracker_authority,
    release_tracker_lease,
    retain_tracker_lease,
    tracker_lease_path,
    try_retire_tracker,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _target(tmp_path, order_id: str = "order-1", *, expected: bool = True):
    return TrackerAuthorityTarget.for_project(tmp_path, order_id, expected=expected)


def _key(
    target,
    project_path,
    kind: Literal["kitchen", "dispatch", "manual"] = "kitchen",
):
    return TrackerParticipantKey(
        target=target,
        owner_kind=kind,
        owner_id="same-owner",
        pid=os.getpid(),
        create_time=psutil.Process(os.getpid()).create_time(),
        project_path=str(project_path),
    )


def _tracker(*, kitchen_id: str = "kitchen-1"):
    return {
        "pipeline_id": "order-1",
        "kitchen_id": kitchen_id,
        "steps": {"prepare": {"status": "pending"}},
        "dependencies": {"prepare": []},
    }


def _registry(monkeypatch, tmp_path, payload: object | None) -> None:
    registry_path = tmp_path / "registry" / "active_kitchens.json"
    lock_path = tmp_path / "registry" / "active_kitchens.lock"
    if payload is not None:
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text(json.dumps(payload))
    monkeypatch.setattr(
        "autoskillit.core._plugin_cache._active_kitchens_path", lambda: registry_path
    )
    monkeypatch.setattr("autoskillit.core._plugin_cache._active_kitchens_lock", lambda: lock_path)


@pytest.mark.parametrize("order_id", ["", ".", "..", "../escape", "a/b", "a\\b"])
def test_authority_target_rejects_nonexplicit_or_unsafe_ids(tmp_path, order_id):
    with pytest.raises(ValueError):
        pipeline_tracker_path(tmp_path, order_id)


def test_read_result_enforces_expected_authority_invariants(tmp_path):
    expected = _target(tmp_path)
    with pytest.raises(ValueError, match="expected authority"):
        TrackerAuthorityReadResult(expected)
    with pytest.raises(ValueError, match="cannot coexist"):
        TrackerAuthorityReadResult(expected, data=_tracker(), error="broken")

    optional = _target(tmp_path, expected=False)
    assert TrackerAuthorityReadResult(optional).data is None
    assert TrackerAuthorityReadResult(optional).error is None


def test_complete_participant_key_prevents_owner_kind_collision(tmp_path):
    target = _target(tmp_path)
    leases = {}
    kitchen_key = _key(target, tmp_path, "kitchen")
    dispatch_key = _key(target, tmp_path, "dispatch")
    kitchen = retain_tracker_lease(leases, kitchen_key)
    dispatch = retain_tracker_lease(leases, dispatch_key)
    try:
        assert kitchen is not dispatch
        assert len(leases) == 2
        release_tracker_lease(leases, kitchen_key)
        assert kitchen.closed
        assert not dispatch.closed
        with pytest.raises(ArtifactLeaseContention):
            ArtifactLease.acquire_exclusive(tracker_lease_path(target), blocking=False)
    finally:
        release_tracker_lease(leases, kitchen_key)
        release_tracker_lease(leases, dispatch_key)


def test_access_takes_shared_lease_before_tracker_lock(monkeypatch, tmp_path):
    import autoskillit.core.pipeline_tracker as tracker_module

    target = _target(tmp_path)
    leases = {}
    lease = retain_tracker_lease(leases, _key(target, tmp_path))
    target.path.parent.mkdir(parents=True, exist_ok=True)
    target.path.write_text(json.dumps(_tracker()))
    real_enter = tracker_module._TrackerLock.__enter__
    lock_entered = False

    def asserting_enter(lock):
        nonlocal lock_entered
        lock_entered = True
        with pytest.raises(ArtifactLeaseContention):
            ArtifactLease.acquire_exclusive(tracker_lease_path(target), blocking=False)
        return real_enter(lock)

    monkeypatch.setattr(tracker_module._TrackerLock, "__enter__", asserting_enter)
    try:
        assert read_tracker_authority(target, lease).data == _tracker()
        assert lock_entered
    finally:
        lease.close()


def test_locked_initialization_and_mutation_preserve_invalid_existing_bytes(tmp_path):
    target = _target(tmp_path)
    leases = {}
    lease = retain_tracker_lease(leases, _key(target, tmp_path))
    target.path.parent.mkdir(parents=True, exist_ok=True)
    target.path.write_bytes(b"{broken")
    try:
        kitchen_result = initialize_kitchen_tracker(target, lease, _tracker())
        assert kitchen_result.error is not None
        assert target.path.read_bytes() == b"{broken"

        manual_result = initialize_manual_tracker(target, lease, _tracker())
        assert "create-only" in (manual_result.error or "")
        assert target.path.read_bytes() == b"{broken"

        mutation_result = mutate_tracker(target, lease, lambda data: data)
        assert mutation_result.error is not None
        assert target.path.read_bytes() == b"{broken"
    finally:
        lease.close()


def test_kitchen_merge_preserves_progress_and_manual_init_is_create_only(tmp_path):
    target = _target(tmp_path, expected=False)
    leases = {}
    lease = retain_tracker_lease(leases, _key(target, tmp_path, "manual"))
    try:
        initial = _tracker()
        assert initialize_manual_tracker(target, lease, initial).data == initial
        assert initialize_manual_tracker(target, lease, initial).error is not None

        incoming = _tracker()
        incoming["steps"]["new"] = {"status": "pending"}
        current = json.loads(target.path.read_text())
        current["steps"]["prepare"] = {"status": "complete"}
        target.path.write_text(json.dumps(current))
        merged = initialize_kitchen_tracker(target, lease, incoming)
        assert merged.data is not None
        assert merged.data["steps"] == {
            "new": {"status": "pending"},
            "prepare": {"status": "complete"},
        }
    finally:
        lease.close()


def test_exact_retirement_skips_contention_and_never_unlinks_sidecar(monkeypatch, tmp_path):
    target = _target(tmp_path)
    target.path.parent.mkdir(parents=True)
    target.path.write_text(json.dumps(_tracker()))
    _registry(monkeypatch, tmp_path, None)
    leases = {}
    key = _key(target, tmp_path)
    retain_tracker_lease(leases, key)

    assert try_retire_tracker(target) is False
    assert target.path.exists()
    release_tracker_lease(leases, key)

    assert try_retire_tracker(target) is True
    assert not target.path.exists()
    assert tracker_lease_path(target).exists()


@pytest.mark.parametrize(
    "registry_payload",
    [b"{broken", json.dumps({"schema_version": 999, "kitchens": []}).encode()],
)
def test_retirement_preserves_tracker_on_unsafe_registry(monkeypatch, tmp_path, registry_payload):
    target = _target(tmp_path)
    target.path.parent.mkdir(parents=True)
    target.path.write_text(json.dumps(_tracker()))
    registry_path = tmp_path / "registry" / "active_kitchens.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_bytes(registry_payload)
    monkeypatch.setattr(
        "autoskillit.core._plugin_cache._active_kitchens_path", lambda: registry_path
    )
    monkeypatch.setattr(
        "autoskillit.core._plugin_cache._active_kitchens_lock",
        lambda: registry_path.with_suffix(".lock"),
    )

    assert try_retire_tracker(target) is False
    assert target.path.exists()


def test_retirement_preserves_tracker_for_live_exact_kitchen(monkeypatch, tmp_path):
    target = _target(tmp_path)
    target.path.parent.mkdir(parents=True)
    target.path.write_text(json.dumps(_tracker()))
    identity = {
        "kitchen_id": "kitchen-1",
        "pid": os.getpid(),
        "create_time": psutil.Process(os.getpid()).create_time(),
        "project_path": str(tmp_path),
        "opened_at": datetime.now(UTC).isoformat(),
    }
    _registry(monkeypatch, tmp_path, {"schema_version": 2, "kitchens": [identity]})

    assert try_retire_tracker(target) is False
    assert target.path.exists()


def test_retirement_ignores_same_kitchen_id_from_other_project(monkeypatch, tmp_path):
    project = tmp_path / "project"
    other_project = tmp_path / "other-project"
    target = _target(project)
    target.path.parent.mkdir(parents=True)
    target.path.write_text(json.dumps(_tracker()))
    identity = {
        "kitchen_id": "kitchen-1",
        "pid": os.getpid(),
        "create_time": psutil.Process(os.getpid()).create_time(),
        "project_path": str(other_project),
        "opened_at": datetime.now(UTC).isoformat(),
    }
    _registry(monkeypatch, tmp_path, {"schema_version": 2, "kitchens": [identity]})

    assert try_retire_tracker(target) is True
    assert not target.path.exists()


def test_retirement_preserves_tracker_without_valid_kitchen_id(monkeypatch, tmp_path):
    target = _target(tmp_path)
    target.path.parent.mkdir(parents=True)
    target.path.write_text(json.dumps(_tracker(kitchen_id="")))
    _registry(monkeypatch, tmp_path, None)

    assert try_retire_tracker(target) is False
    assert target.path.exists()


def test_retirement_preserves_tracker_when_liveness_probe_errors(monkeypatch, tmp_path):
    target = _target(tmp_path)
    target.path.parent.mkdir(parents=True)
    target.path.write_text(json.dumps(_tracker()))
    identity = {
        "kitchen_id": "kitchen-1",
        "pid": os.getpid(),
        "create_time": psutil.Process(os.getpid()).create_time(),
        "project_path": str(tmp_path),
        "opened_at": datetime.now(UTC).isoformat(),
    }
    _registry(monkeypatch, tmp_path, {"schema_version": 2, "kitchens": [identity]})

    def _raise(_entry):
        raise psutil.AccessDenied()

    monkeypatch.setattr("autoskillit.core.pipeline_tracker.kitchen_entry_alive", _raise)

    assert try_retire_tracker(target) is False
    assert target.path.exists()
