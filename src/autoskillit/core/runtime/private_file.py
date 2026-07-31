"""Exception-neutral private-file validation and publication primitives."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

PrivateFileIdentity = tuple[int, int]
PrivateFileIdentityValidator = Callable[..., PrivateFileIdentity | None]


@dataclass(frozen=True, slots=True)
class PrivateSidecarIssue:
    """One deterministic sidecar validation failure."""

    kind: str
    path: Path


def private_file_identity(
    path: Path,
    *,
    owner_id: int,
    file_mode: int,
) -> PrivateFileIdentity | None:
    """Return a stable owner/mode/inode identity for one private regular file."""

    path_stat = path.lstat()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        descriptor_stat = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        not stat.S_ISREG(path_stat.st_mode)
        or path_stat.st_uid != owner_id
        or stat.S_IMODE(path_stat.st_mode) != file_mode
        or path_stat.st_nlink != 1
        or (path_stat.st_dev, path_stat.st_ino) != (descriptor_stat.st_dev, descriptor_stat.st_ino)
    ):
        return None
    return path_stat.st_dev, path_stat.st_ino


def private_sidecar_issue(
    database_path: Path,
    *,
    owner_id: int,
    file_mode: int,
    allow_regular: bool,
    identity_validator: PrivateFileIdentityValidator = private_file_identity,
) -> PrivateSidecarIssue | None:
    """Return the first invalid SQLite sidecar without imposing domain exceptions."""

    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = Path(f"{database_path}{suffix}")
        try:
            sidecar.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return PrivateSidecarIssue("unavailable", sidecar)
        if not allow_regular:
            return PrivateSidecarIssue("orphan", sidecar)
        try:
            identity = identity_validator(
                sidecar,
                owner_id=owner_id,
                file_mode=file_mode,
            )
        except FileNotFoundError:
            continue
        except OSError:
            return PrivateSidecarIssue("unavailable", sidecar)
        if identity is None:
            return PrivateSidecarIssue("insecure", sidecar)
    return None


def reconcile_initialization_links(
    path: Path,
    *,
    owner_id: int,
    file_mode: int,
    remove: bool,
) -> bool:
    """Find and optionally remove same-inode initialization link artifacts."""

    directory_fd = os.open(
        path.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        database_stat = os.stat(
            path.name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(database_stat.st_mode)
            or database_stat.st_uid != owner_id
            or stat.S_IMODE(database_stat.st_mode) != file_mode
            or database_stat.st_nlink <= 1
        ):
            return False
        prefix = f".{path.name}."
        suffix = ".tmp"
        found = False
        for name in os.listdir(directory_fd):
            if not name.startswith(prefix) or not name.endswith(suffix):
                continue
            token = name[len(prefix) : -len(suffix)]
            if len(token) != 24 or any(character not in "0123456789abcdef" for character in token):
                continue
            try:
                candidate_stat = os.stat(
                    name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                continue
            if (
                stat.S_ISREG(candidate_stat.st_mode)
                and candidate_stat.st_dev == database_stat.st_dev
                and candidate_stat.st_ino == database_stat.st_ino
                and candidate_stat.st_uid == owner_id
                and stat.S_IMODE(candidate_stat.st_mode) == file_mode
            ):
                found = True
                if remove:
                    os.unlink(name, dir_fd=directory_fd)
        return found
    finally:
        os.close(directory_fd)


def fsync_file(path: Path) -> None:
    """Synchronize one no-follow file descriptor."""

    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_directory(path: Path) -> None:
    """Synchronize one no-follow directory descriptor."""

    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def unlink_sqlite_initialization_artifacts(path: Path) -> None:
    """Best-effort cleanup for an unpublished SQLite database and journal."""

    for candidate in (path, Path(f"{path}-journal")):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass


def publish_private_file(temporary_path: Path, destination: Path) -> None:
    """Publish a fully initialized private file without overwriting a destination."""

    fsync_file(temporary_path)
    os.link(temporary_path, destination, follow_symlinks=False)
    unlink_sqlite_initialization_artifacts(temporary_path)
    fsync_directory(destination.parent)
