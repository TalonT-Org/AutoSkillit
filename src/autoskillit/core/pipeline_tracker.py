"""Backward-compat shim for pipeline_tracker — see core.pipeline.pipeline_tracker."""

from autoskillit.core.pipeline.pipeline_tracker import (
    TrackerAuthorityReadResult,
    TrackerAuthorityTarget,
    TrackerParticipantKey,
    initialize_kitchen_tracker,
    initialize_manual_tracker,
    mutate_tracker,
    pipeline_tracker_directory,
    pipeline_tracker_path,
    read_tracker_authority,
    release_tracker_lease,
    retain_tracker_lease,
    tracker_lease_path,
    try_retire_tracker,
)

__all__ = [
    "TrackerAuthorityReadResult",
    "TrackerAuthorityTarget",
    "TrackerParticipantKey",
    "initialize_kitchen_tracker",
    "initialize_manual_tracker",
    "mutate_tracker",
    "pipeline_tracker_directory",
    "pipeline_tracker_path",
    "read_tracker_authority",
    "release_tracker_lease",
    "retain_tracker_lease",
    "tracker_lease_path",
    "try_retire_tracker",
]
