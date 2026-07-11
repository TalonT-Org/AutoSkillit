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

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "ATTEMPT_ACTIVE_STATES",
    "ATTEMPT_TERMINAL_STATES",
    "ChildAttemptState",
    "ChildLifecycleObservation",
    "ChildLifecycleSnapshot",
    "ChildObligationState",
    "CleanupOutcome",
    "CompletionCandidate",
    "CompletionCandidateSource",
    "CompletionCandidateState",
    "DEFAULT_CLEANUP_BUDGET_SECONDS",
    "LifecycleActorRequest",
    "LifecycleActorResponse",
    "LifecycleDecision",
    "ParentAssistantMarker",
    "ProcessIdentity",
    "StreamParserFactory",
    "build_lifecycle_snapshot_from_attempts",
]


DEFAULT_CLEANUP_BUDGET_SECONDS: float = 15.0
"""Invocation-level cleanup budget shared by graceful drain, TERM wait, and KILL wait.

Threaded through SubprocessRunner and recording/fake implementations; consumed
by shielded exception/cancellation cleanup so all cleanup paths share one deadline.
"""


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
    """Derived state of one child-obligation tuple (active/satisfied/unresolved)."""

    ACTIVE = "active"
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
class CompletionCandidate:
    """Provenance-rich completion candidate used to authorize completion.

    ``candidate_id`` is the native string UUID of the parent-assistant marker.
    ``parent_turn_generation`` is monotonic per distinct UUID; eligibility
    requires it to exceed the last deferred parent-turn generation observed
    for that UUID.
    """

    candidate_id: str
    parent_turn_generation: int
    sources: tuple[CompletionCandidateSource, ...]
    native_message_id: str
    byte_offset: int
    backend_session_id: str = ""


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


@dataclass(frozen=True, slots=True)
class LifecycleActorRequest:
    """Immutable message produced by Channel B / Channel A / process-exit.

    Each request carries a unique ``request_id`` so the actor can reply on
    the originating stream without leaking producer state. Watermark
    requests additionally carry ``required_channel_a_byte_offset`` so the
    actor can synchronize exactly the lines a producer needs.
    """

    request_id: str
    kind: str  # 'channel_a_observation' | 'channel_b_proposal' | 'process_exit' | 'watermark_ack'
    payload: Any = None


@dataclass(frozen=True, slots=True)
class LifecycleActorResponse:
    """Immutable reply the actor sends back to a single request.

    Watermark replies carry the ``processed_channel_a_byte_offset`` so the
    requesting producer knows exactly when its requested offset was
    reduced end-to-end.
    """

    request_id: str
    snapshot: ChildLifecycleSnapshot
    decision: LifecycleDecision = LifecycleDecision.CONTINUE
    eligible_candidate: CompletionCandidate | None = None
    processed_channel_a_byte_offset: int = 0


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    """Owned process identity rooted at one spawned ``proc.pid``.

    Captures the root PID, start time, and process group/session, plus a
    refreshed snapshot of descendant identities for descendant-safe cleanup.
    Distinct from the observed workload identity used for tracing/callbacks.
    """

    root_pid: int
    start_time: float
    process_group_id: int = 0
    session_id: int = 0
    descendants: tuple[tuple[int, float], ...] = ()
    """Refreshed descendant (pid, start_time) pairs while the root is alive."""


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


@runtime_checkable
class StreamParserFactory(Protocol):
    """Zero-argument factory returning a fresh parser per call.

    Replaces the previous ``StreamParser | None`` parameter on the runner.
    Each attempt must invoke the factory exactly once so concurrent
    watchers/calls cannot share lifecycle/correlation state by accident.
    """

    def __call__(self) -> Any:
        """Build a fresh parser instance.

        Returns an object conforming to the ``StreamParser`` protocol
        (kept as ``Any`` here to avoid the IL-0 dependency cycle on
        ``_type_protocols_backend``). Concrete implementations return
        their own backend-specific ``ClaudeStreamParser``/``CodexStreamParser``.
        """
        ...


def build_lifecycle_snapshot_from_attempts(
    active: tuple[ChildLifecycleObservation, ...],
    completed: tuple[ChildLifecycleObservation, ...],
    unresolved_terminal: tuple[ChildLifecycleObservation, ...],
    candidate_states: tuple[tuple[str, CompletionCandidateState], ...],
    eligible_candidate: CompletionCandidate | None = None,
    last_deferred_parent_generation: int = 0,
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
    )


ChildLifecycleCoordinatorFactory = Callable[..., Any]
"""Public alias for the coordinator-actor factory consumed by the runner.

The factory accepts the immutable observations producer and yields a
frozen snapshot every time it is awaited. Tests instantiate directly.
"""
