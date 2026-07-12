"""Async-child-aware completion lifecycle types (issue #4233).

Implements the typed observation + coordinator contract that gates
``kill_after_completion`` against active subagents. All types here are
IL-0 (zero autoskillit imports) and primitive-only.

Vocabulary (canonical, shared by parser, coordinator, tests):
- attempt states:    ACTIVE, COMPLETED, FAILED, CANCELLED, TIMED_OUT
- candidate states:  DEFERRED, SUPERSEDED, ELIGIBLE
- obligation states: active, satisfied, unresolved-terminal
- attempt_generation: monotonic per-attempt counter derived from native
                      ``replaces`` edges on a child attempt.
- parent_turn_generation: monotonic per-parent-marker counter that drives
                          candidate eligibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import Any

__all__ = [
    "ATTEMPT_ACTIVE_STATES",
    "ATTEMPT_TERMINAL_STATES",
    "CandidateSighting",
    "ChildAttemptState",
    "ChildLifecycleObservation",
    "ChildLifecycleSnapshot",
    "ChildObligationState",
    "CleanupOutcome",
    "CompletionCandidate",
    "CompletionCandidateSource",
    "CompletionCandidateState",
    "LifecycleActorRequest",
    "LifecycleActorResponse",
    "LifecycleDecision",
    "LifecycleEvidenceIssue",
    "LifecycleEvidenceIssueKind",
    "LifecycleEvidenceResolution",
    "ParentAssistantMarker",
    "ProcessIdentity",
    "build_lifecycle_snapshot_from_attempts",
]


DEFAULT_CLEANUP_BUDGET_SECONDS: float = 15.0
"""Deprecated re-export of ``_type_subprocess.DEFAULT_CLEANUP_BUDGET_SECONDS``.

Canonical owner is ``_type_subprocess``; this symbol remains here for backward
compatibility with consumers that import from ``autoskillit.core.types``.
New code should import from ``autoskillit.core.types.DEFAULT_CLEANUP_BUDGET_SECONDS``
or directly from ``autoskillit.core.DEFAULT_CLEANUP_BUDGET_SECONDS``.
"""


@unique
class LifecycleEvidenceIssueKind(Enum):
    """Kind of blocking evidence captured by ``LifecycleEvidenceIssue``.

    Each kind corresponds to one fail-closed path through the Channel A
    normalizer. Unknown / malformed statuses, identity conflicts, and alias
    conflicts all surface as blocking issues that the coordinator must hold
    until matching canonical evidence arrives; the actor surfaces them
    through every carrier so headless retry adjudication can see them.
    """

    UNKNOWN_STATUS = "unknown_status"
    MALFORMED_IDENTITY = "malformed_identity"
    ALIAS_CONFLICT = "alias_conflict"
    MIXED_LAUNCH_AND_TERMINAL = "mixed_launch_and_terminal"


@unique
class LifecycleEvidenceResolution(Enum):
    """Resolution state for one ``LifecycleEvidenceIssue``.

    ``PENDING`` issues remain blocking; ``RESOLVED`` issues have been matched
    against valid canonical evidence carrying the same fingerprint.
    """

    PENDING = "pending"
    RESOLVED = "resolved"


@dataclass(frozen=True, slots=True)
class LifecycleEvidenceIssue:
    """Typed blocking-evidence record captured by the Channel A normalizer.

    Resolution is gated by the canonical child fingerprint: later evidence
    must carry every nonblank native alias in the same task-kind scope.
    Per-event UUID is retained as provenance but excluded from child identity,
    so a corrected later record can resolve malformed evidence. Unrelated or
    partial evidence never clears an issue, and unresolved issues fail closed
    through the actor's race/result carriers into headless retry adjudication.
    """

    issue_kind: LifecycleEvidenceIssueKind
    task_kind: str
    native_aliases: tuple[str, ...]
    source_event_uuid: str
    canonical_fingerprint: str
    """Stable identifier derived from ``(task_kind, nonblank native_aliases)``.

    ``source_event_uuid`` is deliberately excluded because a corrected record
    is a distinct event for the same canonical child.
    """
    channel_relative_byte_offset: int
    native_alias_kinds: tuple[str, ...] = ()
    """Alias-kind names parallel to ``native_aliases``.

    Resolution compares exact ``(kind, value)`` pairs. An empty or
    length-mismatched tuple is invalid evidence and remains pending fail-closed.
    """
    resolution: LifecycleEvidenceResolution = LifecycleEvidenceResolution.PENDING
    detail: str = ""


@unique
class ChildAttemptState(Enum):
    """State of one owned async child attempt as reduced from observations."""

    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@unique
class CompletionCandidateState(Enum):
    """Eligibility state of one captured completion candidate (parent marker)."""

    DEFERRED = "deferred"
    SUPERSEDED = "superseded"
    ELIGIBLE = "eligible"


@unique
class CompletionCandidateSource(Enum):
    """Origin channel of one captured completion candidate.

    Only Channel A and Channel B may create candidates. Process exit
    may *request catch-up* but cannot synthesize a candidate identity.
    """

    CHANNEL_A = "channel_a"
    CHANNEL_B = "channel_b"


@unique
class ChildObligationState(Enum):
    """Derived state of one child-obligation tuple (active/satisfied/unresolved/awaiting)."""

    ACTIVE = "active"
    AWAITING_DELIVERY = "awaiting_delivery"
    SATISFIED = "satisfied"
    UNRESOLVED_TERMINAL = "unresolved_terminal"


@unique
class LifecycleDecision(Enum):
    """Actor decision emitted by the coordinator after catch-up + obligation review.

    ``CHILD_WORK_FAILED`` is distinct from ``CLEANUP_FAILED``: it signals that a
    fresh post-quiescence parent candidate arrived while the previous turn's
    obligations remain unresolved. The two decisions must never collapse.

    ``CATCH_UP_FAILED`` is distinct from ``CHILD_WORK_FAILED``: it signals that
    a Channel B proposal or process-exit requested watermark catch-up but the
    required Channel A offset was never reached before the deadline.
    """

    CONTINUE = "continue"
    ELIGIBLE = "eligible"
    CHILD_WORK_FAILED = "child_work_failed"
    CATCH_UP_FAILED = "catch_up_failed"


ATTEMPT_ACTIVE_STATES: frozenset[ChildAttemptState] = frozenset({ChildAttemptState.ACTIVE})
ATTEMPT_TERMINAL_STATES: frozenset[ChildAttemptState] = frozenset(
    {
        ChildAttemptState.COMPLETED,
        ChildAttemptState.FAILED,
        ChildAttemptState.CANCELLED,
        ChildAttemptState.TIMED_OUT,
    }
)


@dataclass(frozen=True, slots=True)
class ChildLifecycleObservation:
    """Immutable observation of one child lifecycle event emitted by the parser.

    The parser emits one observation per detected lifecycle signal; the
    coordinator (not the parser) maintains the reducer state that joins
    declarations and results. Native IDs are preserved verbatim so a Bash
    notification cannot accidentally close an Agent obligation.

    Attempt generation is carried on terminal observations so the reducer can
    recognize natively-linked replacement generations without consulting the
    parser. A replacement edge (``replaces`` / ``replaced_by``) is proven by a
    sibling system record whose ``task_id``/``tool_use_id`` matches the prior
    attempt's task identity — the parser surfaces those edges as
    ``replaces_native_uuid`` / ``replaced_by_native_uuid``.
    """

    task_kind: str
    """Canonical parent declaration kind: 'Agent', 'Bash', or unknown."""
    task_id: str = ""
    """Backend-native task ID (if present). May be empty until correlation."""
    tool_use_id: str = ""
    """Native tool_use_id of the parent declaration/result."""
    agent_id: str = ""
    """Native Agent agentId (when applicable)."""
    background_task_id: str = ""
    """Native backgroundTaskId (Bash)."""
    attempt_state: ChildAttemptState = ChildAttemptState.ACTIVE
    """Observed attempt state. May be ACTIVE for partial evidence."""
    source_event_id: str = ""
    """Backend-native record UUID of the originating event, when present."""
    parent_turn_id: str = ""
    """Native identifier of the captured parent turn (matches parent marker UUID)."""
    byte_offset: int = 0
    """Provenance offset of the observation (byte index, not character index)."""
    is_parent_declaration: bool = False
    """True for parent-side declarations (launch evidence)."""
    is_user_result: bool = False
    """True for the user-side tool_result that closes the attempt."""
    replaces_native_uuid: str = ""
    """Non-empty when this attempt was launched as a replacement for a prior attempt."""
    replaced_by_native_uuid: str = ""
    """Non-empty when this attempt was replaced by a later linked attempt."""
    attempt_generation: int = 0
    """Monotonic attempt generation derived from native replacement edges."""


@dataclass(frozen=True, slots=True)
class ParentAssistantMarker:
    """Marker-bearing parent-assistant record used to synthesize a candidate.

    The coordinator only creates a ``CompletionCandidate`` when the marker
    carries a non-blank native UUID; the message ID is corroboration, never
    identity. ``byte_offset`` is required so candidates carry Channel A
    provenance end-to-end.
    """

    native_uuid: str
    """Non-blank native record UUID of the parent assistant marker."""
    message_id: str
    """Native string message ID (corroboration only)."""
    byte_offset: int
    """Channel A byte offset where the marker was reduced."""
    backend_session_id: str = ""
    """Backend session identifier carried by the marker record."""


@dataclass(frozen=True, slots=True)
class CandidateSighting:
    """Primitive-only frozen per-channel sighting value for a completion candidate.

    Each channel (A or B) that observes a parent-assistant marker record
    contributes one sighting with its own offset, session identity, and
    provenance. A/B offsets are non-interchangeable: ``channel_relative_byte_offset``
    is relative to the channel's own byte universe.
    """

    source: CompletionCandidateSource
    native_uuid: str
    native_message_id: str = ""
    channel_relative_byte_offset: int = 0
    backend_session_id: str = ""
    record_provenance: str = ""


@dataclass(frozen=True, slots=True)
class CompletionCandidate:
    """Provenance-rich completion candidate used to authorize completion.

    ``candidate_id`` is the native string UUID of the parent-assistant marker.
    ``parent_turn_generation`` is monotonic per distinct UUID; eligibility
    requires it to exceed the last deferred parent-turn generation observed
    for that UUID. ``sightings`` carries a per-channel provenance tuple so
    A-relative and B-relative offsets remain distinct.
    """

    candidate_id: str
    parent_turn_generation: int
    sources: tuple[CompletionCandidateSource, ...]
    native_message_id: str
    byte_offset: int
    backend_session_id: str = ""
    sightings: tuple[CandidateSighting, ...] = ()


@dataclass(frozen=True, slots=True)
class ChildLifecycleSnapshot:
    """Frozen snapshot of the coordinator's reducer state.

    The race resolution and cleanup paths consume this snapshot; the
    coordinator itself continues to mutate privately. No field here may
    ever be the sole truth for ongoing decisions — only the snapshot is.
    """

    active_children: tuple[ChildLifecycleObservation, ...]
    """Immutable observations for children still considered active."""
    completed_children: tuple[ChildLifecycleObservation, ...]
    """Immutable observations for children that reached COMPLETED."""
    unresolved_terminal: tuple[ChildLifecycleObservation, ...]
    """Immutable observations for children that reached FAILED/CANCELLED/TIMED_OUT
    without a natively linked replacement generation."""
    has_active_children: bool
    """Cached derived boolean — set iff active_children is non-empty."""
    has_unresolved_terminal: bool
    """Cached derived boolean — set iff unresolved_terminal is non-empty."""
    candidate_states: tuple[tuple[str, CompletionCandidateState], ...]
    """Each captured candidate paired with its current eligibility state,
    keyed by the candidate's source-event native UUID."""
    eligible_candidate: CompletionCandidate | None = None
    """Single provenance-rich candidate that authorizes completion, when set."""
    last_deferred_parent_generation: int = 0
    """Highest parent-turn generation that produced a DEFERRED candidate;
    a candidate must exceed this value to become ELIGIBLE."""
    lifecycle_issues: tuple[LifecycleEvidenceIssue, ...] = ()
    """Frozen tuple of unresolved blocking-evidence issues.

    ``PENDING`` issues are fail-closed: they block ``LifecycleDecision.ELIGIBLE``
    until matched against valid canonical evidence carrying the same fingerprint.
    Cleared issues remain in the tuple with ``LifecycleEvidenceResolution.RESOLVED``
    so downstream consumers can audit what blocked and what cleared.
    """
    awaiting_delivery: tuple[ChildLifecycleObservation, ...] = ()
    """Children with terminal process evidence but no user tool_result delivery yet."""


