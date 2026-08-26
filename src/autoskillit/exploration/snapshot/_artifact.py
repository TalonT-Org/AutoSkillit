"""Stable-artifact capture — bounded per-path capture with deadline-driven retries.

Decomposed from the original ``exploration/snapshot.py`` per #4836. Public
surface: ``capture_stable_artifact``, ``stable_artifact_matches``. Uses
``read_stable_contained_file`` (a different primitive from
``open_contained_regular_file``) because artifact capture tolerates bounded
mutation via retry rather than failing closed at the first read. Does NOT
import ``resolve_repository_path`` from ``_capture.py`` — it resolves the
worktree root directly with its own ``is_symlink()`` check.
"""

from __future__ import annotations

import hashlib
import math
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

from autoskillit.core import RepositoryIdentity

from .._digest import qualified_digest
from ..collectors._bounded import (
    CollectorByteLimitError,
    CollectorMutationError,
    CollectorNoFollowUnsupportedError,
    CollectorNotRegularFileError,
    CollectorPathInvalidError,
    CollectorRootInvalidError,
    CollectorSafetyError,
    read_stable_contained_file,  # noqa: F401  re-exported via facade for monkeypatch site
)
from ..identity import resolve_repository_identity

# `_git` and `_index_records` are imported here from ``_capture`` rather than
# duplicated so the deadline envelope and NUL-record parsing stay single-sourced
# across the snapshot package. The plan forbids importing ``resolve_repository_path``
# from ``_capture`` — this is consistent: artifact capture does its own root
# validation but uses the same atomic capture primitives.
from ._capture import _git, _index_records  # noqa: E402
from ._records import (
    _MAX_STABLE_ARTIFACT_ATTEMPTS,
    _MAX_STABLE_ARTIFACT_BYTES,
    STABLE_ARTIFACT_DIGEST_DOMAIN,
    ArtifactCaptureError,
    ArtifactCaptureStatus,
    StableArtifactCapture,
)

# Capture the facade module so ``capture_stable_artifact`` looks up
# ``read_stable_contained_file`` through the package attribute. ``test_snapshot.py``
# monkeypatches ``snapshot_module.read_stable_contained_file`` (line 349) and
# expects the patch to propagate; without late-binding through the facade, a
# local import in this shard would capture a separate binding the patch cannot
# reach. The cycle resolves via ``sys.modules``: the facade module entry exists
# by the time ``_artifact.py`` is loaded because the facade's ``from ._artifact
# import`` runs first.
_snapshot_facade = sys.modules[__package__ or "autoskillit.exploration.snapshot"]


def _artifact_path(relative_path: str) -> str:
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or "\x00" in relative_path
        or "\\" in relative_path
    ):
        raise ArtifactCaptureError(ArtifactCaptureStatus.UNSUPPORTED, "invalid_artifact_path")
    path = PurePosixPath(relative_path)
    if path.is_absolute() or not path.parts or ".." in path.parts or ".git" in path.parts:
        raise ArtifactCaptureError(ArtifactCaptureStatus.UNSUPPORTED, "invalid_artifact_path")
    normalized = path.as_posix()
    if normalized in {"", "."}:
        raise ArtifactCaptureError(ArtifactCaptureStatus.UNSUPPORTED, "invalid_artifact_path")
    return normalized


def _artifact_deadline_remaining(deadline: float) -> float:
    if not isinstance(deadline, (int, float)) or isinstance(deadline, bool):
        raise ArtifactCaptureError(ArtifactCaptureStatus.UNSUPPORTED, "invalid_deadline")
    remaining = float(deadline) - time.monotonic()
    if not math.isfinite(float(deadline)) or remaining <= 0:
        raise ArtifactCaptureError(ArtifactCaptureStatus.UNSUPPORTED, "deadline_exceeded")
    return remaining


def _artifact_index_records(root: Path, path: str, deadline: float) -> tuple[str, ...]:
    remaining = _artifact_deadline_remaining(deadline)
    try:
        raw = _git(
            root,
            "--literal-pathspecs",
            "ls-files",
            "--stage",
            "-z",
            "--",
            path,
            timeout=remaining,
        )
        records = _index_records(raw)
    except subprocess.TimeoutExpired as exc:
        raise ArtifactCaptureError(ArtifactCaptureStatus.UNSUPPORTED, "deadline_exceeded") from exc
    except (OSError, RuntimeError, UnicodeError) as exc:
        raise ArtifactCaptureError(ArtifactCaptureStatus.UNSUPPORTED, "index_unavailable") from exc
    if any(record_path != path for _, _, _, record_path in records):
        raise ArtifactCaptureError(ArtifactCaptureStatus.UNSUPPORTED, "index_unavailable")
    try:
        ordered = sorted(
            records,
            key=lambda record: (int(record[2]), record[0], record[1], record[3]),
        )
    except ValueError as exc:
        raise ArtifactCaptureError(ArtifactCaptureStatus.UNSUPPORTED, "index_unavailable") from exc
    return tuple(
        f"{mode} {object_id} {stage}\t{record_path}"
        for mode, object_id, stage, record_path in ordered
    )


