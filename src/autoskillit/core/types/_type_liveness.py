"""Liveness policy, attempt factory, and operation-ledger type contracts.

Zero autoskillit imports outside this sub-package. Provides the frozen,
slotted value types that codify the Codex L2 attempt liveness contract.

These types are introduced by the ``rectify_codex_l2_attempt_liveness``
plan. They are sliced into multiple steps:

- Slice A — :class:`ChildTransportSpec`, :class:`NestedSessionSpec`,
  :class:`AttemptLivenessSpec`. The first-consumed immutable policy.
- Slice B — :class:`AttemptSeed` (attempt identity, sole child environment,
  immutable wall/timeout inputs).
- Slice C — :class:`OperationObservation` and associated enum kinds.
- Slice D — :class:`LivenessObservation` and :class:`LivenessDecision`
  with their reason/action enums.
- Slice F — :class:`BackendPreLaunchSpec`, :class:`LivenessDiagnostics`,
  :class:`SessionLivenessDiagnostics`.

The slice annotations above are invoked here so consumers can discover
the full surface from one place; later slices add their types
immediately before their first production consumers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

__all__ = [
    # Slice A
    "AttemptLivenessSpec",
    "ChildTransportSpec",
    "NestedSessionSpec",
    # Slice B
    "AttemptSeed",
    # Slice C
    "OperationObservation",
    # Slice D
    "LivenessDecision",
    "LivenessObservation",
    # Slice F
    "BackendPreLaunchSpec",
    "LivenessDiagnostics",
    "SessionLivenessDiagnostics",
]


# ---------------------------------------------------------------------------
# Slice A
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChildTransportSpec:
    """Per-attempt child transport timings owned by backend command builders.

    These values describe how the *child* agent behaves w.r.t. its own
    stdout/CLI idle. They must never be reinterpreted as parent-process
    liveness policy. The former scalar ``stream_idle_timeout_ms`` and
    ``exit_after_stop_delay_ms`` fields live here, expressed in the
    backend's native unit, without ever crossing into the
    :class:`AttemptLivenessSpec` resolution domain.
    """

    exit_after_stop_delay_ms: int = 0
    stream_idle_timeout_ms: int = 0


@dataclass(frozen=True, slots=True)
class NestedSessionSpec:
    """Inherited child-env liveness settings for a nested AutoSkillit server.

    Captures the ``idle_output_timeout`` value (preserving explicit zero)
    that the outer headless session must serialize into the child env so
    the nested ``run_headless_core()`` entry can deserialize it as an
    explicit resolver input. Frozen + slotted to make accidental mutation
    visible at the boundary.
    """

    idle_output_timeout: float | None = None


@dataclass(frozen=True, slots=True)
class AttemptLivenessSpec:
    """Frozen values produced by the headless invocation-policy resolver.

    All values come from the actual effective invocation: backend, session
    kind, caller idle (including zero), effective stale threshold,
    canonical MCP timeout, effective wall timeout, extension cap, and
    fallback cap. The resolver freezes this spec *before* command
    construction so neither the backend builder nor the managed-runner
    attempt factory can re-derive conflicting values from ambient state.

    The managed runner and the LivenessCoordinator consume this spec
    verbatim. No other code may compute these inputs at runtime.
    """

    # Identity
    backend_name: str = ""
    session_kind: str = ""
    # Caller-supplied / configured
    caller_idle_output_timeout: float | None = None
    effective_stale_threshold: float = 0.0
    mcp_tool_timeout_sec: float = 0.0
    # Wall/extension
    effective_wall_timeout: float = 0.0
    max_extension_seconds: float = 0.0
    enable_deadline_extension: bool = False
    # Fallback suppression cap (legacy metric cap)
    max_suppression_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Slice B
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AttemptSeed:
    """Identity, sole child environment, and immutable inputs for one attempt.

    Every subprocess launch creates exactly one ``AttemptSeed``. The
    factory must atomically produce a fresh attempt ID, an immutable
    (copy-on-create) child environment, and the immutable policy +
    wall/timeout inputs it will carry.

    The managed runner's :class:`AttemptRuntime` completes the seed
    after ``anyio.open_process()`` returns with the actual child PID and
    the post-launch operation ledger / coordinator state. Provider
    retry and contract-nudge launches discard the runtime and rebuild
    a fresh seed through the same factory.
    """

    attempt_id: str
    child_env: Mapping[str, str]
    liveness_spec: AttemptLivenessSpec
    wall_duration: float
    wall_ceiling: float
    extensions_enabled: bool
    nested_spec: NestedSessionSpec | None = None


# ---------------------------------------------------------------------------
# Slice C
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OperationObservation:
    """A single typed operation lifecycle observation written by the event pump.

    Stable ID + kind + transition + raw record are observations only; they
    never count as completion/write/success evidence.
    """

    operation_id: str
    kind: str
    transition: str
    raw: Mapping[str, Any] = field(default_factory=dict)
    start_monotonic: float = 0.0
    hard_deadline_monotonic: float = 0.0


# ---------------------------------------------------------------------------
# Slice D
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LivenessObservation:
    """Snapshot fact emitted by a watcher into the LivenessCoordinator."""

    source: str  # "stdout_idle" | "channel_b" | "activity" | "deadline" | "inspector" | "wall"
    reason: str  # source-specific reason key, e.g. "idle_no_growth", "directory_missing"
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LivenessDecision:
    """Coordinators sole decision verb over a snapshot."""

    verb: str  # "CONTINUE" | "REQUEST_INSPECTION" | "EXTEND_TO" | "TERMINATE"
    target_monotonic: float = 0.0
    observation_epoch: int = 0
    reason: str = ""
    # Adapter-facing extras
    terminate_kind: str = ""  # when verb=="TERMINATE"


# ---------------------------------------------------------------------------
# Slice F
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BackendPreLaunchSpec:
    """Carries configured MCP budget into backend registration / validation.

    Constructed *after* a caller has normalized an actual config; never
    defaults the MCP tool timeout independently of configuration.
    """

    mcp_tool_timeout_sec: float


@dataclass(frozen=True, slots=True)
class LivenessDiagnostics:
    """Frozen per-attempt snapshot retained on the SubprocessResult.

    Retains every coordinator-relevant monotonic / epoch deadline plus
    conversion provenance, the resolved policy values, the learned child
    identity, the supervisor epochs, and the final operation/coordination
    observations. Never collapsed to raw scalars.
    """

    attempt_id: str = ""
    backend_name: str = ""
    session_kind: str = ""
    initial_wall_deadline: float = 0.0
    current_wall_deadline: float = 0.0
    hard_wall_ceiling_monotonic: float = 0.0
    hard_wall_ceiling_epoch: float = 0.0
    deadline_conversion_provenance: str = ""
    resolved_idle: float | None = None
    resolved_stale_threshold: float = 0.0
    resolved_extension_cap: float = 0.0
    resolved_fallback_cap: float = 0.0
    operation_caps: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))
    learned_child_session_id: str = ""
    coordinator_epochs: int = 0
    final_termination_reason: str = ""
    final_kill_reason: str = ""


@dataclass(frozen=True, slots=True)
class SessionLivenessDiagnostics:
    """Ordered per-session history of attempt LivenessDiagnostics."""

    items: tuple[LivenessDiagnostics, ...] = ()
