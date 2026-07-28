"""Bounded lifecycle sweep orchestration shared by lifecycle module identities."""

from __future__ import annotations

import os
from collections.abc import Callable, Collection, Iterable
from typing import Protocol

from ._types import CaptureCleanupOutcome, LockContended, ObservedArtifact, Tampered


class SweepRecord(Protocol):
    @property
    def capture_id(self) -> str: ...

    @property
    def state(self) -> object: ...

    @property
    def next_attempt_at(self) -> float: ...


def due_capture_ids(
    records: Iterable[SweepRecord],
    now: float,
    terminal_states: Collection[object],
) -> list[str]:
    due = [
        (record.next_attempt_at, record.capture_id)
        for record in records
        if record.state not in terminal_states and record.next_attempt_at <= now
    ]
    due.sort()
    return [capture_id for _next_attempt_at, capture_id in due]


def create_verified_recovery_link(
    *,
    link: Callable[[], None],
    observe: Callable[[], ObservedArtifact | None],
    rollback: Callable[[], None],
    sync: Callable[[], None],
) -> ObservedArtifact:
    link()
    try:
        linked = observe()
        if linked is None or linked.nlink != 2:
            if linked is not None:
                os.close(linked.fd)
            raise Tampered
        return linked
    except BaseException:
        try:
            rollback()
            sync()
        except FileNotFoundError:
            pass
        raise


def run_bounded_sweep(
    *,
    max_items: int,
    max_duration_seconds: float,
    monotonic: Callable[[], float],
    wall_clock: Callable[[], float],
    due_ids: Callable[[float], list[str]],
    sweep_one: Callable[[str], tuple[str, int, int]],
) -> CaptureCleanupOutcome:
    started = monotonic()
    examined = deleted = deleted_bytes = writer_live = 0
    not_due = tampered = errors = retry_count = 0
    try:
        pending = due_ids(wall_clock())
    except LockContended:
        return CaptureCleanupOutcome(
            remaining_due=1,
            duration=max(0.0, monotonic() - started),
        )
    lock_contended = False
    for capture_id in pending[:max_items]:
        if monotonic() - started >= max_duration_seconds:
            break
        try:
            result, logical_bytes, retries = sweep_one(capture_id)
        except LockContended:
            lock_contended = True
            break
        examined += 1
        deleted_bytes += logical_bytes
        retry_count += retries
        if result == "deleted":
            deleted += 1
        elif result == "writer_live":
            writer_live += 1
        elif result == "tampered":
            tampered += 1
        elif result == "error":
            errors += 1
        else:
            not_due += 1
    if lock_contended:
        remaining_due = max(1, len(pending) - examined)
    else:
        try:
            remaining_due = len(due_ids(wall_clock()))
        except LockContended:
            remaining_due = max(1, len(pending) - examined)
    return CaptureCleanupOutcome(
        examined=examined,
        deleted=deleted,
        deleted_bytes=deleted_bytes,
        writer_live=writer_live,
        not_due=not_due,
        tampered=tampered,
        errors=errors,
        retry_count=retry_count,
        remaining_due=remaining_due,
        duration=max(0.0, monotonic() - started),
    )
