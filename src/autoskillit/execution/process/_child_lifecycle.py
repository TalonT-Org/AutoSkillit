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
from dataclasses import dataclass, field, replace

from autoskillit.core import (
    ATTEMPT_TERMINAL_STATES,
    CandidateSighting,
    ChildAttemptState,
    ChildLifecycleObservation,
    ChildLifecycleSnapshot,
    CompletionCandidate,
    CompletionCandidateSource,
    CompletionCandidateState,
    LifecycleEvidenceIssue,
    LifecycleEvidenceIssueKind,
    LifecycleEvidenceResolution,
    ParentAssistantMarker,
    build_lifecycle_snapshot_from_attempts,
)

__all__ = [
    "ChildLifecycleCoordinator",
    "ChildLifecycleCoordinatorHandle",
    "make_coordinator_handle",
    "tick",
]


_AliasKey = tuple[str, str, str]
_NativeEventKey = tuple[str, str]
_ObservationProjection = tuple[
    str,
    str,
    str,
    str,
    str,
    ChildAttemptState,
    str,
    bool,
    bool,
    str,
    str,
    int,
]
_ProjectionKey = tuple[str, str, _ObservationProjection]
_IssueKey = tuple[str, str, int, LifecycleEvidenceIssueKind]


def _observation_projection(obs: ChildLifecycleObservation) -> _ObservationProjection:
    """Return the offset-independent payload projected from one native event."""
    return (
        obs.task_kind,
        obs.task_id,
        obs.tool_use_id,
        obs.agent_id,
        obs.background_task_id,
        obs.attempt_state,
        obs.parent_turn_id,
        obs.is_parent_declaration,
        obs.is_user_result,
        obs.replaces_native_uuid,
        obs.replaced_by_native_uuid,
        obs.attempt_generation,
    )


def _alias_keys(obs: ChildLifecycleObservation) -> tuple[_AliasKey, ...]:
    """Return the canonical alias key set used to correlate an observation.

    Each candidate key is a triple ``(task_kind, alias_kind, alias_value)``.
    The set is task-kind-aware: Agent observations correlate only against
    Agent keys, Bash observations only against Bash keys. Blank aliases are
    dropped so they cannot bridge two unrelated attempts.
    """
    if obs.task_kind == "Agent":
        agent_candidates: list[_AliasKey] = []
        for alias_kind, value in (
            ("tool_use_id", obs.tool_use_id),
            ("task_id", obs.task_id),
            ("agent_id", obs.agent_id),
        ):
            if value:
                agent_candidates.append((obs.task_kind, alias_kind, value))
        return tuple(agent_candidates)
    if obs.task_kind == "Bash":
        bash_candidates: list[_AliasKey] = []
        for alias_kind, value in (
            ("tool_use_id", obs.tool_use_id),
            ("task_id", obs.task_id),
            ("background_task_id", obs.background_task_id),
        ):
            if value:
                bash_candidates.append((obs.task_kind, alias_kind, value))
        return tuple(bash_candidates)
    return ()


def _native_event_key(obs: ChildLifecycleObservation) -> _NativeEventKey | None:
    if not obs.source_event_id:
        return None
    return (obs.task_kind, obs.source_event_id)


def _projection_key(obs: ChildLifecycleObservation) -> _ProjectionKey:
    event_key = _native_event_key(obs)
    if event_key is None:
        return ("anonymous", "", _observation_projection(obs))
    task_kind, source_event_id = event_key
    return (task_kind, source_event_id, _observation_projection(obs))


@dataclass(frozen=True, slots=True)
class _AttemptId:
    """Opaque invocation-local identity for one attempt generation."""

    serial: int


@dataclass
class _AttemptRecord:
    """Private per-attempt mutable state owned by the coordinator."""

    observation: ChildLifecycleObservation
    attempt_generation: int = 0
    declared: bool = False
    aliases: set[_AliasKey] = field(default_factory=set)
    pending_evidence: list[ChildLifecycleObservation] = field(default_factory=list)
    predecessor_id: _AttemptId | None = None
    successor_id: _AttemptId | None = None
    satisfied: bool = False


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

    _attempts: dict[_AttemptId, _AttemptRecord] = field(default_factory=dict)
    _awaiting_delivery: dict[_AttemptId, _AttemptRecord] = field(default_factory=dict)
    _completed: dict[_AttemptId, _AttemptRecord] = field(default_factory=dict)
    _unresolved_terminal: dict[_AttemptId, _AttemptRecord] = field(default_factory=dict)
    _history: dict[_AttemptId, _AttemptRecord] = field(default_factory=dict)
    _records: dict[_AttemptId, _AttemptRecord] = field(default_factory=dict)
    _alias_index: dict[_AliasKey, set[_AttemptId]] = field(default_factory=dict)
    _native_event_projections: dict[_NativeEventKey, set[_ObservationProjection]] = field(
        default_factory=dict
    )
    _anonymous_projections: set[_ObservationProjection] = field(default_factory=set)
    _replacement_index: dict[str, set[_AttemptId]] = field(default_factory=dict)
    _candidates: dict[str, CompletionCandidate] = field(default_factory=dict)
    _candidate_states: dict[str, CompletionCandidateState] = field(default_factory=dict)
    _parent_turn_counter: int = 0
    _parent_turn_generations: dict[str, int] = field(default_factory=dict)
    _last_deferred_parent_generation: dict[str, int] = field(default_factory=dict)
    _next_attempt_id: int = 0
    _global_next_attempt_generation: int = 0
    _lifecycle_issues: dict[_IssueKey, LifecycleEvidenceIssue] = field(default_factory=dict)
    """Blocking issues keyed by child identity and event provenance.

    An issue is cleared only when later valid evidence arrives carrying
    the same fingerprint; unrelated evidence never clears an issue, and
    unresolved issues fail closed through the actor's snapshot.
    """
    _unmatched_evidence: list[ChildLifecycleObservation] = field(default_factory=list)
    """Observations / markers retained when correlation cannot be established yet.

    Replayed whenever a new exact native alias makes correlation possible.
    Terminal-before-declaration evidence is retained here so it can later
    link to a natively-linked replacement generation rather than being
    treated as an irreversible anonymous obligation.
    """
    _retained_projection_keys: set[_ProjectionKey] = field(default_factory=set)
    _replaying_unmatched: bool = False

    def _resolve_matching_issues(self, observation: ChildLifecycleObservation) -> None:
        aliases = frozenset((kind, value) for _, kind, value in _alias_keys(observation))
        if not aliases:
            return
        for issue_key, issue in tuple(self._lifecycle_issues.items()):
            if issue.resolution is not LifecycleEvidenceResolution.PENDING:
                continue
            if issue.task_kind not in {observation.task_kind, "unknown"}:
                continue
            if not issue.native_alias_kinds or len(issue.native_alias_kinds) != len(
                issue.native_aliases
            ):
                continue
            required_aliases = frozenset(
                (kind, value)
                for kind, value in zip(
                    issue.native_alias_kinds,
                    issue.native_aliases,
                    strict=True,
                )
                if kind and value
            )
            if required_aliases and required_aliases.issubset(aliases):
                self._resolve_issue_key(issue_key)

    def _was_consumed(self, observation: ChildLifecycleObservation) -> bool:
        projection = _observation_projection(observation)
        event_key = _native_event_key(observation)
        if event_key is None:
            return projection in self._anonymous_projections
        event_projections = self._native_event_projections.get(event_key)
        return event_projections is not None and projection in event_projections

    def _mark_consumed(self, observation: ChildLifecycleObservation) -> None:
        projection = _observation_projection(observation)
        event_key = _native_event_key(observation)
        if event_key is None:
            self._anonymous_projections.add(projection)
        else:
            self._native_event_projections.setdefault(event_key, set()).add(projection)
        self._retained_projection_keys.discard(_projection_key(observation))

    def _alias_matches(self, observation: ChildLifecycleObservation) -> set[_AttemptId]:
        matches: set[_AttemptId] = set()
        for alias in _alias_keys(observation):
            matches.update(self._alias_index.get(alias, ()))
        return matches

    def _record_alias_conflict(
        self,
        observation: ChildLifecycleObservation,
        attempt_ids: set[_AttemptId],
    ) -> None:
        aliases = _alias_keys(observation)
        fingerprint_parts = [
            observation.task_kind,
            *(f"{kind}={value}" for _, kind, value in aliases),
        ]
        fingerprint = "alias-conflict:" + "|".join(fingerprint_parts)
        ordered_ids = sorted(attempt_ids, key=lambda item: item.serial)
        self.register_issue(
            LifecycleEvidenceIssue(
                issue_kind=LifecycleEvidenceIssueKind.ALIAS_CONFLICT,
                task_kind=observation.task_kind,
                native_aliases=tuple(value for _, _, value in aliases),
                source_event_uuid=observation.source_event_id,
                canonical_fingerprint=fingerprint,
                channel_relative_byte_offset=observation.byte_offset,
                native_alias_kinds=tuple(kind for _, kind, _ in aliases),
                detail="aliases resolve to attempts "
                + ",".join(str(item.serial) for item in ordered_ids),
            )
        )

    def _new_attempt(
        self,
        observation: ChildLifecycleObservation,
        *,
        declared: bool,
    ) -> tuple[_AttemptId, _AttemptRecord]:
        self._next_attempt_id += 1
        self._global_next_attempt_generation += 1
        attempt_id = _AttemptId(self._next_attempt_id)
        record = _AttemptRecord(
            observation=observation,
            attempt_generation=(
                observation.attempt_generation or self._global_next_attempt_generation
            ),
            declared=declared,
        )
        self._records[attempt_id] = record
        self._index_observation(attempt_id, record, observation)
        return attempt_id, record

    def _index_observation(
        self,
        attempt_id: _AttemptId,
        record: _AttemptRecord,
        observation: ChildLifecycleObservation,
    ) -> None:
        for alias in _alias_keys(observation):
            record.aliases.add(alias)
            self._alias_index.setdefault(alias, set()).add(attempt_id)
        if observation.replaced_by_native_uuid:
            self._replacement_index.setdefault(observation.replaced_by_native_uuid, set()).add(
                attempt_id
            )

    def _remove_from_buckets(self, attempt_id: _AttemptId) -> None:
        self._attempts.pop(attempt_id, None)
        self._awaiting_delivery.pop(attempt_id, None)
        self._completed.pop(attempt_id, None)
        self._unresolved_terminal.pop(attempt_id, None)
        self._history.pop(attempt_id, None)

    def _place_record(
        self,
        attempt_id: _AttemptId,
        record: _AttemptRecord,
        bucket: dict[_AttemptId, _AttemptRecord],
    ) -> None:
        self._remove_from_buckets(attempt_id)
        bucket[attempt_id] = record

    @staticmethod
    def _merged_observation(
        current: ChildLifecycleObservation,
        incoming: ChildLifecycleObservation,
    ) -> ChildLifecycleObservation:
        return ChildLifecycleObservation(
            task_kind=incoming.task_kind,
            task_id=incoming.task_id or current.task_id,
            tool_use_id=incoming.tool_use_id or current.tool_use_id,
            agent_id=incoming.agent_id or current.agent_id,
            background_task_id=(incoming.background_task_id or current.background_task_id),
            attempt_state=incoming.attempt_state,
            source_event_id=incoming.source_event_id or current.source_event_id,
            parent_turn_id=incoming.parent_turn_id or current.parent_turn_id,
            byte_offset=incoming.byte_offset,
            is_parent_declaration=(
                incoming.is_parent_declaration or current.is_parent_declaration
            ),
            is_user_result=incoming.is_user_result,
            replaces_native_uuid=(incoming.replaces_native_uuid or current.replaces_native_uuid),
            replaced_by_native_uuid=(
                incoming.replaced_by_native_uuid or current.replaced_by_native_uuid
            ),
            attempt_generation=(incoming.attempt_generation or current.attempt_generation),
        )

    def _apply_observation(
        self,
        attempt_id: _AttemptId,
        record: _AttemptRecord,
        observation: ChildLifecycleObservation,
    ) -> None:
        current_state = record.observation.attempt_state
        merged = self._merged_observation(record.observation, observation)
        if record.satisfied:
            record.observation = replace(
                merged,
                attempt_state=current_state,
                is_user_result=record.observation.is_user_result,
            )
            self._index_observation(attempt_id, record, observation)
            self._place_record(attempt_id, record, self._history)
            return
        if current_state in {
            ChildAttemptState.FAILED,
            ChildAttemptState.CANCELLED,
            ChildAttemptState.TIMED_OUT,
        }:
            record.observation = replace(
                merged,
                attempt_state=current_state,
                is_user_result=(record.observation.is_user_result or observation.is_user_result),
            )
            self._index_observation(attempt_id, record, observation)
            self._place_record(attempt_id, record, self._unresolved_terminal)
            return

        record.observation = merged
        self._index_observation(attempt_id, record, observation)

        if observation.attempt_state == ChildAttemptState.COMPLETED:
            if observation.is_user_result:
                self._place_record(attempt_id, record, self._completed)
                self._satisfy_predecessor_chain(attempt_id)
            else:
                self._place_record(attempt_id, record, self._awaiting_delivery)
        elif observation.attempt_state in ATTEMPT_TERMINAL_STATES:
            self._place_record(attempt_id, record, self._unresolved_terminal)
        else:
            self._place_record(attempt_id, record, self._attempts)

    def _link_replacement(
        self,
        predecessor_id: _AttemptId,
        observation: ChildLifecycleObservation,
        alias_match: _AttemptId | None,
    ) -> tuple[_AttemptId, _AttemptRecord] | None:
        predecessor = self._records[predecessor_id]
        successor_ids = {
            item
            for item in (alias_match, predecessor.successor_id)
            if item is not None and item != predecessor_id
        }
        if len(successor_ids) > 1:
            self._record_alias_conflict(observation, successor_ids | {predecessor_id})
            return None
        if successor_ids:
            successor_id = next(iter(successor_ids))
            successor = self._records[successor_id]
        else:
            successor_id, successor = self._new_attempt(observation, declared=True)
        successor.attempt_generation = max(
            successor.attempt_generation,
            predecessor.attempt_generation + 1,
            observation.attempt_generation,
        )

        predecessor.successor_id = successor_id
        successor.predecessor_id = predecessor_id
        if predecessor_id not in self._unresolved_terminal:
            self._place_record(predecessor_id, predecessor, self._history)
        self._apply_observation(successor_id, successor, observation)
        return successor_id, successor

    def _satisfy_predecessor_chain(self, replacement_id: _AttemptId) -> None:
        current = self._records[replacement_id].predecessor_id
        while current is not None:
            record = self._records[current]
            predecessor = record.predecessor_id
            record.satisfied = True
            self._remove_from_buckets(current)
            self._history[current] = record
            current = predecessor

    def observe(self, observation: ChildLifecycleObservation) -> None:
        """Record one immutable observation in the appropriate bucket.

        Provenance ordering is preserved by the source byte offset: a
        marker earlier in the chunk may not complete before later child
        evidence in the same chunk is reduced. Terminal observations
        advance attempt generation when they carry a native replacement
        edge, otherwise they remain awaiting delivery.
        """
        if self._was_consumed(observation):
            return

        if not self._reduce_observation(observation):
            self._retain_unmatched_evidence(observation)
            return

        self._mark_consumed(observation)
        self._resolve_matching_issues(observation)
        self._replay_unmatched_evidence()

    def _reduce_observation(self, observation: ChildLifecycleObservation) -> bool:
        """Reduce one projection, returning whether it was consumed successfully."""

        alias_matches = self._alias_matches(observation)
        if len(alias_matches) > 1:
            self._record_alias_conflict(observation, alias_matches)
            return False

        alias_match = next(iter(alias_matches), None)
        event_key = _native_event_key(observation)

        if (
            alias_match is not None
            and observation.is_user_result
            and observation.attempt_state in ATTEMPT_TERMINAL_STATES
            and observation.tool_use_id
            and not self._alias_index.get(
                (observation.task_kind, "tool_use_id", observation.tool_use_id)
            )
        ):
            return False

        if observation.replaces_native_uuid:
            predecessor_matches = self._replacement_index.get(
                observation.replaces_native_uuid, set()
            )
            if len(predecessor_matches) > 1:
                self._record_alias_conflict(observation, set(predecessor_matches))
                return False
            if not predecessor_matches:
                return False
            predecessor_id = next(iter(predecessor_matches))
            if self._records[predecessor_id].satisfied:
                return True
            linked = self._link_replacement(
                predecessor_id,
                observation,
                alias_match,
            )
            if linked is None:
                return False
            return True

        attempt_id = alias_match
        if attempt_id is None:
            aliases = _alias_keys(observation)
            if not aliases and (event_key is None or observation.task_kind in {"Agent", "Bash"}):
                return False
            declared = observation.attempt_state not in ATTEMPT_TERMINAL_STATES
            attempt_id, record = self._new_attempt(
                observation,
                declared=declared,
            )
            if declared:
                self._place_record(attempt_id, record, self._attempts)
            else:
                record.pending_evidence.append(observation)
                self._place_record(attempt_id, record, self._unresolved_terminal)
        else:
            record = self._records[attempt_id]
            if record.successor_id is not None:
                if record.observation.attempt_state in {
                    ChildAttemptState.FAILED,
                    ChildAttemptState.CANCELLED,
                    ChildAttemptState.TIMED_OUT,
                } or observation.attempt_state in {
                    ChildAttemptState.FAILED,
                    ChildAttemptState.CANCELLED,
                    ChildAttemptState.TIMED_OUT,
                }:
                    self._apply_observation(attempt_id, record, observation)
                return True
            if (
                not record.declared
                and observation.attempt_state not in ATTEMPT_TERMINAL_STATES
                and not observation.is_user_result
            ):
                pending = tuple(record.pending_evidence)
                record.pending_evidence.clear()
                record.declared = True
                record.observation = self._merged_observation(record.observation, observation)
                self._index_observation(attempt_id, record, observation)
                self._place_record(attempt_id, record, self._attempts)
                for pending_observation in pending:
                    self._apply_observation(attempt_id, record, pending_observation)
            elif (
                record.observation.attempt_state in ATTEMPT_TERMINAL_STATES
                and observation.attempt_state not in ATTEMPT_TERMINAL_STATES
            ):
                self._apply_observation(attempt_id, record, observation)
            else:
                self._apply_observation(attempt_id, record, observation)

        return True

    def _replay_unmatched_evidence(self) -> None:
        if self._replaying_unmatched or not self._unmatched_evidence:
            return
        self._replaying_unmatched = True
        try:
            while self._unmatched_evidence:
                pending = tuple(self._unmatched_evidence)
                self._unmatched_evidence.clear()
                self._retained_projection_keys.clear()
                made_progress = False
                for evidence in pending:
                    if self._was_consumed(evidence):
                        continue
                    if self._reduce_observation(evidence):
                        self._mark_consumed(evidence)
                        self._resolve_matching_issues(evidence)
                        made_progress = True
                    else:
                        self._retain_unmatched_evidence(evidence)
                if not made_progress:
                    break
        finally:
            self._replaying_unmatched = False

    def _retain_unmatched_evidence(self, evidence: ChildLifecycleObservation) -> None:
        projection_key = _projection_key(evidence)
        if projection_key in self._retained_projection_keys:
            return
        self._retained_projection_keys.add(projection_key)
        self._unmatched_evidence.append(evidence)

    def register_parent_marker(self, marker: ParentAssistantMarker) -> CompletionCandidate:
        """Record one parent-assistant marker and synthesize its candidate.

        Blank or malformed native UUIDs are dropped without synthesizing
        a candidate — markers from text content, channel, session ID,
        fingerprint, or the literal string ``"unknown"`` cannot bridge
        to a candidate.
        """
        sighting = CandidateSighting(
            source=CompletionCandidateSource.CHANNEL_A,
            native_uuid=marker.native_uuid,
            native_message_id=marker.message_id,
            channel_relative_byte_offset=marker.byte_offset,
            backend_session_id=marker.backend_session_id,
            record_provenance="parent_assistant_marker",
        )
        return self.register_candidate_sighting(sighting)

    def register_candidate_sighting(self, sighting: CandidateSighting) -> CompletionCandidate:
        """Register one channel-specific sighting without collapsing provenance."""
        uuid = sighting.native_uuid.strip()
        if not uuid:
            raise ValueError("parent_marker_native_uuid_blank")
        if uuid.lower() == "unknown":
            raise ValueError("parent_marker_native_uuid_unknown")

        generation = self._parent_turn_generations.get(uuid)
        if generation is None:
            for candidate_id, state in tuple(self._candidate_states.items()):
                if state is CompletionCandidateState.DEFERRED:
                    self._candidate_states[candidate_id] = CompletionCandidateState.SUPERSEDED
            self._parent_turn_counter += 1
            generation = self._parent_turn_counter
            self._parent_turn_generations[uuid] = generation
        candidate = CompletionCandidate(
            candidate_id=uuid,
            parent_turn_generation=generation,
            sources=(sighting.source,),
            native_message_id=sighting.native_message_id,
            byte_offset=sighting.channel_relative_byte_offset,
            backend_session_id=sighting.backend_session_id,
            sightings=(sighting,),
        )
        existing = self._candidates.get(uuid)
        if existing is None:
            self._candidates[uuid] = candidate
        else:
            sightings = existing.sightings
            if sighting not in sightings:
                sightings = (*sightings, sighting)
            merged_sources = tuple(dict.fromkeys(item.source for item in sightings))
            self._candidates[uuid] = CompletionCandidate(
                candidate_id=existing.candidate_id,
                parent_turn_generation=generation,
                sources=merged_sources,
                native_message_id=existing.native_message_id,
                byte_offset=existing.byte_offset,
                backend_session_id=existing.backend_session_id,
                sightings=sightings,
            )
        self._candidate_states.setdefault(uuid, CompletionCandidateState.DEFERRED)
        return self._candidates[uuid]

    def _supersede_candidate(self, candidate_id: str) -> None:
        """Move one captured candidate to ``SUPERSEDED``.

        Deferred candidates become superseded after their obligations
        fail and never reactivate.
        """
        if not candidate_id:
            return
        if candidate_id in self._candidate_states:
            self._candidate_states[candidate_id] = CompletionCandidateState.SUPERSEDED

    def get_candidate(self, candidate_id: str) -> CompletionCandidate | None:
        """Return one immutable candidate without exposing reducer internals."""
        return self._candidates.get(candidate_id)

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

        if (
            self._attempts
            or self._awaiting_delivery
            or self._unresolved_terminal
            or self._unmatched_evidence
        ):
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
        self._supersede_candidate(candidate_id)

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
        awaiting = tuple(record.observation for record in self._awaiting_delivery.values())
        completed = tuple(record.observation for record in self._completed.values())
        unresolved = tuple(record.observation for record in self._unresolved_terminal.values())
        last_deferred = max(self._last_deferred_parent_generation.values(), default=0)
        lifecycle_issues = tuple(self._lifecycle_issues.values())
        return build_lifecycle_snapshot_from_attempts(
            active=active,
            completed=completed,
            unresolved_terminal=unresolved,
            candidate_states=candidate_states,
            eligible_candidate=None,
            last_deferred_parent_generation=last_deferred,
            lifecycle_issues=lifecycle_issues,
            awaiting_delivery=awaiting,
        )

    def register_issue(self, issue: LifecycleEvidenceIssue) -> None:
        """Record a pending blocking-evidence issue.

        Exact duplicate event provenance is idempotent. Distinct source events,
        offsets, or issue kinds for the same child fingerprint remain separate.
        """
        issue_key = (
            issue.canonical_fingerprint,
            issue.source_event_uuid,
            issue.channel_relative_byte_offset,
            issue.issue_kind,
        )
        self._lifecycle_issues[issue_key] = issue

    def _resolve_issue_key(self, issue_key: _IssueKey) -> bool:
        issue = self._lifecycle_issues.get(issue_key)
        if issue is None:
            return False
        self._lifecycle_issues[issue_key] = replace(
            issue,
            resolution=LifecycleEvidenceResolution.RESOLVED,
        )
        return True

    def resolve_issue(self, canonical_fingerprint: str) -> bool:
        """Mark one issue as resolved by canonical fingerprint.

        All independently retained events for that child fingerprint are
        resolved. Returns False when no matching issue was registered.
        """
        issue_keys = tuple(
            issue_key
            for issue_key in self._lifecycle_issues
            if issue_key[0] == canonical_fingerprint
        )
        for issue_key in issue_keys:
            self._resolve_issue_key(issue_key)
        return bool(issue_keys)

    def has_pending_issues(self) -> bool:
        """Return True when any blocking issue is still ``PENDING``."""
        return any(
            issue.resolution == LifecycleEvidenceResolution.PENDING
            for issue in self._lifecycle_issues.values()
        )