@dataclass(frozen=True, slots=True)
class LifecycleActorRequest:
    """Deprecated public type — actor transports are private.

    The actor's producer/reply message types live inside
    ``execution/process/_lifecycle_actor.py``. This class remains for
    backward-compatible imports but is no longer constructed or consumed
    by any production code path.
    """

    request_id: str
    kind: str = ""
    payload: Any = None


@dataclass(frozen=True, slots=True)
class LifecycleActorResponse:
    """Deprecated public type — actor transports are private.

    The actor's reply type lives inside ``execution/process/_lifecycle_actor.py``.
    This class remains for backward-compatible imports but is no longer
    constructed or consumed by any production code path. Use
    ``LifecycleDecision`` (the frozen IL-0 decision value type) for carrier
    propagation through ``SubprocessResult`` and other core carriers.
    """

    request_id: str
    snapshot: ChildLifecycleSnapshot | None = None
    decision: LifecycleDecision = LifecycleDecision.CONTINUE
    eligible_candidate: CompletionCandidate | None = None
    processed_channel_a_byte_offset: int = 0


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    """Owned process identity rooted at one spawned ``proc.pid``.

    Linux identity uses raw ``/proc`` start-time ticks so WSL clock adjustments
    cannot perturb PID-reuse checks. ``fallback_create_time`` is used only when
    start-time ticks are unavailable on another platform.
    """

    root_pid: int
    starttime_ticks: int
    fallback_create_time: float = 0.0
    process_group_id: int = 0
    session_id: int = 0
    descendants: tuple[tuple[int, int], ...] = ()
    """Refreshed descendant ``(pid, starttime_ticks)`` pairs."""


