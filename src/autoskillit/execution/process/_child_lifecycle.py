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
        if not candidates and obs.source_event_id:
            candidates.append((obs.task_kind, "source", obs.source_event_id))
        return tuple(candidates)
    if obs.task_kind == "Bash":
        candidates = []
        for value in (obs.tool_use_id, obs.task_id, obs.background_task_id):
            if value:
                candidates.append((obs.task_kind, "alias", value))
        if not candidates and obs.source_event_id:
            candidates.append((obs.task_kind, "source", obs.source_event_id))
        return tuple(candidates)
    if obs.source_event_id:
        return ((obs.task_kind, "source", obs.source_event_id),)
    return ()


def _persistent_identity_keys(obs: ChildLifecycleObservation) -> tuple[str, ...]:
    """Return the persistent-identity key set used to correlate observations.

    Persistent identities survive tool_use_id reuse / loss on the same
    long-lived attempt: ``agent_id`` for Agent, ``background_task_id`` for
    Bash, and ``task_id`` for either when the kind-specific identity is
    blank. They are returned as bare strings (not tuples) so the
    coordinator can index by them independently of alias keys.
    """
    if obs.task_kind == "Agent":
        keys = []
        if obs.agent_id:
            keys.append(f"agent:{obs.agent_id}")
        if obs.task_id:
            keys.append(f"task:{obs.task_id}")
        return tuple(keys)
    if obs.task_kind == "Bash":
        keys = []
        if obs.background_task_id:
            keys.append(f"bg:{obs.background_task_id}")
        if obs.task_id:
            keys.append(f"task:{obs.task_id}")
        return tuple(keys)
    return ()


def _is_stale_for_existing(
    existing: _AttemptRecord, observation: ChildLifecycleObservation
) -> bool:
    """Return True when ``observation`` refers to a superseded generation of ``existing``.

    Detection is heuristic-only: when the live record has been replaced
    (``record.replaced`` set, typically via a prior ``replaces`` edge) AND
    both the live record and the incoming observation carry a non-blank
    ``tool_use_id`` AND those tool_use_ids differ, the new evidence is for
    the prior attempt generation. Routing it through normal collapse would
    collapse the live replacement; discarding it preserves the live state.
    """
    if not existing.replaced:
        return False
    live_id = existing.observation.tool_use_id
    incoming_id = observation.tool_use_id
    if not live_id or not incoming_id:
        return False
    return live_id != incoming_id


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
    _identity_index: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """Maps each persistent identity (``agent:<id>``, ``bg:<id>``,
    ``task:<id>``) to the alias key currently bound to the active record
    observation, so subsequent observations that lose their explicit alias
    can still correlate via a shared persistent identity."""

    def _find_existing(
        self, observation: ChildLifecycleObservation
    ) -> tuple[str, tuple[str, ...]] | None:
        """Return ``(bucket_name, aliases)`` matching this observation, if any.

        Scans every bucket (active/completed/unresolved) for an attempt
        that shares at least one alias key OR is the target of the
        observation's native ``replaces`` edge. Returns a tuple identifying
        which bucket to mutate plus the canonical alias tuple to use as
        the lookup key on the destination bucket.
        """
        for key in _alias_keys(observation):
            if key in self._attempts:
                return ("active", key)
            if key in self._completed:
                return ("completed", key)
            if key in self._unresolved_terminal:
                return ("unresolved_terminal", key)
        for identity in _persistent_identity_keys(observation):
            candidate_key = self._identity_index.get(identity)
            if candidate_key is not None:
                if candidate_key in self._attempts:
                    return ("active", candidate_key)
                if candidate_key in self._completed:
                    return ("completed", candidate_key)
                if candidate_key in self._unresolved_terminal:
                    return ("unresolved_terminal", candidate_key)
        if observation.replaces_native_uuid:
            for bucket in (self._attempts, self._completed, self._unresolved_terminal):
                for key, record in bucket.items():
                    if (
                        record.observation.replaced_by_native_uuid
                        == observation.replaces_native_uuid
                    ):
                        name = (
                            "active"
                            if bucket is self._attempts
                            else (
                                "completed" if bucket is self._completed else "unresolved_terminal"
                            )
                        )
                        return (name, key)
        return None

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

        existing_match = self._find_existing(observation)
        existing_key = existing_match[1] if existing_match else primary
        source_bucket = (
            self._attempts
            if existing_match is None or existing_match[0] == "active"
            else (
                self._completed if existing_match[0] == "completed" else self._unresolved_terminal
            )
        )
        existing_record = source_bucket.pop(existing_key, None)

        if observation.is_user_result:
            if existing_record is not None and _is_stale_for_existing(
                existing_record, observation
            ):
                # Late evidence for a superseded generation must not
                # resurrect / collapse the live replacement record.
                source_bucket[existing_key] = existing_record
                return
            if existing_record is None:
                # Late user_result arrives before any declaration. There
                # is no declared attempt to deliver against — record
                # the evidence as an unresolved obligation so the audit
                # trail surfaces why completion cannot be authorised.
                self._global_next_attempt_generation += 1
                attempt_generation = (
                    observation.attempt_generation or self._global_next_attempt_generation
                )
                late_record = _AttemptRecord(
                    observation=observation,
                    attempt_generation=attempt_generation,
                    replaced=bool(observation.replaced_by_native_uuid),
                )
                self._unresolved_terminal[existing_key] = late_record
                return
            existing_record.observation = observation
            existing_record.replaced = bool(observation.replaced_by_native_uuid)
            if observation.attempt_state == ChildAttemptState.COMPLETED:
                self._completed[existing_key] = existing_record
            elif observation.attempt_state in ATTEMPT_TERMINAL_STATES:
                self._unresolved_terminal[existing_key] = existing_record
            return

        if observation.attempt_state in ATTEMPT_TERMINAL_STATES:
            if existing_record is not None and _is_stale_for_existing(
                existing_record, observation
            ):
                source_bucket[existing_key] = existing_record
                return
            if existing_record is None:
                # Late terminal evidence without an open attempt: still
                # recorded as an outstanding obligation rather than
                # silently dropped, since the audit trail must show why
                # an obligation surfaced post-quiescence.
                self._global_next_attempt_generation += 1
                attempt_generation = (
                    observation.attempt_generation or self._global_next_attempt_generation
                )
                late_record = _AttemptRecord(
                    observation=observation,
                    attempt_generation=attempt_generation,
                    replaced=bool(observation.replaced_by_native_uuid),
                )
                self._unresolved_terminal[existing_key] = late_record
                return
            existing_record.observation = observation
            existing_record.replaced = bool(observation.replaced_by_native_uuid)
            if observation.attempt_state == ChildAttemptState.COMPLETED:
                self._completed[existing_key] = existing_record
            else:
                self._unresolved_terminal[existing_key] = existing_record
            return

        if existing_record is None:
            self._global_next_attempt_generation += 1
            attempt_generation = (
                observation.attempt_generation or self._global_next_attempt_generation
            )
            existing_record = _AttemptRecord(
                observation=observation,
                attempt_generation=attempt_generation,
                replaced=bool(observation.replaced_by_native_uuid),
            )
        else:
            existing_record.observation = observation
            existing_record.replaced = existing_record.replaced or bool(
                observation.replaced_by_native_uuid
            )
        self._attempts[existing_key] = existing_record
        for identity in _persistent_identity_keys(observation):
            self._identity_index[identity] = existing_key

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
                parent_turn_generation=generation,
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
