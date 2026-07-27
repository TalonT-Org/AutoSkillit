"""Low-level filesystem primitives for context-admission ledger storage."""

from __future__ import annotations

import os
import stat
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;
CREATE TABLE streams (
    stream_id BLOB PRIMARY KEY,
    stream_key BLOB NOT NULL UNIQUE,
    genesis_envelope BLOB NOT NULL,
    state_envelope BLOB NOT NULL,
    aggregate_revision INTEGER NOT NULL,
    admission_sequence INTEGER NOT NULL,
    latest_journal_sequence INTEGER NOT NULL,
    health_status TEXT NOT NULL,
    failure_reason TEXT,
    reason_code TEXT
) STRICT;
CREATE TABLE journal_events (
    stream_id BLOB NOT NULL,
    journal_sequence INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    event_envelope BLOB NOT NULL,
    decision_envelope BLOB NOT NULL,
    expected_revision INTEGER NOT NULL,
    prior_aggregate_revision INTEGER NOT NULL,
    prior_admission_sequence INTEGER NOT NULL,
    resulting_aggregate_revision INTEGER NOT NULL,
    resulting_admission_sequence INTEGER NOT NULL,
    PRIMARY KEY (stream_id, journal_sequence),
    UNIQUE (stream_id, event_id),
    FOREIGN KEY (stream_id) REFERENCES streams(stream_id)
) STRICT;
CREATE TABLE effect_outbox (
    stream_id BLOB NOT NULL,
    journal_sequence INTEGER NOT NULL,
    effect_ordinal INTEGER NOT NULL,
    effect_envelope BLOB NOT NULL,
    PRIMARY KEY (stream_id, journal_sequence, effect_ordinal),
    FOREIGN KEY (stream_id, journal_sequence)
        REFERENCES journal_events(stream_id, journal_sequence)
) STRICT;
CREATE TABLE shadow_decisions (
    stream_id BLOB NOT NULL,
    journal_sequence INTEGER NOT NULL,
    shadow_envelope BLOB NOT NULL,
    PRIMARY KEY (stream_id, journal_sequence),
    FOREIGN KEY (stream_id, journal_sequence)
        REFERENCES journal_events(stream_id, journal_sequence)
) STRICT;
"""


def reconcile_initialization_links(
    path: Path,
    *,
    owner_id: int,
    file_mode: int,
    remove: bool,
) -> bool:
    """Find, and optionally remove, same-inode initialization link artifacts."""
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


def private_file_identity(
    path: Path,
    *,
    owner_id: int,
    file_mode: int,
) -> tuple[int, int] | None:
    """Return a stable private-file identity, or None when validation fails."""
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


def fsync_file(path: Path) -> None:
    """Synchronize one no-follow regular file descriptor."""
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


def unlink_initialization_artifact(path: Path) -> None:
    """Best-effort cleanup for an unpublished temporary database and journal."""
    for candidate in (path, Path(f"{path}-journal")):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass
