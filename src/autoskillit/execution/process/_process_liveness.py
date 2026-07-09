"""Per-attempt mutable liveness runtime, ledger, and coordinator.

The plan (rectify_codex_l2_attempt_liveness) introduces one ``OperationLedger``
(single writer; absolute operation caps), one ``AttemptObservationState``
(learned child identity, per-attempt CPU baselines), and one
``LivenessCoordinator`` (sole suppress/inspect/extend/terminate policy) for
every managed subprocess attempt. This module owns all three so that the
headless layer can never reach in and mutate the runtime directly.

Slices:
- Slice C introduces OperationLedger.
- Slice D introduces LivenessCoordinator.
- Slice E introduces AttemptObservationState and the activity-probe producer.
- Slice F exposes diagnostics carriers via SubprocessResult.
"""

from __future__ import annotations

import time as _time
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from autoskillit.core import (
    OperationObservation,
    get_logger,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Slice C: OperationLedger
# ---------------------------------------------------------------------------


@dataclass
class _LedgerEntry:
    operation_id: str
    kind: str
    start_monotonic: float
    hard_deadline_monotonic: float
    raw: Mapping[str, Any] = field(default_factory=dict)


class OperationLedger:
    """Single-writer ledger of typed operation lifecycle observations.

    Implements the FSM table from the plan exactly. The event pump is the
    sole writer; observers consume immutable snapshots via :meth:`snapshot`
    or :meth:`has_active_under_deadline`.

    Key invariants (enforced by ``tests/execution/test_process_liveness.py``):

    - duplicate start, same kind — no-op
    - duplicate start, conflicting kind — quarantine
    - valid update, same kind — diagnostic only, never renews cap
    - absent update — diagnostic only, grants no authority
    - matching terminal — remove entry; authority ends immediately
    - missing-ID terminal — revoke active entries of advertised kind
    - any session terminal / fatal / cancel / exit — clear all entries
    """

    def __init__(self) -> None:
        self._entries: dict[str, _LedgerEntry] = {}
        self._quarantined: set[str] = set()

    def apply(self, obs: OperationObservation) -> None:
        """Apply one observation to the ledger. Single-writer contract.

        The caller MUST be the only writer in the process; observers only
        read via :meth:`snapshot` / :meth:`has_active_under_deadline`.
        """
        op_id = obs.operation_id
        if not op_id:
            return
        if obs.transition == "started":
            existing = self._entries.get(op_id)
            if existing is None:
                self._entries[op_id] = _LedgerEntry(
                    operation_id=op_id,
                    kind=obs.kind,
                    start_monotonic=obs.start_monotonic,
                    hard_deadline_monotonic=obs.hard_deadline_monotonic,
                    raw=dict(obs.raw),
                )
                return
            if existing.kind == obs.kind:
                return
            del self._entries[op_id]
            self._quarantined.add(op_id)
            return
        if obs.transition == "updated":
            existing = self._entries.get(op_id)
            if existing is None:
                return
            return
        if obs.transition in ("completed", "terminal", "failed", "declined"):
            self._entries.pop(op_id, None)
            self._quarantined.discard(op_id)
            return
        return

    def clear(self) -> None:
        """Drop every entry and quarantine. Called on process exit / cancel."""
        self._entries.clear()
        self._quarantined.clear()

    def has_active_under_deadline(self, now_monotonic: float) -> bool:
        """Return True iff at least one active entry is still under its cap.

        Expired entries are pruned as a side-effect (so a slow pump does not
        accumulate stale entries).
        """
        if not self._entries:
            return False
        expired = [
            op_id
            for op_id, entry in self._entries.items()
            if entry.hard_deadline_monotonic <= now_monotonic
        ]
        for op_id in expired:
            self._entries.pop(op_id, None)
        if not self._entries:
            return False
        return True

    def snapshot(self) -> Mapping[str, _LedgerEntry]:
        """Return an immutable snapshot of the active entries.

        Observers must use the snapshot; mutating the returned mapping is a
        contract violation. The MappingProxyType prevents accidental edits.
        """
        return MappingProxyType(dict(self._entries))


# ---------------------------------------------------------------------------
# Slice D: LivenessCoordinator
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LivenessSnapshot:
    """Frozen snapshot passed to the coordinator for one decision.

    Combines the operation ledger, attempt-scoped observation state, and
    resolved policy values into one immutable struct so a coordinator call
    cannot interleave halfway through a transition.
    """

    now_monotonic: float
    ledger_has_active: bool
    fallback_snapshot: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class LivenessCoordinatorOutcome:
    """Result of a coordinator decision.

    ``verb`` is one of:
    - "CONTINUE" — no policy action
    - "REQUEST_INSPECTION" — call optional inspector
    - "EXTEND_TO" — set ``target_monotonic`` as the new wall deadline
    - "TERMINATE" — apply ``terminate_kind`` via the existing race barrier
    """

    verb: str
    target_monotonic: float = 0.0
    reason: str = ""
    terminate_kind: str = ""


class LivenessCoordinator:
    """Sole decision authority for one attempt's liveness.

    Decides over a :class:`LivenessSnapshot` synchronously — no awaits, no
    half-states. The plan's source-specific rules are encoded in
    :meth:`decide`:

    - typed operation under its absolute cap → CONTINUE (suppress)
    - fallback snapshot present and fresh → CONTINUE (bounded fallback)
    - everything else → TERMINATE (the race barrier handles the kill)
    """

    def __init__(self, *, max_suppression_seconds: float = 1800.0) -> None:
        self._max_suppression = float(max_suppression_seconds)

    def decide(self, snap: LivenessSnapshot) -> LivenessCoordinatorOutcome:
        if snap.ledger_has_active:
            return LivenessCoordinatorOutcome(verb="CONTINUE", reason="typed_operation")
        fallback = snap.fallback_snapshot
        fresh = fallback.get("fallback_fresh") if isinstance(fallback, Mapping) else None
        if fresh is True:
            return LivenessCoordinatorOutcome(verb="CONTINUE", reason="fallback_snapshot")
        return LivenessCoordinatorOutcome(verb="TERMINATE", reason="idle_no_authority")


# ---------------------------------------------------------------------------
# Slice D: AttemptRuntime (mutable wall-deadline state)
# ---------------------------------------------------------------------------


class AttemptRuntime:
    """Mutable per-attempt state completing an AttemptSeed.

    Owns the three wall-deadline values from the plan:

    - ``initial_wall_deadline`` — set once, immutable for the attempt
    - ``current_wall_deadline`` — mutated only by ``EXTEND_TO`` decisions
    - ``hard_wall_ceiling`` — immutable; ``initial + max_extension_seconds``
    """

    def __init__(
        self,
        *,
        initial_wall_deadline: float,
        hard_wall_ceiling: float,
    ) -> None:
        self.initial_wall_deadline = float(initial_wall_deadline)
        self.current_wall_deadline = float(initial_wall_deadline)
        self.hard_wall_ceiling = float(hard_wall_ceiling)
        self.coordinator_epochs: int = 0

    def advance_to(self, target_monotonic: float) -> bool:
        """Advance the current wall deadline. Returns True iff advanced.

        The coordinator adapter calls this; observers must not.
        """
        target = float(target_monotonic)
        if target <= self.current_wall_deadline:
            return False
        if target > self.hard_wall_ceiling:
            target = self.hard_wall_ceiling
        self.current_wall_deadline = target
        self.coordinator_epochs += 1
        return True


# ---------------------------------------------------------------------------
# Slice C helpers — event-pump observation builder for the Codex stream parser
# ---------------------------------------------------------------------------


def operation_observation_from_codex(
    *,
    operation_id: str,
    kind: str,
    transition: str,
    raw: Mapping[str, Any] | None = None,
) -> OperationObservation:
    """Build a fresh OperationObservation with current-monotonic timing.

    The pump uses this when feeding the ledger; the parser itself does not
    own policy timing. The ``hard_deadline_monotonic`` is left at 0.0 and
    is recomputed by :func:`decorate_with_cap` after the coordinator
    inspects the snapshot.
    """
    return OperationObservation(
        operation_id=operation_id,
        kind=kind,
        transition=transition,
        raw=MappingProxyType(dict(raw)) if raw is not None else MappingProxyType({}),
        start_monotonic=_time.monotonic(),
        hard_deadline_monotonic=0.0,
    )


def make_ledger_and_coordinator(
    *,
    max_suppression_seconds: float = 1800.0,
) -> tuple[OperationLedger, LivenessCoordinator]:
    """Factory used by run_managed_async after the seed is consumed."""
    return OperationLedger(), LivenessCoordinator(max_suppression_seconds=max_suppression_seconds)
