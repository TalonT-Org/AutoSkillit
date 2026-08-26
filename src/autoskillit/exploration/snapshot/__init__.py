"""Stable facade re-exporting exploration snapshot capture surface.

Decomposed from the original 1,155-line ``exploration/snapshot.py`` per #4836.
Importing shard symbols directly is fine; importing them through this facade
guarantees the public surface (``autoskillit.exploration.snapshot.X``) survives
future shard reorganisation.
"""

from __future__ import annotations

from autoskillit.core import SnapshotCaptureReason, SnapshotCaptureStatus

from ._artifact import (
    capture_stable_artifact,
    read_stable_contained_file,  # noqa: F401  production uses _snapshot_facade lookup
    stable_artifact_matches,
)
from ._capture import (  # noqa: F401
    _capture_once,  # noqa: F401
    activate_repository_profiles,  # noqa: F401
    capture_repository_snapshot,
    observe_path_mode,  # noqa: F401
    resolve_repository_identity,  # noqa: F401  production uses _snapshot_facade lookup
    resolve_repository_path,
)
from ._records import (
    DEFAULT_IGNORE_POLICY,  # noqa: F401  production uses _snapshot_facade lookup
    ArtifactCaptureError,
    ArtifactCaptureStatus,
    SnapshotCaptureLimits,
    SnapshotCaptureResult,
    StableArtifactCapture,
)

__all__ = [
    # Public capture API
    "ArtifactCaptureError",
    "ArtifactCaptureStatus",
    "SnapshotCaptureLimits",
    "SnapshotCaptureReason",
    "SnapshotCaptureResult",
    "SnapshotCaptureStatus",
    "StableArtifactCapture",
    "capture_repository_snapshot",
    "capture_stable_artifact",
    "resolve_repository_path",
    "stable_artifact_matches",
]