def _artifact_repository_identity(root: Path, deadline: float) -> RepositoryIdentity:
    _artifact_deadline_remaining(deadline)
    try:
        identity = resolve_repository_identity(root).repository_identity
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        raise ArtifactCaptureError(
            ArtifactCaptureStatus.UNSUPPORTED, "repository_identity_unavailable"
        ) from exc
    _artifact_deadline_remaining(deadline)
    if Path(identity.worktree_path) != root:
        raise ArtifactCaptureError(ArtifactCaptureStatus.UNSUPPORTED, "repository_root_mismatch")
    return identity


def _artifact_unsupported_reason(exc: CollectorSafetyError) -> str:
    """Map one ``CollectorSafetyError`` to a stable unsupported-reason code.

    Dispatches on exception class first (the source-of-truth signal) so the
    classifier does not silently misclassify a new error message that happens
    to share a substring with an existing case.
    """
    if isinstance(exc, CollectorNoFollowUnsupportedError):
        return "no_follow_unsupported"
    if isinstance(exc, CollectorByteLimitError):
        return "artifact_too_large"
    if isinstance(exc, CollectorNotRegularFileError):
        return "artifact_not_regular"
    if isinstance(exc, CollectorRootInvalidError):
        return "invalid_repository_root"
    if isinstance(exc, CollectorPathInvalidError):
        return "artifact_path_invalid"
    return "artifact_unavailable"


def capture_stable_artifact(
    repository_root: Path,
    artifact_path: str,
    *,
    deadline: float,
    max_attempts: int = 3,
    max_bytes: int = 1_000_000,
) -> StableArtifactCapture:
    """Capture one stable repository artifact within an absolute monotonic deadline."""

    normalized_path = _artifact_path(artifact_path)
    if (
        not isinstance(max_attempts, int)
        or isinstance(max_attempts, bool)
        or not 1 <= max_attempts <= _MAX_STABLE_ARTIFACT_ATTEMPTS
        or not isinstance(max_bytes, int)
        or isinstance(max_bytes, bool)
        or not 1 <= max_bytes <= _MAX_STABLE_ARTIFACT_BYTES
    ):
        raise ArtifactCaptureError(ArtifactCaptureStatus.UNSUPPORTED, "invalid_capture_limits")
    _artifact_deadline_remaining(deadline)
    if not isinstance(repository_root, Path) or repository_root.is_symlink():
        raise ArtifactCaptureError(ArtifactCaptureStatus.UNSUPPORTED, "invalid_repository_root")
    try:
        root = repository_root.resolve(strict=True)
    except OSError as exc:
        raise ArtifactCaptureError(
            ArtifactCaptureStatus.UNSUPPORTED, "invalid_repository_root"
        ) from exc
    if not root.is_dir():
        raise ArtifactCaptureError(ArtifactCaptureStatus.UNSUPPORTED, "invalid_repository_root")

    for _ in range(max_attempts):
        identity_before = _artifact_repository_identity(root, deadline)
        index_before = _artifact_index_records(root, normalized_path, deadline)
        _artifact_deadline_remaining(deadline)
        try:
            observed = _snapshot_facade.read_stable_contained_file(
                root, normalized_path, max_bytes=max_bytes
            )
        except CollectorMutationError:
            continue
        except CollectorSafetyError as exc:
            raise ArtifactCaptureError(
                ArtifactCaptureStatus.UNSUPPORTED, _artifact_unsupported_reason(exc)
            ) from exc
        _artifact_deadline_remaining(deadline)
        index_after = _artifact_index_records(root, normalized_path, deadline)
        identity_after = _artifact_repository_identity(root, deadline)
        if (
            identity_before.digest != identity_after.digest
            or identity_before.revision != identity_after.revision
            or index_before != index_after
        ):
            continue

        content_digest = f"sha256:{hashlib.sha256(observed.content).hexdigest()}"
        payload = {
            "repository_root": str(root),
            "repository_identity_digest": identity_after.digest,
            "revision": identity_after.revision,
            "artifact_path": normalized_path,
            "content_digest": content_digest,
            "size": observed.size,
            "mode": observed.mode,
            "index_records": index_after,
        }
        return StableArtifactCapture(
            repository_root=root,
            repository_identity_digest=identity_after.digest,
            revision=identity_after.revision,
            artifact_path=normalized_path,
            content=observed.content,
            content_digest=content_digest,
            size=observed.size,
            mode=observed.mode,
            index_records=index_after,
            snapshot_digest=qualified_digest(STABLE_ARTIFACT_DIGEST_DOMAIN, payload),
        )
    raise ArtifactCaptureError(ArtifactCaptureStatus.STALE, "artifact_changed_during_capture")


def stable_artifact_matches(
    start: StableArtifactCapture,
    current: StableArtifactCapture,
) -> bool:
    """Return whether a terminal recapture matches the original path authority."""

    return (
        start.repository_root == current.repository_root
        and start.artifact_path == current.artifact_path
        and start.snapshot_digest == current.snapshot_digest
    )
