"""Async-child lifecycle reducer/coordinator (issue #4233).

Sole mutable owner of the per-invocation child-lifecycle state. Accepts
frozen observations from the per-line parser and emits frozen snapshots
that the race-resolution and termination paths consume. The reducer is
idempotent on duplicate / out-of-order input; the candidate-book keeps
the parent-marker eligibility gate honest without exposing helper maps
on the parser instance.

This module is intentionally UI-agnostic: it imports nothing from the
backends/claude.py layer and never inspects process trees. All child
identity is carried in the observations themselves.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from autoskillit.core import (
    ATTEMPT_TERMINAL_STATES,
    ChildAttemptState,
    ChildLifecycleObservation,
    ChildLifecycleSnapshot,
    CompletionCandidateSource,
    CompletionCandidateState,
)

__all__ = [
    "ChildLifecycleCoordinator",
    "tick",
]


def _observation_correlation_keys(obs: ChildLifecycleObservation) -> tuple[tuple[str, ...], ...]:
    """Return the canonical alias key set used to correlate an observation.

    The set is task-kind-aware: an Agent observation correlates only
    against Agent keys, a Bash observation only against Bash keys.
    Fallback to ``(task_kind, source_event_id)`` when no native alias is
    present keeps the reducer undefined-input-tolerant.
    """
    if obs.task_kind == "Agent":
        return (
            ("Agent", obs.tool_use_id),
            ("Agent", obs.task_id),
            ("Agent", obs.agent_id),
        )
    if obs.task_kind == "Bash":
        return (
            ("Bash", obs.tool_use_id),
            ("Bash", obs.task_id),
            ("Bash", obs.background_task_id),
        )
    return ((obs.task_kind, obs.source_event_id),)


@dataclass
class ChildLifecycleCoordinator:
    """Invocation-scoped reducer of child-lifecycle observations.

    One coordinator is the sole mutable owner of child lifecycle state
    per process invocation. The reducer is private; only its frozen
    snapshot escapes. Duplicate observations (matched by the canonical
    correlation key) collapse silently.
    """

    _active: dict[tuple[str, ...], ChildLifecycleObservation] = field(default_factory=dict)
    _completed: dict[tuple[str, ...], ChildLifecycleObservation] = field(default_factory=dict)
    _unresolved_terminal: dict[tuple[str, ...], ChildLifecycleObservation] = field(
        default_factory=dict
    )
    _candidates: dict[str, CompletionCandidateState] = field(default_factory=dict)

    def observe(self, observation: ChildLifecycleObservation) -> None:
        """Record one immutable observation in the appropriate bucket.

        If the observation is the user-result side of an attempt that
        is already terminal, the snapshot records it as a satisfied
        resolution. FAILED/CANCELLED/TIMED_OUT without a linked
        replacement generation are retained as unresolved-terminal
        obligations.
        """
        keys = _observation_correlation_keys(observation)
        primary = keys[0]

        if observation.is_user_result:
            existing = self._active.pop(primary, None)
            if existing is not None:
                if observation.attempt_state in ATTEMPT_TERMINAL_STATES:
                    self._unresolved_terminal[primary] = existing
                else:
                    self._completed[primary] = existing
            return

        if observation.attempt_state in ATTEMPT_TERMINAL_STATES:
            self._active.pop(primary, None)
            if observation.attempt_state == ChildAttemptState.COMPLETED:
                self._completed[primary] = observation
            else:
                self._unresolved_terminal[primary] = observation
            return

        self._active[primary] = observation

    def register_candidate(
        self,
        source_event_id: str,
        source: CompletionCandidateSource,
        initial: CompletionCandidateState,
    ) -> None:
        """One producer records one captured completion candidate.

        Source is encoded into the registry key so two producers cannot
        collide. Subsequent calls override prior state only when the
        candidate is the same logical record (matched by event UUID).
        """
        key = source_event_id or f"{source.value}:unknown"
        if key not in self._candidates:
            self._candidates[key] = initial

    def supersede_candidate(self, source_event_id: str) -> None:
        """Mark one captured candidate as SUPERSEDED after quiescence."""
        if not source_event_id:
            return
        if source_event_id in self._candidates:
            self._candidates[source_event_id] = CompletionCandidateState.SUPERSEDED

    def mark_candidate_eligible(self, source_event_id: str) -> None:
        """Promote one captured candidate to ELIGIBLE.

        Only the coordinator may set the global completion trigger; raw
        marker observations from the producers must route through here.
        """
        if not source_event_id:
            return
        self._candidates[source_event_id] = CompletionCandidateState.ELIGIBLE

    def snapshot(self) -> ChildLifecycleSnapshot:
        """Return the frozen snapshot for race resolution / diagnostics.

        The returned snapshot is independent of future ``observe()``
        calls — the coordinator's own dicts are private.
        """
        candidate_states: tuple[tuple[str, CompletionCandidateState], ...] = tuple(
            sorted(self._candidates.items())
        )
        return ChildLifecycleSnapshot(
            active_children=tuple(self._active.values()),
            completed_children=tuple(self._completed.values()),
            unresolved_terminal=tuple(self._unresolved_terminal.values()),
            has_active_children=bool(self._active),
            has_unresolved_terminal=bool(self._unresolved_terminal),
            candidate_states=candidate_states,
        )


def tick(
    coordinator: ChildLifecycleCoordinator,
    observations: Iterable[ChildLifecycleObservation],
) -> ChildLifecycleSnapshot:
    """One-shot reducer pass — observe a batch then return the snapshot.

    Convenience helper used by synchronous callers/tests. The async
    runner calls ``coordinator.observe(...)`` directly inside the
    Channel-A pump without going through this function.
    """
    for obs in observations:
        coordinator.observe(obs)
    return coordinator.snapshot()