@dataclass(frozen=True, slots=True)
class ChildLifecycleCoordinatorHandle:
    """Thin handle exposing only the methods the actor needs.

    The handle is the sole surface the actor (and tests) call into.
    Returning a frozen handle rather than the coordinator itself keeps
    the producer side free of any reducer mutation rights.
    """

    _coordinator: ChildLifecycleCoordinator

    def observe(self, observation: ChildLifecycleObservation) -> None:
        self._coordinator.observe(observation)

    def register_parent_marker(self, marker: ParentAssistantMarker) -> CompletionCandidate:
        return self._coordinator.register_parent_marker(marker)

    def register_candidate_sighting(self, sighting: CandidateSighting) -> CompletionCandidate:
        return self._coordinator.register_candidate_sighting(sighting)

    def evaluate_candidate(self, candidate_id: str) -> CompletionCandidate | None:
        return self._coordinator.evaluate_candidate(candidate_id)

    def get_candidate(self, candidate_id: str) -> CompletionCandidate | None:
        return self._coordinator.get_candidate(candidate_id)

    def note_child_work_failed(self, candidate_id: str) -> None:
        self._coordinator.note_child_work_failed(candidate_id)

    def register_issue(self, issue: LifecycleEvidenceIssue) -> None:
        self._coordinator.register_issue(issue)

    def has_pending_issues(self) -> bool:
        return self._coordinator.has_pending_issues()

    def snapshot(self) -> ChildLifecycleSnapshot:
        return self._coordinator.snapshot()


def make_coordinator_handle() -> ChildLifecycleCoordinatorHandle:
    """Build a fresh handle for one invocation."""
    return ChildLifecycleCoordinatorHandle(_coordinator=ChildLifecycleCoordinator())


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
