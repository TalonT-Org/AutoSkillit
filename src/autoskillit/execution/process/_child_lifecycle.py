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
    LifecycleEvidenceIssue,
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


def _alias_keys(obs: ChildLifecycleObservation) -> tuple[tuple[str, ...], ...]:
    """Return the canonical alias key set used to correlate an observation.

    Each candidate key is a triple ``(task_kind, alias_kind, alias_value)``.
    The set is task-kind-aware: Agent observations correlate only against
    Agent keys, Bash observations only against Bash keys. Blank aliases are
    dropped so they cannot bridge two unrelated attempts.
    """
    if obs.task_kind == "Agent":
        candidates: list[tuple[str, ...]] = []
        for alias_kind, value in (
            ("tool_use_id", obs.tool_use_id),
            ("task_id", obs.task_id),
            ("agent_id", obs.agent_id),
        ):
            if value:
                candidates.append((obs.task_kind, alias_kind, value))
        if not candidates and obs.source_event_id:
            candidates.append((obs.task_kind, "source", obs.source_event_id))
        return tuple(candidates)
    if obs.task_kind == "Bash":
        candidates = []
        for alias_kind, value in (
            ("tool_use_id", obs.tool_use_id),
            ("task_id", obs.task_id),
            ("background_task_id", obs.background_task_id),
        ):
            if value:
                candidates.append((obs.task_kind, alias_kind, value))
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

    Staleness requires a native replacement edge. Alias inequality alone
    cannot prove that an observation belongs to an older generation.
    """
    replacement_edge = existing.observation.replaces_native_uuid
    return bool(
        existing.replaced
        and replacement_edge
        and observation.replaced_by_native_uuid == replacement_edge
    )


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
    _parent_turn_counter: int = 0
    _parent_turn_generations: dict[str, int] = field(default_factory=dict)
    _last_deferred_parent_generation: dict[str, int] = field(default_factory=dict)
    _global_next_attempt_generation: int = 0
    _identity_index: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """Maps each persistent identity (``agent:<id>``, ``bg:<id>``,
    ``task:<id>``) to the alias key currently bound to the active record
    observation, so subsequent observations that lose their explicit alias
    can still correlate via a shared persistent identity."""
    _lifecycle_issues: dict[str, LifecycleEvidenceIssue] = field(default_factory=dict)
    """Pending blocking-evidence issues keyed by canonical fingerprint.

    An issue is cleared only when later valid evidence arrives carrying
    the same fingerprint; unrelated evidence never clears an issue, and
    unresolved issues fail closed through the actor's snapshot.
    """
    _unmatched_evidence: list[ChildLifecycleObservation | ParentAssistantMarker] = field(
        default_factory=list,
    )
    """Observations / markers retained when correlation cannot be established yet.

    Replayed whenever a new exact native alias makes correlation possible.
    Terminal-before-declaration evidence is retained here so it can later
    link to a natively-linked replacement generation rather than being
    treated as an irreversible anonymous obligation.
    """

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

        if (
            existing_record is not None
            and existing_match is not None
            and existing_match[0] in {"completed", "unresolved_terminal"}
            and observation.attempt_state not in ATTEMPT_TERMINAL_STATES
            and not observation.is_user_result
        ):
            # Terminal facts are irreversible. A late declaration or replay
            # cannot resurrect a completed/failed attempt.
            source_bucket[existing_key] = existing_record
            return

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
                # A task_notification is terminal process evidence, but the
                # obligation remains active until the corresponding user
                # tool_result is delivered to the parent.
                self._attempts[existing_key] = existing_record
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
            if observation.replaces_native_uuid:
                existing_record.attempt_generation += 1
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

        generation = self._parent_turn_generations.get(uuid)
        if generation is None:
            self._parent_turn_counter += 1
            generation = self._parent_turn_counter
            self._parent_turn_generations[uuid] = generation
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
        self.supersede_candidate(candidate_id)

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
        lifecycle_issues = tuple(self._lifecycle_issues.values())
        return build_lifecycle_snapshot_from_attempts(
            active=active,
            completed=completed,
            unresolved_terminal=unresolved,
            candidate_states=candidate_states,
            eligible_candidate=None,
            last_deferred_parent_generation=last_deferred,
            lifecycle_issues=lifecycle_issues,
        )

    def register_issue(self, issue: LifecycleEvidenceIssue) -> None:
        """Record a pending blocking-evidence issue.

        Idempotent on canonical fingerprint: later observations with the
        same fingerprint overwrite the existing entry but never merge
        distinct issues into one record.
        """
        self._lifecycle_issues[issue.canonical_fingerprint] = issue

    def resolve_issue(self, canonical_fingerprint: str) -> bool:
        """Mark one issue as resolved by canonical fingerprint.

        Returns True when an issue was found and cleared; False when no
        issue with that fingerprint was registered.
        """
        issue = self._lifecycle_issues.get(canonical_fingerprint)
        if issue is None:
            return False
        self._lifecycle_issues[canonical_fingerprint] = LifecycleEvidenceIssue(
            issue_kind=issue.issue_kind,
            task_kind=issue.task_kind,
            native_aliases=issue.native_aliases,
            source_event_uuid=issue.source_event_uuid,
            canonical_fingerprint=issue.canonical_fingerprint,
            channel_relative_byte_offset=issue.channel_relative_byte_offset,
            resolution=LifecycleEvidenceResolution.RESOLVED,
            detail=issue.detail,
        )
        return True

    def has_pending_issues(self) -> bool:
        """Return True when any blocking issue is still ``PENDING``."""
        return any(
            issue.resolution == LifecycleEvidenceResolution.PENDING
            for issue in self._lifecycle_issues.values()
        )

    def retain_unmatched_evidence(
        self,
        evidence: ChildLifecycleObservation | ParentAssistantMarker,
    ) -> None:
        """Retain one observation / marker for later correlation.

        Terminal-before-declaration evidence is retained here so a
        later natively-linked replacement can satisfy it; raw declaration
        evidence whose aliases cannot yet be resolved is also retained.
        """
        self._unmatched_evidence.append(evidence)

    def drain_unmatched_evidence(
        self,
    ) -> tuple[ChildLifecycleObservation | ParentAssistantMarker, ...]:
        """Return and clear the retained unmatched evidence list.

        Callers re-ingest every returned item through ``observe`` /
        ``register_parent_marker``; items that still cannot be correlated
        will be retained again on the next call.
        """
        items = tuple(self._unmatched_evidence)
        self._unmatched_evidence.clear()
        return items


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

    def get_candidate(self, candidate_id: str) -> CompletionCandidate | None:
        return self.coordinator.get_candidate(candidate_id)

    def note_child_work_failed(self, candidate_id: str) -> None:
        self.coordinator.note_child_work_failed(candidate_id)

    def register_issue(self, issue: LifecycleEvidenceIssue) -> None:
        self.coordinator.register_issue(issue)

    def resolve_issue(self, canonical_fingerprint: str) -> bool:
        return self.coordinator.resolve_issue(canonical_fingerprint)

    def has_pending_issues(self) -> bool:
        return self.coordinator.has_pending_issues()

    def retain_unmatched_evidence(
        self,
        evidence: ChildLifecycleObservation | ParentAssistantMarker,
    ) -> None:
        self.coordinator.retain_unmatched_evidence(evidence)

    def drain_unmatched_evidence(
        self,
    ) -> tuple[ChildLifecycleObservation | ParentAssistantMarker, ...]:
        return self.coordinator.drain_unmatched_evidence()

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
