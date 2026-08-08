"""Canonical persisted and transported capture-failure field policy."""

from __future__ import annotations

import errno
import re
from enum import StrEnum
from typing import NamedTuple

from ._module_identity import register_module_aliases

register_module_aliases(__name__)

FAILURE_STAGE_MAX_BYTES = 64
FAILURE_DETAIL_MAX_BYTES = 240
FAILURE_STAGE_RE = re.compile(rf"^[a-z][a-z0-9_]{{0,{FAILURE_STAGE_MAX_BYTES - 1}}}$")


class CaptureFailureReason(StrEnum):
    ACTIVE_CAPACITY_EXHAUSTED = "ACTIVE_CAPACITY_EXHAUSTED"
    RETENTION_CAPACITY_EXHAUSTED = "RETENTION_CAPACITY_EXHAUSTED"
    EVIDENCE_CAPACITY_EXHAUSTED = "EVIDENCE_CAPACITY_EXHAUSTED"
    PROJECTED_COMPACTED_BYTES_EXHAUSTED = "PROJECTED_COMPACTED_BYTES_EXHAUSTED"
    HARD_LEDGER_CAPACITY_EXHAUSTED = "HARD_LEDGER_CAPACITY_EXHAUSTED"
    MIGRATION_BLOCKED = "MIGRATION_BLOCKED"
    LEDGER_INTEGRITY = "LEDGER_INTEGRITY"
    FILESYSTEM_AUTHORITY = "FILESYSTEM_AUTHORITY"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    FILESYSTEM_IO = "FILESYSTEM_IO"
    RECOVERY_CONTENDED = "RECOVERY_CONTENDED"
    SNAPSHOT_INTEGRITY = "SNAPSHOT_INTEGRITY"
    UNKNOWN_SETUP = "UNKNOWN_SETUP"


CAPACITY_FAILURE_REASONS = frozenset(
    {
        CaptureFailureReason.ACTIVE_CAPACITY_EXHAUSTED,
        CaptureFailureReason.RETENTION_CAPACITY_EXHAUSTED,
        CaptureFailureReason.EVIDENCE_CAPACITY_EXHAUSTED,
        CaptureFailureReason.PROJECTED_COMPACTED_BYTES_EXHAUSTED,
        CaptureFailureReason.HARD_LEDGER_CAPACITY_EXHAUSTED,
    }
)


def os_failure_reason(exc: OSError) -> CaptureFailureReason:
    if exc.errno in {errno.EACCES, errno.EPERM}:
        return CaptureFailureReason.PERMISSION_DENIED
    if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
        return CaptureFailureReason.FILESYSTEM_AUTHORITY
    return CaptureFailureReason.FILESYSTEM_IO


def runtime_failure_reason(exc: BaseException) -> CaptureFailureReason:
    failure_reason = getattr(exc, "failure_reason", None)
    if type(failure_reason) is CaptureFailureReason:
        return failure_reason
    reason = getattr(exc, "reason", None)
    if type(reason) is CaptureFailureReason:
        return reason
    if isinstance(exc, OSError):
        return os_failure_reason(exc)
    return CaptureFailureReason.UNKNOWN_SETUP


def normalize_failure_stage(value: str) -> str:
    normalized = "".join(
        character if character.isascii() and character.isalnum() else "_"
        for character in value.lower()
    ).strip("_")[:FAILURE_STAGE_MAX_BYTES]
    return normalized if FAILURE_STAGE_RE.fullmatch(normalized) else "capture_failure"


def normalize_failure_detail(value: str) -> str:
    normalized = " ".join(value.split()) or "capture failure"
    return normalized.encode("utf-8")[:FAILURE_DETAIL_MAX_BYTES].decode(
        "utf-8",
        errors="ignore",
    )


def valid_failure_stage(value: object) -> bool:
    return isinstance(value, str) and FAILURE_STAGE_RE.fullmatch(value) is not None


def valid_failure_detail(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value.encode("utf-8")) <= FAILURE_DETAIL_MAX_BYTES
    )


