"""Stable facade re-exporting exploration snapshot capture surface.

Decomposed from the original 1,155-line ``exploration/snapshot.py`` per #4836.
Importing shard symbols directly is fine; importing them through this facade
guarantees the public surface (``autoskillit.exploration.snapshot.X``) survives
future shard reorganisation.

This facade re-exports not only the public capture API but also the helpers
the test suite monkeypatches through the module (``subprocess``, ``time``,
``_capture_once``, ``activate_repository_profiles``, ``observe_path_mode``,
``resolve_repository_identity``). Removing any of these re-exports breaks the
existing test suite.
"""

from __future__ import annotations

# Imports below are intentionally marked ``# noqa: F401`` because the test
# suite monkeypatches these through the facade (``monkeypatch.setattr(
# snapshot_module, "_capture_once", ...)``); they must remain reachable as
# module attributes even though the facade itself does not reference them.
import subprocess  # noqa: F401
import time  # noqa: F401

from autoskillit.core import SnapshotCaptureReason, SnapshotCaptureStatus

# ``read_stable_contained_file`` is imported from ``_artifact`` for the same
# reason the other helpers are re-exported here: the test suite monkeypatches
# this name on the facade to simulate post-open mutation. The plan's Test #3
# facade table omits this name, but the live test exists and the helper must
# resolve.
from ._artifact import (
    capture_stable_artifact,
    read_stable_contained_file,  # noqa: F401
    stable_artifact_matches,
)
from ._capture import (  # noqa: F401
    _capture_once,  # noqa: F401
    activate_repository_profiles,  # noqa: F401
    capture_repository_snapshot,
    observe_path_mode,  # noqa: F401
    resolve_repository_identity,  # noqa: F401
    resolve_repository_path,
)
from ._records import (
    DEFAULT_IGNORE_POLICY,  # noqa: F401  tests patch via facade
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
