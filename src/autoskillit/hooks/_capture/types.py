"""Lifecycle result and internal signal types."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["CaptureCleanupOutcome"]


@dataclass(frozen=True, slots=True)
class CaptureCleanupOutcome:
    examined: int = 0
    deleted: int = 0
    deleted_bytes: int = 0
    writer_live: int = 0
    not_due: int = 0
    tampered: int = 0
    errors: int = 0
    retry_count: int = 0
    remaining_due: int = 0
    duration: float = 0.0


@dataclass(frozen=True, slots=True)
class ObservedArtifact:
    fd: int
    identity: tuple[int, int]
    nlink: int
    size: int


class WriterLive(Exception):
    pass


class Tampered(Exception):
    pass