def valid_failure_reason(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    try:
        CaptureFailureReason(value)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# Failure disposition registry — A-I1 (workstream A)
# ---------------------------------------------------------------------------


class CaptureFailureDisposition(StrEnum):
    """Whether a failure that occurs after output verification may deliver that output.

    ``PRESERVE_OUTPUT`` *permits* delivering an existing checksum-verified
    snapshot with the child's real exit code.  It never manufactures output.
    ``DISCARD_OUTPUT`` forbids delivery even if a snapshot exists.

    The registry is an eligibility classification only — delivery additionally
    requires a live ``VerifiedCaptureSnapshot`` and a completed child process.
    No caller may treat a disposition alone as sufficient authority to deliver
    at an earlier, unverified stage.
    """

    PRESERVE_OUTPUT = "PRESERVE_OUTPUT"
    DISCARD_OUTPUT = "DISCARD_OUTPUT"


class CaptureFailureDispositionDef(NamedTuple):
    """One entry in the total failure-disposition registry."""

    reason: CaptureFailureReason
    disposition: CaptureFailureDisposition
    rationale: str


FAILURE_DISPOSITIONS: dict[CaptureFailureReason, CaptureFailureDispositionDef] = {
    CaptureFailureReason.ACTIVE_CAPACITY_EXHAUSTED: CaptureFailureDispositionDef(
        reason=CaptureFailureReason.ACTIVE_CAPACITY_EXHAUSTED,
        disposition=CaptureFailureDisposition.PRESERVE_OUTPUT,
        rationale="capacity bookkeeping — verified output is unaffected",
    ),
    CaptureFailureReason.RETENTION_CAPACITY_EXHAUSTED: CaptureFailureDispositionDef(
        reason=CaptureFailureReason.RETENTION_CAPACITY_EXHAUSTED,
        disposition=CaptureFailureDisposition.PRESERVE_OUTPUT,
        rationale="capacity bookkeeping — verified output is unaffected",
    ),
    CaptureFailureReason.EVIDENCE_CAPACITY_EXHAUSTED: CaptureFailureDispositionDef(
        reason=CaptureFailureReason.EVIDENCE_CAPACITY_EXHAUSTED,
        disposition=CaptureFailureDisposition.PRESERVE_OUTPUT,
        rationale="capacity bookkeeping — verified output is unaffected",
    ),
    CaptureFailureReason.PROJECTED_COMPACTED_BYTES_EXHAUSTED: CaptureFailureDispositionDef(
        reason=CaptureFailureReason.PROJECTED_COMPACTED_BYTES_EXHAUSTED,
        disposition=CaptureFailureDisposition.PRESERVE_OUTPUT,
        rationale="capacity bookkeeping — verified output is unaffected",
    ),
    CaptureFailureReason.HARD_LEDGER_CAPACITY_EXHAUSTED: CaptureFailureDispositionDef(
        reason=CaptureFailureReason.HARD_LEDGER_CAPACITY_EXHAUSTED,
        disposition=CaptureFailureDisposition.PRESERVE_OUTPUT,
        rationale="capacity bookkeeping — verified output is unaffected",
    ),
    CaptureFailureReason.MIGRATION_BLOCKED: CaptureFailureDispositionDef(
        reason=CaptureFailureReason.MIGRATION_BLOCKED,
        disposition=CaptureFailureDisposition.PRESERVE_OUTPUT,
        rationale="ledger migration — verified output is unaffected",
    ),
    CaptureFailureReason.LEDGER_INTEGRITY: CaptureFailureDispositionDef(
        reason=CaptureFailureReason.LEDGER_INTEGRITY,
        disposition=CaptureFailureDisposition.PRESERVE_OUTPUT,
        rationale="ledger-file integrity, not output integrity — verified output is unaffected",
    ),
    CaptureFailureReason.FILESYSTEM_AUTHORITY: CaptureFailureDispositionDef(
        reason=CaptureFailureReason.FILESYSTEM_AUTHORITY,
        disposition=CaptureFailureDisposition.PRESERVE_OUTPUT,
        rationale="filesystem authority — verified output is unaffected",
    ),
    CaptureFailureReason.PERMISSION_DENIED: CaptureFailureDispositionDef(
        reason=CaptureFailureReason.PERMISSION_DENIED,
        disposition=CaptureFailureDisposition.PRESERVE_OUTPUT,
        rationale="permission denied on ledger — verified output is unaffected",
    ),
    CaptureFailureReason.FILESYSTEM_IO: CaptureFailureDispositionDef(
        reason=CaptureFailureReason.FILESYSTEM_IO,
        disposition=CaptureFailureDisposition.PRESERVE_OUTPUT,
        rationale="filesystem I/O on ledger — verified output is unaffected",
    ),
    CaptureFailureReason.RECOVERY_CONTENDED: CaptureFailureDispositionDef(
        reason=CaptureFailureReason.RECOVERY_CONTENDED,
        disposition=CaptureFailureDisposition.PRESERVE_OUTPUT,
        rationale="recovery lock contended — verified output is unaffected",
    ),
    CaptureFailureReason.SNAPSHOT_INTEGRITY: CaptureFailureDispositionDef(
        reason=CaptureFailureReason.SNAPSHOT_INTEGRITY,
        disposition=CaptureFailureDisposition.DISCARD_OUTPUT,
        rationale="output integrity failure — checksum mismatch or tamper detection",
    ),
    CaptureFailureReason.UNKNOWN_SETUP: CaptureFailureDispositionDef(
        reason=CaptureFailureReason.UNKNOWN_SETUP,
        disposition=CaptureFailureDisposition.DISCARD_OUTPUT,
        rationale=(
            "unknown/unclassified failure — currently doubles as wire label for the "
            "verify-stage tamper detector; fail-closed until workstream C splits "
            "SNAPSHOT_INTEGRITY out"
        ),
    ),
}

# Import-time totality assertion — a missing or extra CaptureFailureReason
# member prevents the module from loading, so an unclassified reason cannot
# reach production.  Never bare ``assert`` (``-O`` strips it).
if set(FAILURE_DISPOSITIONS) != set(CaptureFailureReason):
    raise AssertionError(
        "FAILURE_DISPOSITIONS must cover exactly the CaptureFailureReason members: "
        f"missing={set(CaptureFailureReason) - set(FAILURE_DISPOSITIONS)}, "
        f"extra={set(FAILURE_DISPOSITIONS) - set(CaptureFailureReason)}"
    )
for _key, _entry in FAILURE_DISPOSITIONS.items():
    if _key != _entry.reason:
        raise AssertionError(
            f"FAILURE_DISPOSITIONS key {_key!r} does not match entry reason {_entry.reason!r}"
        )
    if not isinstance(_entry.disposition, CaptureFailureDisposition):
        raise AssertionError(
            f"FAILURE_DISPOSITIONS[{_key!r}].disposition is not a CaptureFailureDisposition"
        )
    if not _entry.rationale:
        raise AssertionError(f"FAILURE_DISPOSITIONS[{_key!r}].rationale is empty")
