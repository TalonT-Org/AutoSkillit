"""Async-child lifecycle reducer/coordinator (issue #4233 remediation).

Sole mutable owner of the per-invocation child-lifecycle state. Accepts
frozen observations from the per-line parser and emits frozen snapshots
that the race-resolution and termination paths consume. The reducer is
idempotent on duplicate / out-of-order input; the candidate-book keeps
the parent-marker eligibility gate honest without exposing helper maps
on the parser instance.

This module is intentionally UI-agnostic: it imports nothing from the
backends/claude.py layer and never inspects process trees. All child
identity is carried in the observations themselves.

Two distinct monotonic counters are tracked:

- ``attempt_generation`` — derived from native replacement edges
  (``replaces`` / ``replaced_by``). It governs which attempt generation
  is the active one for a given correlation key.

- ``parent_turn_generation`` — assigned monotonically in ingestion order
  for each distinct native parent-assistant UUID. It governs candidate
  eligibility: ``ELIGIBLE`` requires ``candidate.parent_turn_generation
  > last_deferred_parent_generation`` for the same UUID, and requires
  zero blocking / unresolved obligations.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from autoskillit.core import (
    ATTEMPT_TERMINAL_STATES,
    ChildAttemptState,
    ChildLifecycleObservation,
    ChildLifecycleSnapshot,
    CompletionCandidate,
    CompletionCandidateSource,
    CompletionCandidateState,
    ParentAssistantMarker,
    build_lifecycle_snapshot_from_attempts,
)

__all__ = [
    "ChildLifecycleCoordinator",
    "ChildLifecycleCoordinatorHandle",
    "make_coordinator_handle",
    "tick",
]


def _alias_keys(obs: ChildLifecycleObservation) -> tuple[tuple[str, ...], ...]:
    """Return the canonical alias key set used to correlate an observation.

    Each candidate key is a triple ``(task_kind, alias_kind, alias_value)``.
    The set is task-kind-aware: Agent observations correlate only against
    Agent keys, Bash observations only against Bash keys. Blank aliases are
    dropped so they cannot bridge two unrelated attempts.
    """
    if obs.task_kind == "Agent":
        candidates: list[tuple[str, ...]] = []
        for value in (obs.tool_use_id, obs.task_id, obs.agent_id):
            if value:
                candidates.append((obs.task_kind, "alias", value))
        if not candidates:
            candidates.append((obs.task_kind, "source", obs.source_event_id))
        return tuple(candidates)
    if obs.task_kind == "Bash":
        candidates = []
        for value in (obs.tool_use_id, obs.task_id, obs.background_task_id):
            if value:
                candidates.append((obs.task_kind, "alias", value))
        if not candidates:
            candidates.append((obs.task_kind, "source", obs.source_event_id))
        return tuple(candidates)
    if obs.source_event_id:
        return ((obs.task_kind, "source", obs.source_event_id),)
    return ()


@dataclass
class _AttemptRecord:
    """Private per-attempt mutable state owned by the coordinator."""

    observation: ChildLifecycleObservation
    attempt_generation: int = 0
    replaced: bool = False


@dataclass
class ChildLifecycleCoordinator:
    """Invocation-scoped reducer of child-lifecycle observations.

    One coordinator is the sole mutable owner of child lifecycle state
    per process invocation. The reducer is private; only its frozen
    snapshot escapes. Duplicate observations (matched by the canonical
    correlation key) collapse silently. Parent-assistant markers are
    the only path to a ``CompletionCandidate``; raw channel sources or
    process-exit signals may request catch-up but never synthesize one.
    """

    _attempts: dict[tuple[str, ...], _AttemptRecord] = field(default_factory=dict)
    _completed: dict[tuple[str, ...], _AttemptRecord] = field(default_factory=dict)
    _unresolved_terminal: dict[tuple[str, ...], _AttemptRecord] = field(default_factory=dict)
    _candidates: dict[str, CompletionCandidate] = field(default_factory=dict)
    _candidate_states: dict[str, CompletionCandidateState] = field(default_factory=dict)
    _parent_turn_counter: dict[str, int] = field(default_factory=dict)
    _last_deferred_parent_generation: dict[str, int] = field(default_factory=dict)
    _global_next_attempt_generation: int = 0

    def observe(self, observation: ChildLifecycleObservation) -> None:
        """Record one immutable observation in the appropriate bucket.

        Provenance ordering is preserved by the source byte offset: a
        marker earlier in the chunk may not complete before later child
        evidence in the same chunk is reduced. Terminal observations
        advance attempt generation when they carry a native replacement
        edge, otherwise they remain awaiting delivery.
        """
        keys = _alias_keys(observation)
        primary = keys[0] if keys else None
        if primary is None:
            return

        if observation.is_user_result:
            existing = self._attempts.pop(primary, None)
            if existing is not None:
                if observation.attempt_state in ATTEMPT_TERMINAL_STATES:
                    self._unresolved_terminal[primary] = existing
                else:
                    self._completed[primary] = existing
            return

        if observation.attempt_state in ATTEMPT_TERMINAL_STATES:
            existing = self._attempts.pop(primary, None)
            if existing is None:
                # Late terminal evidence without an open attempt: ignore
                # rather than creating a phantom attempt.
                return
            existing.observation = observation
            existing.replaced = bool(observation.replaced_by_native_uuid)
            if observation.attempt_state == ChildAttemptState.COMPLETED:
                self._completed[primary] = existing
            else:
                self._unresolved_terminal[primary] = existing
            return

        record = self._attempts.get(primary)
        if record is None:
            self._global_next_attempt_generation += 1
            attempt_generation = (
                observation.attempt_generation or self._global_next_attempt_generation
            )
            record = _AttemptRecord(
                observation=observation,
                attempt_generation=attempt_generation,
                replaced=bool(observation.replaced_by_native_uuid),
            )
        else:
            record.observation = observation
            record.replaced = bool(observation.replaced_by_native_uuid)
        self._attempts[primary] = record

    def register_parent_marker(self, marker: ParentAssistantMarker) -> CompletionCandidate:
        """Record one parent-assistant marker and synthesize its candidate.

        Blank or malformed native UUIDs are dropped without synthesizing
        a candidate — markers from text content, channel, session ID,
        fingerprint, or the literal string ``"unknown"`` cannot bridge
        to a candidate.
        """
        uuid = marker.native_uuid.strip()
        if not uuid:
            raise ValueError("parent_marker_native_uuid_blank")
        if uuid.lower() == "unknown":
            raise ValueError("parent_marker_native_uuid_unknown")

        generation = self._parent_turn_counter.get(uuid, 0) + 1
        self._parent_turn_counter[uuid] = generation
        candidate = CompletionCandidate(
            candidate_id=uuid,
            parent_turn_generation=generation,
            sources=(CompletionCandidateSource.CHANNEL_A,),
            native_message_id=marker.message_id,
            byte_offset=marker.byte_offset,
            backend_session_id=marker.backend_session_id,
        )
        existing = self._candidates.get(uuid)
        if existing is None:
            self._candidates[uuid] = candidate
        else:
            merged_sources = tuple({*existing.sources, *candidate.sources})
            self._candidates[uuid] = CompletionCandidate(
                candidate_id=existing.candidate_id,
                parent_turn_generation=existing.parent_turn_generation,
                sources=merged_sources,
                native_message_id=existing.native_message_id or candidate.native_message_id,
                byte_offset=existing.byte_offset or candidate.byte_offset,
                backend_session_id=existing.backend_session_id or candidate.backend_session_id,
            )
        self._candidate_states.setdefault(uuid, CompletionCandidateState.DEFERRED)
        return self._candidates[uuid]

    def supersede_candidate(self, candidate_id: str) -> None:
        """Move one captured candidate to ``SUPERSEDED``.

        Deferred candidates become superseded after their obligations
        fail and never reactivate.
        """
        if not candidate_id:
            return
        if candidate_id in self._candidate_states:
            self._candidate_states[candidate_id] = CompletionCandidateState.SUPERSEDED

    def evaluate_candidate(self, candidate_id: str) -> CompletionCandidate | None:
        """Promote a candidate to ``ELIGIBLE`` when all obligations are clear.

        Returns the eligible candidate, or ``None`` when the candidate
        remains deferred or has been superseded. A fresh post-quiescence
        candidate encountering unresolved-terminal work returns ``None``
        and signals ``CHILD_WORK_FAILED`` via the actor decision.
        """
        if not candidate_id:
            return None
        candidate = self._candidates.get(candidate_id)
        if candidate is None:
            return None
        state = self._candidate_states.get(candidate_id, CompletionCandidateState.DEFERRED)
        if state != CompletionCandidateState.DEFERRED:
            return None

        last_deferred = self._last_deferred_parent_generation.get(candidate_id, 0)
        if candidate.parent_turn_generation <= last_deferred:
            return None

        if self._attempts or self._unresolved_terminal:
            # Record the deferred generation so a later parent-turn
            # generation can become ELIGIBLE.
            self._last_deferred_parent_generation[candidate_id] = candidate.parent_turn_generation
            return None

        self._candidate_states[candidate_id] = CompletionCandidateState.ELIGIBLE
        return candidate

    def note_child_work_failed(self, candidate_id: str) -> None:
        """Mark that a fresh candidate encountered unresolved-terminal work.

        Records the parent-turn generation so a still-fresh candidate
        does not immediately become eligible on the next tick. The actor
        surfaces ``LifecycleDecision.CHILD_WORK_FAILED`` for this case.
        """
        candidate = self._candidates.get(candidate_id)
        if candidate is None:
            return
        self._last_deferred_parent_generation[candidate_id] = candidate.parent_turn_generation

    def snapshot(self) -> ChildLifecycleSnapshot:
        """Return the frozen snapshot for race resolution / diagnostics.

        The returned snapshot is independent of future ``observe()``
        calls — the coordinator's own dicts are private. Eligibility is
        not decided here; the actor evaluates after catch-up.
        """
        candidate_states: tuple[tuple[str, CompletionCandidateState], ...] = tuple(
            sorted(self._candidate_states.items())
        )
        active = tuple(record.observation for record in self._attempts.values())
        completed = tuple(record.observation for record in self._completed.values())
        unresolved = tuple(record.observation for record in self._unresolved_terminal.values())
        last_deferred = max(self._last_deferred_parent_generation.values(), default=0)
        return build_lifecycle_snapshot_from_attempts(
            active=active,
            completed=completed,
            unresolved_terminal=unresolved,
            candidate_states=candidate_states,
            eligible_candidate=None,
            last_deferred_parent_generation=last_deferred,
        )


@dataclass
class ChildLifecycleCoordinatorHandle:
    """Thin handle exposing only the methods the actor needs.

    The handle is the sole surface the actor (and tests) call into.
    Returning a frozen handle rather than the coordinator itself keeps
    the producer side free of any reducer mutation rights.
    """

    coordinator: ChildLifecycleCoordinator

    def observe(self, observation: ChildLifecycleObservation) -> None:
        self.coordinator.observe(observation)

    def register_parent_marker(self, marker: ParentAssistantMarker) -> CompletionCandidate:
        return self.coordinator.register_parent_marker(marker)

    def supersede_candidate(self, candidate_id: str) -> None:
        self.coordinator.supersede_candidate(candidate_id)

    def evaluate_candidate(self, candidate_id: str) -> CompletionCandidate | None:
        return self.coordinator.evaluate_candidate(candidate_id)

    def note_child_work_failed(self, candidate_id: str) -> None:
        self.coordinator.note_child_work_failed(candidate_id)

    def snapshot(self) -> ChildLifecycleSnapshot:
        return self.coordinator.snapshot()


def make_coordinator_handle() -> ChildLifecycleCoordinatorHandle:
    """Build a fresh handle for one invocation."""
    return ChildLifecycleCoordinatorHandle(coordinator=ChildLifecycleCoordinator())


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
