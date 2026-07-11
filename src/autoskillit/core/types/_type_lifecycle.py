"""Async-child-aware completion lifecycle types (issue #4233).

Implements the typed observation + coordinator contract that gates
``kill_after_completion`` against active subagents. All types here are
IL-0 (zero autoskillit imports) and primitive-only.

Vocabulary (canonical, shared by parser, coordinator, tests):
- attempt states:    ACTIVE, COMPLETED, FAILED, CANCELLED, TIMED_OUT
- candidate states:  DEFERRED, SUPERSEDED, ELIGIBLE
- obligation states: active, satisfied, unresolved-terminal
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
    "ChildLifecycleCoordinatorFactory",
    "ChildLifecycleObservation",
    "ChildLifecycleSnapshot",
    "ChildObligationState",
    "CleanupOutcome",
    "CompletionCandidateSource",
    "CompletionCandidateState",
    "DEFAULT_CLEANUP_BUDGET_SECONDS",
    "ProcessIdentity",
    "StreamParserFactory",
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
    """Origin channel of one captured completion candidate."""

    CHANNEL_A = "channel_a"
    CHANNEL_B = "channel_b"
    PROCESS_EXIT = "process_exit"


@unique
class ChildObligationState(Enum):
    """Derived state of one child-obligation tuple (active/satisfied/unresolved)."""

    ACTIVE = "active"
    SATISFIED = "satisfied"
    UNRESOLVED_TERMINAL = "unresolved_terminal"


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


ChildLifecycleCoordinatorFactory = Callable[..., Any]
"""Public alias for the coordinator-actor factory consumed by the runner.

The factory accepts the immutable observations producer and yields a
frozen snapshot every time it is awaited. Tests instantiate directly.
"""
