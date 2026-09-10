"""Backward-compat shim for pipeline_tracker — see core.pipeline.pipeline_tracker."""

# Re-export monkeypatch targets used by tests/core/test_pipeline_tracker.py.
import fcntl  # noqa: F401
import os  # noqa: F401

import psutil  # noqa: F401

from autoskillit.core.pipeline.pipeline_tracker import (
    ARTIFACT_LEASE_TIMEOUT_SECONDS,
    ArtifactLease,
    TrackerAuthorityReadResult,
    TrackerAuthorityTarget,
    TrackerData,
    TrackerMutation,
    TrackerOwnerKind,
    TrackerParticipantKey,
    # Private names that tests reference via monkeypatch:
    _TrackerLock,
    acquire_flock_with_timeout,
    atomic_write,
    initialize_kitchen_tracker,
    initialize_manual_tracker,
    kitchen_entry_alive,
    mutate_tracker,
    pipeline_tracker_directory,
    pipeline_tracker_path,
    read_active_kitchens_registry,
    read_tracker_authority,
    release_tracker_lease,
    retain_tracker_lease,
    tracker_lease_path,
    try_retire_tracker,
)

__all__ = [
    "ARTIFACT_LEASE_TIMEOUT_SECONDS",
    "ArtifactLease",
    "TrackerAuthorityReadResult",
    "TrackerAuthorityTarget",
    "TrackerData",
    "TrackerMutation",
    "TrackerOwnerKind",
    "TrackerParticipantKey",
    "_TrackerLock",
    "acquire_flock_with_timeout",
    "atomic_write",
    "initialize_kitchen_tracker",
    "initialize_manual_tracker",
    "kitchen_entry_alive",
    "mutate_tracker",
    "pipeline_tracker_directory",
    "pipeline_tracker_path",
    "read_active_kitchens_registry",
    "read_tracker_authority",
    "release_tracker_lease",
    "retain_tracker_lease",
    "tracker_lease_path",
    "try_retire_tracker",
]