@dataclass(frozen=True, slots=True)
class CleanupOutcome:
    """Outcome record of an attempt to clean up a managed process tree.

    Surfaces through ``SubprocessResult.cleanup_outcome`` for downstream
    diagnostics. Three orthogonal booleans drive the retry-reason mapping.
    """

    succeeded: bool
    budget_exhausted: bool
    retained_identities: tuple[ProcessIdentity, ...] = ()
    """Any owned process identity whose removal was deferred past the budget."""
    unknown_identities: tuple[ProcessIdentity, ...] = ()
    """Identities that could not be classified during verification.

    Distinct from retained (verified alive); these survived enumeration
    but identity verification failed (PID reuse, access denied, etc.).
    """


def build_lifecycle_snapshot_from_attempts(
    active: tuple[ChildLifecycleObservation, ...],
    completed: tuple[ChildLifecycleObservation, ...],
    unresolved_terminal: tuple[ChildLifecycleObservation, ...],
    candidate_states: tuple[tuple[str, CompletionCandidateState], ...],
    eligible_candidate: CompletionCandidate | None = None,
    last_deferred_parent_generation: int = 0,
    lifecycle_issues: tuple[LifecycleEvidenceIssue, ...] = (),
    awaiting_delivery: tuple[ChildLifecycleObservation, ...] = (),
) -> ChildLifecycleSnapshot:
    """Build a frozen lifecycle snapshot from reducer-private buckets.

    Centralizes the cached boolean derivations and ordering invariants so
    the coordinator can construct the snapshot without touching protocol
    or backend modules.
    """
    return ChildLifecycleSnapshot(
        active_children=tuple(active),
        completed_children=tuple(completed),
        unresolved_terminal=tuple(unresolved_terminal),
        has_active_children=bool(active),
        has_unresolved_terminal=bool(unresolved_terminal),
        candidate_states=tuple(candidate_states),
        eligible_candidate=eligible_candidate,
        last_deferred_parent_generation=last_deferred_parent_generation,
        lifecycle_issues=tuple(lifecycle_issues),
        awaiting_delivery=tuple(awaiting_delivery),
    )
