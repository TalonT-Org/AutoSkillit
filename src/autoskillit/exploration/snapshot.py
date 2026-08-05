"""Atomic, bounded repository snapshot capture and pagination identities."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Literal

from autoskillit.core import RepositoryIdentity, RepositorySnapshot

from ._digest import qualified_digest
from .collectors._bounded import _open_contained_regular_file
from .identity import resolve_repository_identity
from .profile import RepositoryProfileActivation, activate_repository_profiles

SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_SCHEMA_ID = "autoskillit.repository-snapshot.v1"
SNAPSHOT_DIGEST_DOMAIN = b"autoskillit.repository-snapshot.v1\0"
PAGINATION_DIGEST_DOMAIN = b"autoskillit.exploration-page.v1\0"
DEFAULT_IGNORE_POLICY = "ignored-names-modes-collapsed-v1"


class SnapshotCaptureStatus(StrEnum):
    COMPLETE = "complete"
    STALE = "stale"
    TRUNCATED = "truncated"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SnapshotCaptureLimits:
    """Hard bounds applied before a snapshot can become publishable."""

    max_paths: int = 50_000
    max_file_bytes: int = 64 * 1024 * 1024
    max_total_bytes: int = 256 * 1024 * 1024
    git_timeout_seconds: int = 30

    def __post_init__(self) -> None:
        for name in (
            "max_paths",
            "max_file_bytes",
            "max_total_bytes",
            "git_timeout_seconds",
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
    """Internal path authority that keeps ignored-file bytes out of returned state."""

    state: RepositoryPathState
    hashed_bytes: int
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
    total_hashed_bytes: int
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
    start_identity: str = ""
    end_identity: str = ""
    validated_activation: RepositoryProfileActivation | None = None

    def __post_init__(self) -> None:
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


class _SnapshotTruncated(RuntimeError):
    pass


def _git(
    root: Path,
    *args: str,
    timeout: int,
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


def _hash_file(root: Path, relative_path: str, limits: SnapshotCaptureLimits) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    descriptor = _open_contained_regular_file(root, relative_path)
    try:
        while total <= limits.max_file_bytes:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, limits.max_file_bytes + 1 - total),
            )
            if not chunk:
                break
            total += len(chunk)
            if total > limits.max_file_bytes:
                raise _SnapshotTruncated(
                    f"file exceeds max_file_bytes: {Path(relative_path).name}"
                )
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return f"sha256:{digest.hexdigest()}", total


def _path_state(
    root: Path,
    relative_path: str,
    category: Literal["tracked", "untracked", "ignored"],
    limits: SnapshotCaptureLimits,
) -> _ObservedPath:
    path = root / relative_path
    try:
        metadata = path.lstat()
    except FileNotFoundError:
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
            0,
            "",
        )

    mode = stat.S_IMODE(metadata.st_mode)
    if stat.S_ISLNK(metadata.st_mode):
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
            len(payload),
            content_digest,
        )
    if stat.S_ISREG(metadata.st_mode):
        content_digest, size = _hash_file(root, relative_path, limits)
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
                size,
                content_digest,
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
            size,
            content_digest,
        )
    if stat.S_ISDIR(metadata.st_mode):
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
        0,
        "",
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


def _untracked_special_paths(root: Path) -> tuple[str, ...]:
    """Find non-regular worktree entries omitted by git's untracked listing."""
    paths: list[str] = []
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = sorted(name for name in names if name != ".git")
        for name in [*names, *sorted(files)]:
            candidate = Path(directory) / name
            mode = candidate.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
                paths.append(candidate.relative_to(root).as_posix())
    return tuple(paths)


