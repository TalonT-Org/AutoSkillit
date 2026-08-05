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

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from autoskillit.core import atomic_write, get_logger

__all__ = [
    "PublicationObligation",
    "clear_obligation",
    "read_obligation",
    "update_obligation_expected_version",
    "write_obligation",
]

logger = get_logger(__name__)

_OBLIGATION_FILENAME = "update_obligation.json"


def _obligation_path(home: Path) -> Path:
    return home / ".autoskillit" / _OBLIGATION_FILENAME


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


def write_obligation(home: Path, *, previous_version: str, originating_phase: str) -> None:
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
    _write(home, record)


def update_obligation_expected_version(home: Path, *, expected_version: str) -> None:
    """Backfill ``expected_version`` once the post-pivot probe succeeds.

    A post-pivot journal touch that must never raise: failure to backfill
    leaves the field ``None``, which downstream repair code already treats
    as "version unknown" — a degraded-but-safe state, not a lost record.
    """
    try:
        current = read_obligation(home)
        if current is None:
            return
        _write(
            home,
            PublicationObligation(
                previous_version=current.previous_version,
                expected_version=expected_version,
                written_at=current.written_at,
                originating_phase=current.originating_phase,
            ),
        )
    except Exception:
        logger.warning("update_obligation_backfill_failed", exc_info=True)


def read_obligation(home: Path) -> PublicationObligation | None:
    """Return the pending obligation, or ``None`` if none is recorded.

    A corrupt/unreadable file reads as "pending, version unknown" — fail
    toward repair, which is idempotent — never silently as "no obligation".
    """
    path = _obligation_path(home)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("obligation record is not a JSON object")
        expected_version = data.get("expected_version")
        return PublicationObligation(
            previous_version=str(data.get("previous_version") or "unknown"),
            expected_version=(
                str(expected_version) if isinstance(expected_version, str) else None
            ),
            written_at=str(data.get("written_at") or "unknown"),
            originating_phase=str(data.get("originating_phase") or "unknown"),
        )
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        logger.warning("update_obligation_read_corrupt", exc_info=True)
        return PublicationObligation(
            previous_version="unknown",
            expected_version=None,
            written_at="unknown",
            originating_phase="unknown",
        )


def clear_obligation(home: Path) -> None:
    """Clear the obligation after verified publication. Never raises.

    Success-only: called from exactly two named sites — the update
    transaction's ``RESULT_FINALIZATION`` (child reported COMPLETED and
    post-update verification passed) and the shared CLI repair helper
    (``attempt_obligation_repair()``) after a verified repair. No other code
    clears it.
    """
    try:
        _obligation_path(home).unlink(missing_ok=True)
    except OSError:
        logger.warning("update_obligation_clear_failed", exc_info=True)


def _write(home: Path, record: PublicationObligation) -> None:
    path = _obligation_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(
        path,
        json.dumps(
            {
                "previous_version": record.previous_version,
                "expected_version": record.expected_version,
                "written_at": record.written_at,
                "originating_phase": record.originating_phase,
            }
        ),
    )
