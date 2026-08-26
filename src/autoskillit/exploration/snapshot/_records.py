"""Dataclasses, errors, schema constants, and the ``(status, reason)`` invariant.

Decomposed from the original ``exploration/snapshot.py`` per #4836. ``_ObservedPath``
moves here with its identity/published byte accounting because it is the schema
of one observation (not the producer of one) — ``_path_state`` (the producer)
and ``_identity_state_payload`` (the helper that surfaces the private digest)
live in ``_capture.py``. ``_CaptureAborted.__init__`` and
``_expected_status_for_reason`` are colocated here because the constructor's
assertion that ``_expected_status_for_reason(reason) == status`` cannot drift
between the two without silently breaking every raise site.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, assert_never

from autoskillit.core import (
    RepositorySnapshot,
    SnapshotCaptureReason,
    SnapshotCaptureStatus,
)

from ..profile import RepositoryProfileActivation

__all__ = [
    # Schema constants (consumed by _capture.py and _artifact.py)
    "DEFAULT_IGNORE_POLICY",
    "SNAPSHOT_DIGEST_DOMAIN",
    "SNAPSHOT_SCHEMA_ID",
    "SNAPSHOT_SCHEMA_VERSION",
    "STABLE_ARTIFACT_DIGEST_DOMAIN",
    "_MAX_STABLE_ARTIFACT_ATTEMPTS",
    "_MAX_STABLE_ARTIFACT_BYTES",
    "_CaptureAborted",
    "_ObservedPath",
]

SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_SCHEMA_ID = "autoskillit.repository-snapshot.v1"
SNAPSHOT_DIGEST_DOMAIN = b"autoskillit.repository-snapshot.v1\0"
DEFAULT_IGNORE_POLICY = "ignored-names-modes-collapsed-v2"
STABLE_ARTIFACT_DIGEST_DOMAIN = b"autoskillit.stable-artifact.v1\0"
_MAX_STABLE_ARTIFACT_BYTES = 1_000_000
_MAX_STABLE_ARTIFACT_ATTEMPTS = 3


class ArtifactCaptureStatus(StrEnum):
    STALE = "stale"
    UNSUPPORTED = "unsupported"


class ArtifactCaptureError(RuntimeError):
    """A structured terminal failure to capture one stable artifact."""

    def __init__(self, status: ArtifactCaptureStatus, stop_reason: str) -> None:
        self.status = status
        self.stop_reason = stop_reason
        super().__init__(f"{status}: {stop_reason}")


@dataclass(frozen=True, slots=True)
class StableArtifactCapture:
    """Immutable bytes and path-specific repository authority for one artifact."""

    repository_root: Path
    repository_identity_digest: str
    revision: str
    artifact_path: str
    content: bytes
    content_digest: str
    size: int
    mode: int
    index_records: tuple[str, ...]
    snapshot_digest: str


@dataclass(frozen=True, slots=True)
class SnapshotCaptureLimits:
    """Hard bounds applied before a snapshot can become publishable."""

    max_paths: int = 50_000
    max_file_bytes: int = 64 * 1024 * 1024
    max_total_bytes: int = 256 * 1024 * 1024
    git_timeout_seconds: int = 30
    capture_deadline_seconds: int = 120

    def __post_init__(self) -> None:
        for name in (
            "max_paths",
            "max_file_bytes",
            "max_total_bytes",
            "git_timeout_seconds",
            "capture_deadline_seconds",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class RepositoryPathState:
    """Canonical state of one worktree path without following symlinks."""

    path: str
    category: Literal["tracked", "untracked", "ignored"]
    kind: Literal["file", "symlink", "directory", "other", "missing"]
    mode: int
    size: int
    content_digest: str
    symlink_target: str


@dataclass(frozen=True, slots=True)
class _ObservedPath:
    """Internal path authority that keeps ignored-file bytes out of returned state.

    ``published_bytes`` and ``identity_bytes`` diverge only for an ignored regular
    file: its content is still read and hashed in full (``identity_bytes``) so a
    rewrite still invalidates the fingerprint, but none of that size is charged
    against ``max_total_bytes`` and none of it reaches the public state (``0``).
    Every other observation reads and publishes the same bytes, so the two fields
    are equal.
    """

    state: RepositoryPathState
    published_bytes: int
    identity_bytes: int
    identity_content_digest: str


@dataclass(frozen=True, slots=True)
class CapturedRepositoryState:
    """One complete repository observation used for start/end comparison."""

    schema_version: int
    worktree_root: str
    common_git_dir: str
    head: str
    index_tree_digest: str
    working_tree_digest: str
    status_digest: str
    ignore_policy: str
    tracked_paths: int
    untracked_paths: int
    ignored_paths: int
    missing_paths: int
    total_published_bytes: int
    snapshot_identity: str
    tracked_records: tuple[tuple[str, str], ...]
    untracked_records: tuple[tuple[str, str], ...]
    ignored_records: tuple[tuple[str, str], ...]
    missing_records: tuple[tuple[str, str], ...]
    mode_records: tuple[tuple[str, str], ...]
    symlink_records: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class SnapshotCaptureResult:
    """Atomic snapshot result; non-complete results never expose a snapshot."""

    status: SnapshotCaptureStatus
    snapshot: RepositorySnapshot | None
    diagnostic: str = ""
    reason: SnapshotCaptureReason | None = None
    start_identity: str = ""
    end_identity: str = ""
    validated_activation: RepositoryProfileActivation | None = None

    def __post_init__(self) -> None:
        if self.status is SnapshotCaptureStatus.COMPLETE and self.reason is not None:
            raise ValueError("complete snapshot capture cannot expose a failure reason")
        if self.status is not SnapshotCaptureStatus.COMPLETE and self.reason is None:
            raise ValueError("non-complete snapshot capture requires a failure reason")
        if self.status is SnapshotCaptureStatus.COMPLETE and self.snapshot is None:
            raise ValueError("complete snapshot capture requires a snapshot")
        if self.status is SnapshotCaptureStatus.COMPLETE and self.validated_activation is None:
            raise ValueError("complete snapshot capture requires a validated activation")
        if (
            self.status in {SnapshotCaptureStatus.STALE, SnapshotCaptureStatus.TRUNCATED}
            and self.snapshot is None
        ):
            raise ValueError("terminal snapshot capture requires an atomic marker")
        if self.status is SnapshotCaptureStatus.FAILED and self.snapshot is not None:
            raise ValueError("failed snapshot capture cannot expose snapshot state")
        if (
            self.status is not SnapshotCaptureStatus.COMPLETE
            and self.validated_activation is not None
        ):
            raise ValueError("non-complete snapshot capture cannot expose an activation")
        if self.snapshot is None or self.validated_activation is None:
            return
        activation = self.validated_activation
        if self.snapshot.stale or self.snapshot.truncated:
            raise ValueError("complete snapshot capture requires complete snapshot state")
        if self.snapshot.identity != activation.identity.repository_identity:
            raise ValueError("snapshot and activation repository identities must match")
        if self.snapshot.profile_activation_digest != activation.activation_digest:
            raise ValueError("snapshot and activation digests must match")
        if self.snapshot.profile_versions != activation.profile_versions:
            raise ValueError("snapshot and activation profile versions must match")


def _expected_status_for_reason(reason: SnapshotCaptureReason) -> SnapshotCaptureStatus:
    """The one legal :class:`SnapshotCaptureStatus` for each terminal cause.

    Exhaustive over every member of :class:`SnapshotCaptureReason` so a reason
    added without an assigned status is a mypy failure at ``assert_never``
    (pre-commit), not a mismatched ``(status, reason)`` pair discovered later by
    a caller.
    """
    match reason:
        case (
            SnapshotCaptureReason.PATH_COUNT_EXCEEDED
            | SnapshotCaptureReason.FILE_BYTES_EXCEEDED
            | SnapshotCaptureReason.TOTAL_BYTES_EXCEEDED
        ):
            return SnapshotCaptureStatus.TRUNCATED
        case SnapshotCaptureReason.IDENTITY_DRIFT:
            return SnapshotCaptureStatus.STALE
        case (
            SnapshotCaptureReason.GIT_TIMEOUT
            | SnapshotCaptureReason.GIT_COMMAND_FAILED
            | SnapshotCaptureReason.ROOT_NOT_WORKTREE
            | SnapshotCaptureReason.IDENTITY_UNRESOLVED
            | SnapshotCaptureReason.PROFILE_ACTIVATION_FAILED
            | SnapshotCaptureReason.WORKTREE_UNREADABLE
            | SnapshotCaptureReason.COLLECTOR_SAFETY_FAULT
            | SnapshotCaptureReason.MANIFEST_DIGEST_EMPTY
            | SnapshotCaptureReason.CAPTURE_DEADLINE_EXCEEDED
        ):
            return SnapshotCaptureStatus.FAILED
        case _ as unreachable:
            assert_never(unreachable)


class _CaptureAborted(RuntimeError):
    """A structured internal abort of one repository snapshot capture attempt."""

    def __init__(
        self, status: SnapshotCaptureStatus, reason: SnapshotCaptureReason, detail: str
    ) -> None:
        expected = _expected_status_for_reason(reason)
        if status is not expected:
            raise AssertionError(
                f"_CaptureAborted status/reason mismatch: {reason} requires "
                f"{expected}, got {status}"
            )
        self.status = status
        self.reason = reason
        self.detail = detail
        super().__init__(f"{status}: {reason}: {detail}")