def _capture_once(root: Path, limits: SnapshotCaptureLimits) -> CapturedRepositoryState:
    timeout = limits.git_timeout_seconds
    worktree_root = Path(
        _git(root, "rev-parse", "--show-toplevel", timeout=timeout)
        .decode("utf-8", errors="surrogateescape")
        .strip()
    ).resolve()
    if worktree_root != root:
        raise ValueError(f"root must be the concrete Git worktree root: {worktree_root}")
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
    head_result = _git(root, "rev-parse", "--verify", "HEAD", timeout=timeout, check=False)
    head = head_result.decode("ascii", errors="replace").strip()

    raw_index = _git(root, "ls-files", "--stage", "-z", timeout=timeout)
    index_records = _index_records(raw_index)
    tracked_paths = tuple(sorted({record[3] for record in index_records}))
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
            | set(_untracked_special_paths(root))
        )
    )
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
    path_count = len(tracked_paths) + len(untracked_paths) + len(ignored_paths)
    if path_count > limits.max_paths:
        raise _SnapshotTruncated(
            f"repository path count {path_count} exceeds max_paths {limits.max_paths}"
        )

    observations: list[_ObservedPath] = []
    for path in tracked_paths:
        observations.append(_path_state(root, path, "tracked", limits))
    for path in untracked_paths:
        observations.append(_path_state(root, path, "untracked", limits))
    # Ignored directories remain collapsed, while ignored regular-file content is
    # represented only by private identity material.
    for path in ignored_paths:
        observations.append(_path_state(root, path.rstrip("/"), "ignored", limits))

    total_hashed_bytes = sum(observation.hashed_bytes for observation in observations)
    if total_hashed_bytes > limits.max_total_bytes:
        raise _SnapshotTruncated(
            "repository content exceeds max_total_bytes "
            f"({total_hashed_bytes} > {limits.max_total_bytes})"
        )

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
        {"ignore_policy": DEFAULT_IGNORE_POLICY, "paths": working_payload},
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
        "ignore_policy": DEFAULT_IGNORE_POLICY,
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
        ignore_policy=DEFAULT_IGNORE_POLICY,
        tracked_paths=len(tracked_paths),
        untracked_paths=len(untracked_paths),
        ignored_paths=len(ignored_paths),
        missing_paths=sum(state.kind == "missing" for state in states),
        total_hashed_bytes=total_hashed_bytes,
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
            b"autoskillit.ignore-policy.v1\0", DEFAULT_IGNORE_POLICY
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
        )
    identity_start = None
    activation_start = None
    try:
        identity_start = resolve_repository_identity(resolved_root)
        activation_start = activate_repository_profiles(resolved_root, identity=identity_start)
        start = _capture_once(resolved_root, active_limits)
        end = _capture_once(resolved_root, active_limits)
        identity_end = resolve_repository_identity(resolved_root)
        activation_end = activate_repository_profiles(resolved_root, identity=identity_end)
    except _SnapshotTruncated as exc:
        if identity_start is None or activation_start is None:
            return SnapshotCaptureResult(
                status=SnapshotCaptureStatus.FAILED, snapshot=None, diagnostic=str(exc)
            )
        terminal = _terminal_snapshot(
            identity=identity_start.repository_identity,
            activation=activation_start,
            collector_manifest_digest=collector_manifest_digest,
            state="truncated",
            authority_digest="",
            reason=str(exc),
        )
        return SnapshotCaptureResult(
            status=SnapshotCaptureStatus.TRUNCATED, snapshot=terminal, diagnostic=str(exc)
        )
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        return SnapshotCaptureResult(
            status=SnapshotCaptureStatus.FAILED,
            snapshot=None,
            diagnostic=f"{type(exc).__name__}: {exc}",
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


def normalize_query(query: str) -> str:
    """Return the stable Unicode/whitespace form used by pagination authority."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    return " ".join(unicodedata.normalize("NFKC", query).split())


def pagination_identity(
    *,
    query: str,
    ordered_item_identities: Sequence[str],
    snapshot_identity: str,
    profile_identity: str,
    schema_identity: str,
    collector_manifest_digest: str,
) -> str:
    """Bind a page sequence to stable order and every invalidation authority."""
    if len(set(ordered_item_identities)) != len(ordered_item_identities):
        raise ValueError("pagination item identities must be unique")
    payload: Mapping[str, object] = {
        "normalized_query": normalize_query(query),
        "stable_order": list(ordered_item_identities),
        "total_count": len(ordered_item_identities),
        "snapshot_identity": snapshot_identity,
        "profile_identity": profile_identity,
        "schema_identity": schema_identity,
        "collector_manifest_digest": collector_manifest_digest,
    }
    for name, value in payload.items():
        if name not in {"stable_order", "total_count"} and not value:
            raise ValueError(f"pagination authority {name} must be non-empty")
    return qualified_digest(PAGINATION_DIGEST_DOMAIN, payload)
