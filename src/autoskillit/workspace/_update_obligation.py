"""Publication obligation journal — "republication owed" as a persisted fact.

Before this module, a crash between the update transaction's irreversible
pivot and a completed republication child left NO on-disk breadcrumb
distinguishing "never updated" from "republication still owed" — the exact
blind spot behind issue #4469's total session lockout (the incident's
republication child never ran, and nothing recorded that it was supposed
to).

Layer note (IL-1, workspace): the obligation must be writable by the update
transaction (cli/update/, IL-3) and readable by MCP server startup
(server/_lifespan.py, IL-3) without a server → cli import edge (REQ-ARCH-003b
forbids that). Living here — the same layer verify_install_state() already
lives at, and that server/_lifespan.py already imports unconditionally — is
the precedented, legal home for both callers.

Idiom: the same registry-plus-repair pattern as
``RETIRED_INSTALL_ARTIFACT_SHAPES`` (core/types/_type_constants.py) /
``reconcile_install_artifacts()`` (workspace/_install_state.py) — a durable
record that gives a repair loop something concrete to act on, instead of a
one-off in-memory decision that vanishes if the process dies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from autoskillit.core import ArtifactLease, get_logger, read_versioned_json, write_versioned_json

__all__ = [
    "PublicationObligation",
    "clear_obligation",
    "read_obligation",
    "update_obligation_expected_version",
    "write_obligation",
]

logger = get_logger(__name__)

_OBLIGATION_FILENAME = "update_obligation.json"
_OBLIGATION_SCHEMA_VERSION = 1


def _obligation_path(home: Path) -> Path:
    return home / ".autoskillit" / _OBLIGATION_FILENAME


def _obligation_lock_path(home: Path) -> Path:
    return home / ".autoskillit" / f"{_OBLIGATION_FILENAME}.lock"


@dataclass(frozen=True, slots=True)
class PublicationObligation:
    """A durable record that plugin republication may be owed.

    ``expected_version`` is ``None`` at write time by necessity — the new
    version is structurally unknowable before the upgrade subprocess runs —
    and is backfilled via ``update_obligation_expected_version()`` once the
    post-pivot version probe succeeds. It stays ``None`` for failures at or
    before that probe (the new version was never established); downstream
    repair code branches on this to decide which verification path applies
    (see ``attempt_obligation_repair()`` in ``cli/update/``).
    """

    previous_version: str
    expected_version: str | None
    written_at: str
    originating_phase: str


def write_obligation(
    home: Path, *, previous_version: str, originating_phase: str
) -> PublicationObligation:
    """Persist that publication is owed, before the irreversible mutation.

    Called once, at entry of ``UPGRADE_SUBPROCESS_GATE`` — immediately
    before the ``uv`` upgrade subprocess launches. Every failure/deferral
    strictly before this point mutates nothing and must never call this
    (a pending obligation against untouched state would trigger repair
    forever); every outcome at or after this point legitimately leaves one
    pending. Raises on write failure — the caller must map that to an
    aborted transaction *before* launching the upgrade subprocess: nothing
    is yet mutated, so refusing to proceed without a recorded breadcrumb is
    safe, and it upholds the invariant that the irreversible region is
    entered only with the breadcrumb already on disk.
    """
    record = PublicationObligation(
        previous_version=previous_version,
        expected_version=None,
        written_at=datetime.now(UTC).isoformat(),
        originating_phase=originating_phase,
    )
    with ArtifactLease.acquire_exclusive(_obligation_lock_path(home), blocking=True):
        _write(home, record)
    return record


def update_obligation_expected_version(
    home: Path,
    *,
    expected: PublicationObligation,
    expected_version: str,
) -> PublicationObligation | None:
    """Backfill ``expected_version`` once the post-pivot probe succeeds.

    A post-pivot journal touch that must never raise: failure to backfill
    leaves the field ``None``, which downstream repair code already treats
    as "version unknown" — a degraded-but-safe state, not a lost record.
    """
    try:
        with ArtifactLease.acquire_exclusive(_obligation_lock_path(home), blocking=True):
            current = read_obligation(home)
            if current != expected:
                return None
            updated = PublicationObligation(
                previous_version=current.previous_version,
                expected_version=expected_version,
                written_at=current.written_at,
                originating_phase=current.originating_phase,
            )
            _write(home, updated)
            return updated
    except Exception:
        logger.warning("update_obligation_backfill_failed", exc_info=True)
        return None


def read_obligation(home: Path) -> PublicationObligation | None:
    """Return the pending obligation, or ``None`` if none is recorded.

    A corrupt, unreadable, or schema-version-mismatched file reads as
    "pending, version unknown" — fail toward repair, which is idempotent —
    never silently as "no obligation". ``path.exists()`` is checked first so
    only a *missing* file (the common, safe case: no obligation was ever
    written) returns ``None``; every other failure mode of
    ``read_versioned_json`` (corrupt JSON, non-dict, schema drift) is
    collapsed into the degraded-but-pending record below instead.
    """
    path = _obligation_path(home)
    if not path.exists():
        return None
    data = read_versioned_json(path, _OBLIGATION_SCHEMA_VERSION, logger=logger)
    if data is None:
        logger.warning("update_obligation_read_corrupt")
        return _degraded_obligation()
    previous_version = data.get("previous_version")
    expected_version = data.get("expected_version")
    written_at = data.get("written_at")
    originating_phase = data.get("originating_phase")
    if (
        not isinstance(previous_version, str)
        or not previous_version.strip()
        or (expected_version is not None and not isinstance(expected_version, str))
        or not isinstance(written_at, str)
        or not written_at.strip()
        or not isinstance(originating_phase, str)
        or not originating_phase.strip()
    ):
        logger.warning("update_obligation_read_invalid_fields")
        return _degraded_obligation()
    normalized_expected_version = (
        expected_version.strip()
        if isinstance(expected_version, str) and expected_version.strip()
        else None
    )
    return PublicationObligation(
        previous_version=previous_version,
        expected_version=normalized_expected_version,
        written_at=written_at,
        originating_phase=originating_phase,
    )


def _degraded_obligation() -> PublicationObligation:
    return PublicationObligation(
        previous_version="unknown",
        expected_version=None,
        written_at="unknown",
        originating_phase="unknown",
    )


def clear_obligation(home: Path, *, expected: PublicationObligation) -> bool:
    """Compare-and-delete an obligation after verified publication. Never raises.

    Success-only: called from exactly two named sites — the update
    transaction's ``RESULT_FINALIZATION`` (child reported COMPLETED and
    post-update verification passed) and the shared CLI repair helper
    (``attempt_obligation_repair()``) after a verified repair. No other code
    clears it.
    """
    try:
        with ArtifactLease.acquire_exclusive(_obligation_lock_path(home), blocking=True):
            if read_obligation(home) != expected:
                return False
            _obligation_path(home).unlink(missing_ok=True)
            return True
    except Exception:
        logger.warning("update_obligation_clear_failed", exc_info=True)
        return False


def _write(home: Path, record: PublicationObligation) -> None:
    path = _obligation_path(home)
    write_versioned_json(
        path,
        {
            "previous_version": record.previous_version,
            "expected_version": record.expected_version,
            "written_at": record.written_at,
            "originating_phase": record.originating_phase,
        },
        schema_version=_OBLIGATION_SCHEMA_VERSION,
        strict_durability=True,
    )
