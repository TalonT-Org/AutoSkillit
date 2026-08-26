"""Atomic repository snapshot capture.

``_capture_once`` and the deadline/budget/reason plumbing it threads through.

Decomposed from the original ``exploration/snapshot.py`` per #4836. The capture
pipeline is preserved as one cohesive unit (per the #4756 capture-immunity
rectify that introduced the deadline thread, the two-byte-accounting split, and
the ``_CaptureAborted`` dispatch): splitting ``_capture_once`` from its direct
helpers would separate the budget plumbing from the single loop it threads
through.

Public surface: ``capture_repository_snapshot``, ``resolve_repository_path``.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Literal

from autoskillit.core import (
    RepositoryIdentity,
    RepositorySnapshot,
    SnapshotCaptureReason,
    SnapshotCaptureStatus,
    observe_path_mode,
)

from .._digest import qualified_digest
from ..collectors import open_contained_regular_file
from ..identity import resolve_repository_identity
from ..pagination import PAGINATION_DIGEST_DOMAIN
from ..profile import RepositoryProfileActivation, activate_repository_profiles
from ._capture_stage import _capture_stage, _stage
from ._records import (
    DEFAULT_IGNORE_POLICY,
    SNAPSHOT_DIGEST_DOMAIN,
    SNAPSHOT_SCHEMA_ID,
    SNAPSHOT_SCHEMA_VERSION,
    CapturedRepositoryState,
    RepositoryPathState,
    SnapshotCaptureLimits,
    SnapshotCaptureResult,
    _CaptureAborted,
    _ObservedPath,
)

__all__ = [
    # Helpers re-exported via the facade for monkeypatch sites
    "DEFAULT_IGNORE_POLICY",
    "_capture_once",
    "activate_repository_profiles",
    "capture_repository_snapshot",
    "observe_path_mode",
    "resolve_repository_identity",
    "resolve_repository_path",
    # Internal symbols used by sibling shards
    "_git",
    "_index_records",
]

# Capture the facade module so ``capture_repository_snapshot`` looks up
# ``_capture_once`` through the package attribute; the test suite monkeypatches
# that name on the facade and expects the patch to propagate. Without
# late-binding through the facade, a local import in this shard would capture a
# separate binding the patch cannot reach. The cycle resolves via
# ``sys.modules``: the facade module entry exists by the time ``_capture.py`` is
# loaded because the facade's ``from ._capture import`` runs first.
_snapshot_facade = sys.modules[__package__ or "autoskillit.exploration.snapshot"]


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise _CaptureAborted(
            SnapshotCaptureStatus.FAILED,
            SnapshotCaptureReason.CAPTURE_DEADLINE_EXCEEDED,
            "repository snapshot capture exceeded its deadline",
        )


def _git(
    root: Path,
    *args: str,
    timeout: float,
    check: bool = True,
) -> bytes:
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    result = subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        timeout=timeout,
        env=env,
    )
    if check and result.returncode != 0:
        diagnostic = result.stderr.decode("utf-8", errors="replace").strip()[:500]
        raise RuntimeError(f"git {' '.join(args)} failed: {diagnostic}")
    return result.stdout


def _decode_path(value: bytes) -> str:
    return value.decode("utf-8", errors="surrogateescape")


def _nul_paths(value: bytes) -> tuple[str, ...]:
    return tuple(_decode_path(item) for item in value.split(b"\0") if item)


def _index_records(value: bytes) -> tuple[tuple[str, str, str, str], ...]:
    records: list[tuple[str, str, str, str]] = []
    for raw in value.split(b"\0"):
        if not raw:
            continue
        metadata, separator, raw_path = raw.partition(b"\t")
        fields = metadata.decode("ascii", errors="strict").split(" ")
        if not separator or len(fields) != 3:
            raise RuntimeError("malformed git ls-files --stage record")
        records.append((fields[0], fields[1], fields[2], _decode_path(raw_path)))
    return tuple(records)


def _hash_file(
    root: Path,
    relative_path: str,
    limits: SnapshotCaptureLimits,
    *,
    enforce_cap: bool,
    deadline: float,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    descriptor = open_contained_regular_file(root, relative_path)
    try:
        if enforce_cap:
            while total <= limits.max_file_bytes:
                _check_deadline(deadline)
                chunk = os.read(
                    descriptor,
                    min(1024 * 1024, limits.max_file_bytes + 1 - total),
                )
                if not chunk:
                    break
                total += len(chunk)
                if total > limits.max_file_bytes:
                    raise _CaptureAborted(
                        SnapshotCaptureStatus.TRUNCATED,
                        SnapshotCaptureReason.FILE_BYTES_EXCEEDED,
                        f"file exceeds max_file_bytes: {Path(relative_path).name}",
                    )
                digest.update(chunk)
        else:
            # No cap: an ignored file's true bytes are still hashed in full for
            # identity purposes (see _ObservedPath), just never charged against
            # max_file_bytes/max_total_bytes. The deadline is the only bound left.
            while True:
                _check_deadline(deadline)
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                digest.update(chunk)
    finally:
        os.close(descriptor)
    return f"sha256:{digest.hexdigest()}", total


def _path_state(
    root: Path,
    relative_path: str,
    category: Literal["tracked", "untracked", "ignored"],
    limits: SnapshotCaptureLimits,
    *,
    deadline: float,
) -> _ObservedPath:
    path = root / relative_path
    raw_mode = _snapshot_facade.observe_path_mode(path)
    if raw_mode is None:
        return _ObservedPath(
            RepositoryPathState(
                path=relative_path,
                category=category,
                kind="missing",
                mode=0,
                size=0,
                content_digest="",
                symlink_target="",
            ),
            published_bytes=0,
            identity_bytes=0,
            identity_content_digest="",
        )

    mode = stat.S_IMODE(raw_mode)
    if stat.S_ISLNK(raw_mode):
        target = os.readlink(path)
        payload = os.fsencode(target)
        content_digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        return _ObservedPath(
            RepositoryPathState(
                path=relative_path,
                category=category,
                kind="symlink",
                mode=mode,
                size=len(payload),
                content_digest=content_digest,
                symlink_target=target,
            ),
            published_bytes=len(payload),
            identity_bytes=len(payload),
            identity_content_digest=content_digest,
        )
    if stat.S_ISREG(raw_mode):
        enforce_cap = category != "ignored"
        content_digest, size = _hash_file(
            root, relative_path, limits, enforce_cap=enforce_cap, deadline=deadline
        )
        if category == "ignored":
            return _ObservedPath(
                RepositoryPathState(
                    path=relative_path,
                    category=category,
                    kind="file",
                    mode=mode,
                    size=0,
                    content_digest="",
                    symlink_target="",
                ),
                published_bytes=0,
                identity_bytes=size,
                identity_content_digest=content_digest,
            )
        return _ObservedPath(
            RepositoryPathState(
                path=relative_path,
                category=category,
                kind="file",
                mode=mode,
                size=size,
                content_digest=content_digest,
                symlink_target="",
            ),
            published_bytes=size,
            identity_bytes=size,
            identity_content_digest=content_digest,
        )
    if stat.S_ISDIR(raw_mode):
        kind: Literal["directory", "other"] = "directory"
    else:
        kind = "other"
    return _ObservedPath(
        RepositoryPathState(
            path=relative_path,
            category=category,
            kind=kind,
            mode=mode,
            size=0,
            content_digest="",
            symlink_target="",
        ),
        published_bytes=0,
        identity_bytes=0,
        identity_content_digest="",
    )


def _state_payload(state: RepositoryPathState) -> dict[str, object]:
    return {
        "path": state.path,
        "category": state.category,
        "kind": state.kind,
        "mode": state.mode,
        "size": state.size,
        "content_digest": state.content_digest,
        "symlink_target": state.symlink_target,
    }


def _identity_state_payload(observation: _ObservedPath) -> dict[str, object]:
    """Render state for identity without exposing ignored regular-file hashes."""

    payload = _state_payload(observation.state)
    if observation.state.category == "ignored" and observation.state.kind == "file":
        payload["content_digest"] = observation.identity_content_digest
    return payload


def _untracked_special_paths(root: Path, *, ignored: frozenset[str]) -> tuple[str, ...]:
    """Find non-regular worktree entries omitted by git's untracked listing.

    ``ignored`` is git's own collapsed-ignored-entry set (directories and
    files — see the caller) so this walk does not descend into, or report
    entries under, a directory the ignore policy has already declared
    collapsed. A vanished entry (``observe_path_mode`` returning ``None``) is
    skipped rather than treated as a failure: the walk made no commitment
    about this path's presence the way a git-listed path does.
    """
    paths: list[str] = []
    for directory, names, files in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        candidates = sorted(name for name in names if name != ".git")
        names[:] = [
            name
            for name in candidates
            if (directory_path / name).relative_to(root).as_posix() not in ignored
        ]
        for name in [*names, *sorted(files)]:
            candidate = directory_path / name
            relative = candidate.relative_to(root).as_posix()
            if relative in ignored:
                continue
            mode = _snapshot_facade.observe_path_mode(candidate)
            if mode is None:
                continue
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
                paths.append(relative)
    return tuple(paths)


def _capture_once(
    root: Path, limits: SnapshotCaptureLimits, *, deadline: float
) -> CapturedRepositoryState:
    timeout = limits.git_timeout_seconds
    _check_deadline(deadline)
    worktree_root = Path(
        _git(root, "rev-parse", "--show-toplevel", timeout=timeout)
        .decode("utf-8", errors="surrogateescape")
        .strip()
    ).resolve()
    if worktree_root != root:
        raise ValueError(f"root must be the concrete Git worktree root: {worktree_root}")
    _check_deadline(deadline)
    common_git_dir = Path(
        _git(
            root,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
            timeout=timeout,
        )
        .decode("utf-8", errors="surrogateescape")
        .strip()
    ).resolve()
    _check_deadline(deadline)
    head_result = _git(root, "rev-parse", "--verify", "HEAD", timeout=timeout, check=False)
    head = head_result.decode("ascii", errors="replace").strip()

    _check_deadline(deadline)
    raw_index = _git(root, "ls-files", "--stage", "-z", timeout=timeout)
    index_records = _index_records(raw_index)
    tracked_paths = tuple(sorted({record[3] for record in index_records}))
    # Ask git which entries are already collapsed as ignored before walking the
    # worktree, so the walk can honour that decision instead of racing against it.
    _check_deadline(deadline)
    ignored_paths = tuple(
        sorted(
            _nul_paths(
                _git(
                    root,
                    "ls-files",
                    "--others",
                    "--ignored",
                    "--exclude-standard",
                    "--directory",
                    "--no-empty-directory",
                    "-z",
                    timeout=timeout,
                )
            )
        )
    )
    ignored_prune_set = frozenset(entry.rstrip("/") for entry in ignored_paths)
    _check_deadline(deadline)
    untracked_paths = tuple(
        sorted(
            set(
                _nul_paths(
                    _git(
                        root,
                        "ls-files",
                        "--others",
                        "--exclude-standard",
                        "-z",
                        timeout=timeout,
                    )
                )
            )
            | set(_untracked_special_paths(root, ignored=ignored_prune_set))
        )
    )
    # max_paths bounds every tracked, untracked, or ignored record eligible for
    # publication; a Git-collapsed ignored directory contributes one record. This
    # cardinality budget is independent of published-content byte budgets, which
    # exempt ignored regular-file content.
    path_count = len(tracked_paths) + len(untracked_paths) + len(ignored_paths)
    if path_count > limits.max_paths:
        raise _CaptureAborted(
            SnapshotCaptureStatus.TRUNCATED,
            SnapshotCaptureReason.PATH_COUNT_EXCEEDED,
            f"repository path count {path_count} exceeds max_paths {limits.max_paths}",
        )

    observations: list[_ObservedPath] = []
    for path in tracked_paths:
        _check_deadline(deadline)
        observations.append(_path_state(root, path, "tracked", limits, deadline=deadline))
    for path in untracked_paths:
        _check_deadline(deadline)
        observations.append(_path_state(root, path, "untracked", limits, deadline=deadline))
    # Ignored directories remain collapsed, while ignored regular-file content is
    # represented only by private identity material.
    for path in ignored_paths:
        _check_deadline(deadline)
        observations.append(
            _path_state(root, path.rstrip("/"), "ignored", limits, deadline=deadline)
        )

    total_published_bytes = sum(observation.published_bytes for observation in observations)
    if total_published_bytes > limits.max_total_bytes:
        raise _CaptureAborted(
            SnapshotCaptureStatus.TRUNCATED,
            SnapshotCaptureReason.TOTAL_BYTES_EXCEEDED,
            "repository content exceeds max_total_bytes "
            f"({total_published_bytes} > {limits.max_total_bytes})",
        )

    _check_deadline(deadline)
    index_payload = [
        {"mode": mode, "object_id": object_id, "stage": stage, "path": path}
        for mode, object_id, stage, path in index_records
    ]
    working_payload = [_identity_state_payload(observation) for observation in observations]
    states = [observation.state for observation in observations]
    raw_status = _git(
        root,
        "status",
        "--porcelain=v2",
        "--untracked-files=all",
        "--ignored=matching",
        "-z",
        timeout=timeout,
    )
    index_tree_digest = qualified_digest(b"autoskillit.index-tree.v1\0", index_payload)
    working_tree_digest = qualified_digest(
        b"autoskillit.working-tree.v1\0",
        {"ignore_policy": _snapshot_facade.DEFAULT_IGNORE_POLICY, "paths": working_payload},
    )
    status_digest = f"sha256:{hashlib.sha256(raw_status).hexdigest()}"
    snapshot_payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "worktree_root": os.fsdecode(os.fsencode(worktree_root)),
        "common_git_dir": os.fsdecode(os.fsencode(common_git_dir)),
        "head": head,
        "index_tree_digest": index_tree_digest,
        "working_tree_digest": working_tree_digest,
        "status_digest": status_digest,
        "ignore_policy": _snapshot_facade.DEFAULT_IGNORE_POLICY,
    }
    snapshot_identity = qualified_digest(SNAPSHOT_DIGEST_DOMAIN, snapshot_payload)
    return CapturedRepositoryState(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        worktree_root=str(worktree_root),
        common_git_dir=str(common_git_dir),
        head=head,
        index_tree_digest=index_tree_digest,
        working_tree_digest=working_tree_digest,
        status_digest=status_digest,
        ignore_policy=_snapshot_facade.DEFAULT_IGNORE_POLICY,
        tracked_paths=len(tracked_paths),
        untracked_paths=len(untracked_paths),
        ignored_paths=len(ignored_paths),
        missing_paths=sum(state.kind == "missing" for state in states),
        total_published_bytes=total_published_bytes,
        snapshot_identity=snapshot_identity,
        tracked_records=tuple(
            (state.path, state.content_digest)
            for state in states
            if state.category == "tracked" and state.kind != "missing"
        ),
        untracked_records=tuple(
            (state.path, state.content_digest) for state in states if state.category == "untracked"
        ),
        ignored_records=tuple(
            (state.path, state.content_digest) for state in states if state.category == "ignored"
        ),
        missing_records=tuple(
            (state.path, "missing") for state in states if state.kind == "missing"
        ),
        mode_records=tuple(
            (state.path, f"{state.mode:o}") for state in states if state.kind != "missing"
        ),
        symlink_records=tuple(
            (state.path, state.symlink_target) for state in states if state.kind == "symlink"
        ),
    )


def _snapshot_pagination_identity(
    captured: CapturedRepositoryState,
    activation: RepositoryProfileActivation,
    collector_manifest_digest: str,
) -> str:
    ordered_records = (
        *captured.tracked_records,
        *captured.untracked_records,
        *captured.ignored_records,
        *captured.missing_records,
    )
    return qualified_digest(
        PAGINATION_DIGEST_DOMAIN,
        {
            "stable_order": ordered_records,
            "snapshot_identity": captured.snapshot_identity,
            "profile_activation_digest": activation.activation_digest,
            "profile_versions": activation.profile_versions,
            "schema_version": SNAPSHOT_SCHEMA_ID,
            "collector_manifest_digest": collector_manifest_digest,
            "total_count": len(ordered_records),
        },
    )


def _complete_snapshot(
    captured: CapturedRepositoryState,
    identity: RepositoryIdentity,
    activation: RepositoryProfileActivation,
    collector_manifest_digest: str,
) -> RepositorySnapshot:
    ignore_policy_digest = qualified_digest(
        b"autoskillit.ignore-policy.v1\0", captured.ignore_policy
    )
    return RepositorySnapshot(
        identity=identity,
        tree_digest=captured.working_tree_digest,
        collector_manifest_digest=collector_manifest_digest,
        head_sha=captured.head,
        index_digest=captured.index_tree_digest,
        tracked_records=captured.tracked_records,
        untracked_records=captured.untracked_records,
        ignored_records=captured.ignored_records,
        missing_records=captured.missing_records,
        mode_records=captured.mode_records,
        symlink_records=captured.symlink_records,
        profile_versions=activation.profile_versions,
        profile_activation_digest=activation.activation_digest,
        schema_version=SNAPSHOT_SCHEMA_ID,
        ignore_policy_digest=ignore_policy_digest,
        pagination_identity=_snapshot_pagination_identity(
            captured, activation, collector_manifest_digest
        ),
        state="complete",
    )


def _terminal_snapshot(
    *,
    identity: RepositoryIdentity,
    activation: RepositoryProfileActivation,
    collector_manifest_digest: str,
    state: Literal["stale", "truncated"],
    authority_digest: str,
    reason: str,
) -> RepositorySnapshot:
    """Return an atomic terminal marker without exposing partial records."""
    return RepositorySnapshot(
        identity=identity,
        tree_digest=authority_digest,
        collector_manifest_digest=collector_manifest_digest,
        head_sha=identity.revision,
        profile_versions=activation.profile_versions,
        profile_activation_digest=activation.activation_digest,
        schema_version=SNAPSHOT_SCHEMA_ID,
        ignore_policy_digest=qualified_digest(
            b"autoskillit.ignore-policy.v1\0", _snapshot_facade.DEFAULT_IGNORE_POLICY
        ),
        pagination_identity="",
        state=state,
        stale=state == "stale",
        truncated=state == "truncated",
        truncation_reason=reason if state == "truncated" else None,
    )


def capture_repository_snapshot(
    root: str | Path,
    *,
    collector_manifest_digest: str,
    limits: SnapshotCaptureLimits | None = None,
) -> SnapshotCaptureResult:
    """Capture twice and publish only one stable, complete repository snapshot."""
    resolved_root = Path(root).resolve()
    active_limits = limits or SnapshotCaptureLimits()
    if not collector_manifest_digest:
        return SnapshotCaptureResult(
            status=SnapshotCaptureStatus.FAILED,
            snapshot=None,
            diagnostic="collector_manifest_digest must be non-empty",
            reason=SnapshotCaptureReason.MANIFEST_DIGEST_EMPTY,
        )
    # Computed once and shared across both _capture_once calls below: recomputing
    # per call would silently double the effective bound.
    deadline = time.monotonic() + active_limits.capture_deadline_seconds
    identity_start = None
    activation_start = None
    # Resolve monkeypatch helpers through the facade so test-suite patches
    # propagated via ``monkeypatch.setattr(snapshot_module, ...)`` reach here.
    resolve_identity = _snapshot_facade.resolve_repository_identity
    activate_profiles = _snapshot_facade.activate_repository_profiles
    try:
        identity_start = _stage(
            SnapshotCaptureReason.IDENTITY_UNRESOLVED,
            resolve_identity,
            resolved_root,
        )
        activation_start = _stage(
            SnapshotCaptureReason.PROFILE_ACTIVATION_FAILED,
            activate_profiles,
            resolved_root,
            identity=identity_start,
        )
        start = _capture_stage(resolved_root, active_limits, deadline=deadline)
        end = _capture_stage(resolved_root, active_limits, deadline=deadline)
        identity_end = _stage(
            SnapshotCaptureReason.IDENTITY_UNRESOLVED,
            resolve_identity,
            resolved_root,
        )
        activation_end = _stage(
            SnapshotCaptureReason.PROFILE_ACTIVATION_FAILED,
            activate_profiles,
            resolved_root,
            identity=identity_end,
        )
    except _CaptureAborted as exc:
        if exc.status is not SnapshotCaptureStatus.TRUNCATED:
            return SnapshotCaptureResult(
                status=exc.status, snapshot=None, diagnostic=exc.detail, reason=exc.reason
            )
        if identity_start is None or activation_start is None:
            return SnapshotCaptureResult(
                status=SnapshotCaptureStatus.FAILED,
                snapshot=None,
                diagnostic=exc.detail,
                reason=exc.reason,
            )
        terminal = _terminal_snapshot(
            identity=identity_start.repository_identity,
            activation=activation_start,
            collector_manifest_digest=collector_manifest_digest,
            state="truncated",
            authority_digest="",
            reason=exc.detail,
        )
        return SnapshotCaptureResult(
            status=SnapshotCaptureStatus.TRUNCATED,
            snapshot=terminal,
            diagnostic=exc.detail,
            reason=exc.reason,
        )
    identity_changed = (
        identity_start.repository_identity.digest != identity_end.repository_identity.digest
    )
    activation_changed = activation_start.activation_digest != activation_end.activation_digest
    if start.snapshot_identity != end.snapshot_identity or identity_changed or activation_changed:
        stale_digest = qualified_digest(
            b"autoskillit.stale-snapshot.v1\0",
            {
                "start": start.snapshot_identity,
                "end": end.snapshot_identity,
                "identity_start": identity_start.repository_identity.digest,
                "identity_end": identity_end.repository_identity.digest,
                "activation_start": activation_start.activation_digest,
                "activation_end": activation_end.activation_digest,
            },
        )
        terminal = _terminal_snapshot(
            identity=identity_end.repository_identity,
            activation=activation_end,
            collector_manifest_digest=collector_manifest_digest,
            state="stale",
            authority_digest=stale_digest,
            reason="repository changed during snapshot capture",
        )
        return SnapshotCaptureResult(
            status=SnapshotCaptureStatus.STALE,
            snapshot=terminal,
            diagnostic="repository changed during snapshot capture",
            reason=SnapshotCaptureReason.IDENTITY_DRIFT,
            start_identity=start.snapshot_identity,
            end_identity=end.snapshot_identity,
        )
    return SnapshotCaptureResult(
        status=SnapshotCaptureStatus.COMPLETE,
        snapshot=_complete_snapshot(
            end,
            identity_end.repository_identity,
            activation_end,
            collector_manifest_digest,
        ),
        start_identity=start.snapshot_identity,
        end_identity=end.snapshot_identity,
        validated_activation=activation_end,
    )


def resolve_repository_path(root: str | Path, relative_path: str) -> Path:
    """Resolve a repository-relative path and reject lexical or symlink escapes."""
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or "\x00" in relative_path
        or "\\" in relative_path
    ):
        raise ValueError("repository path must be a non-empty string without NUL")
    posix = PurePosixPath(relative_path)
    if posix.is_absolute() or ".." in posix.parts or posix.parts[0] == ".git":
        raise ValueError("repository path must stay within the worktree")
    resolved_root = Path(root).resolve(strict=True)
    candidate = (resolved_root / Path(*posix.parts)).resolve(strict=False)
    if not candidate.is_relative_to(resolved_root):
        raise ValueError("repository path resolves outside the worktree")
    return candidate
