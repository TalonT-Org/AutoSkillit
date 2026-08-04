"""Canonical persisted and transported capture-failure field policy."""

from __future__ import annotations

import errno
import re
from enum import StrEnum

from ._module_identity import register_module_aliases

register_module_aliases(__name__)

FAILURE_STAGE_MAX_BYTES = 64
FAILURE_DETAIL_MAX_BYTES = 240
FAILURE_STAGE_RE = re.compile(rf"^[a-z][a-z0-9_]{{0,{FAILURE_STAGE_MAX_BYTES - 1}}}$")


class CaptureFailureReason(StrEnum):
    ACTIVE_CAPACITY_EXHAUSTED = "ACTIVE_CAPACITY_EXHAUSTED"
    RETENTION_CAPACITY_EXHAUSTED = "RETENTION_CAPACITY_EXHAUSTED"
    FORENSIC_EVIDENCE_EXHAUSTED = "FORENSIC_EVIDENCE_EXHAUSTED"
    PROJECTED_COMPACTED_BYTES_EXHAUSTED = "PROJECTED_COMPACTED_BYTES_EXHAUSTED"
    HARD_LEDGER_CAPACITY_EXHAUSTED = "HARD_LEDGER_CAPACITY_EXHAUSTED"
    MIGRATION_BLOCKED = "MIGRATION_BLOCKED"
    LEDGER_INTEGRITY = "LEDGER_INTEGRITY"
    FILESYSTEM_AUTHORITY = "FILESYSTEM_AUTHORITY"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    FILESYSTEM_IO = "FILESYSTEM_IO"
    RECOVERY_CONTENDED = "RECOVERY_CONTENDED"
    UNKNOWN_SETUP = "UNKNOWN_SETUP"


def valid_failure_reason(value: object) -> bool:
    if not isinstance(value, (str, CaptureFailureReason)):
        return False
    try:
        CaptureFailureReason(value)
    except ValueError:
        return False
    return True


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
    if type(exc).__name__ in {
        "CaptureLedgerError",
        "CaptureLifecycleError",
        "CaptureTransitionCommittedError",
    }:
        return CaptureFailureReason.LEDGER_INTEGRITY
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
