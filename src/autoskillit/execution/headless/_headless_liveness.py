"""Per-session liveness resolver for headless execution.

The resolver is the single chokepoint that combines run_skill, fleet, and
caller-provided scalars into a ``SessionLivenessSpec`` consumed by every
process watcher. It must run BEFORE ``SkillSessionConfig`` or
``CmdSpec.env`` is built, so that the resolved spec — not a backend
builder's hint — drives the outer stdout/idle watchdog.

IL-1 module: imports ``autoskillit.core`` (IL-0) and ``autoskillit.config``
(IL-1), but config never imports execution. The primitive budget
computation is also exported as ``compute_legal_silence_window(...)`` for
config-coherence gates that must not pull in any execution-layer modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from autoskillit.core import LivenessSource, SessionLivenessSpec, get_logger

if TYPE_CHECKING:
    from autoskillit.config import AutomationConfig

logger = get_logger(__name__)

#: Floor added to the legal silence window before checking it against the
#: outer byte-growth watchdog. Provides breathing room for last-line
#: scheduling jitter and a partial grace before a sub-deadline kill.
DEFAULT_LEGAL_SILENCE_FLOOR_SEC: float = 100.0


@dataclass(frozen=True, slots=True)
class ResolverInputs:
    """Scalar inputs combined into the resolved liveness spec.

    All fields are positional / keyword. ``caller_idle_output_timeout``
    is ``None`` or 0.0 means "explicitly disable the outer idle watchdog".
    """

    run_skill_idle_output_timeout: float
    run_skill_stream_idle_timeout_ms: int
    run_skill_stale_threshold: float
    run_skill_max_suppression_seconds: float
    run_skill_mcp_tool_timeout_sec: float
    run_skill_timeout: float
    fleet_idle_output_timeout: float
    fleet_default_timeout_sec: float
    fleet_max_extension_seconds: float
    enable_deadline_extension: bool
    caller_idle_output_timeout: float | None
    caller_session_id: str
    is_food_truck: bool


def _legal_silence_for_skill(inp: ResolverInputs) -> float:
    """Maximum legal silence window for a skill session."""
    return min(inp.run_skill_mcp_tool_timeout_sec, inp.run_skill_timeout)


def _legal_silence_for_food_truck(inp: ResolverInputs) -> float:
    """Maximum legal silence window for a food-truck session."""
    if inp.enable_deadline_extension:
        return min(
            inp.run_skill_mcp_tool_timeout_sec,
            inp.fleet_default_timeout_sec + inp.fleet_max_extension_seconds,
        )
    return min(inp.run_skill_mcp_tool_timeout_sec, inp.fleet_default_timeout_sec)


def compute_legal_silence_window(inp: ResolverInputs) -> float:
    """Return the maximum legal silence window per session kind.

    Exposed as an IL-0-safe primitive (no execution-layer imports) so
    config-coherence gates can validate resolved specs without dragging
    in any runtime dependencies.
    """
    if inp.is_food_truck:
        return _legal_silence_for_food_truck(inp)
    return _legal_silence_for_skill(inp)


def resolve_session_liveness_spec(
    cfg: AutomationConfig,
    *,
    is_food_truck: bool,
    caller_idle_output_timeout: float | None,
    caller_session_id: str,
    enable_deadline_extension: bool,
) -> SessionLivenessSpec:
    """Resolve the per-session liveness contract from already-loaded config.

    The caller passes an :class:`AutomationConfig` instead of individual
    scalars so the resolver never reads ambient ``os.environ`` — parent
    watchdog input comes from the resolved spec, never from a separate
    ``AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT`` channel. Backend hints stay in
    the child ``CmdSpec.env`` for backward compatibility but cannot
    re-enable a watchdog that this resolver disabled.
    """
    rs = cfg.run_skill
    fl = cfg.fleet
    inp = ResolverInputs(
        run_skill_idle_output_timeout=float(rs.idle_output_timeout),
        run_skill_stream_idle_timeout_ms=int(rs.stream_idle_timeout_ms),
        run_skill_stale_threshold=float(rs.stale_threshold),
        run_skill_max_suppression_seconds=float(rs.max_suppression_seconds),
        run_skill_mcp_tool_timeout_sec=float(rs.mcp_tool_timeout_sec),
        run_skill_timeout=float(rs.timeout),
        fleet_idle_output_timeout=float(fl.idle_output_timeout),
        fleet_default_timeout_sec=float(fl.default_timeout_sec),
        fleet_max_extension_seconds=float(fl.max_extension_seconds),
        enable_deadline_extension=bool(enable_deadline_extension),
        caller_idle_output_timeout=caller_idle_output_timeout,
        caller_session_id=caller_session_id,
        is_food_truck=bool(is_food_truck),
    )
    explicit_idle_disabled = (
        caller_idle_output_timeout is not None and caller_idle_output_timeout == 0
    )

    if is_food_truck:
        default_idle = inp.fleet_idle_output_timeout
    else:
        default_idle = inp.run_skill_idle_output_timeout

    if explicit_idle_disabled:
        stdout_idle_timeout_sec: float | None = None
    elif caller_idle_output_timeout is not None and caller_idle_output_timeout > 0:
        stdout_idle_timeout_sec = float(caller_idle_output_timeout)
    elif default_idle > 0.0:
        stdout_idle_timeout_sec = float(default_idle)
    else:
        stdout_idle_timeout_sec = None

    legal_silence = compute_legal_silence_window(inp)
    operation_deadline_sec = legal_silence + DEFAULT_LEGAL_SILENCE_FLOOR_SEC

    authorized_sources: frozenset[LivenessSource] = frozenset(
        {
            LivenessSource.STDOUT_GROWTH,
            LivenessSource.CHANNEL_B_GROWTH,
            LivenessSource.EXECUTION_MARKER,
            LivenessSource.OPERATION_IN_FLIGHT,
        }
    )

    spec = SessionLivenessSpec(
        stdout_idle_timeout_sec=stdout_idle_timeout_sec,
        stale_threshold_sec=inp.run_skill_stale_threshold,
        operation_deadline_sec=operation_deadline_sec,
        mcp_tool_timeout_sec=inp.run_skill_mcp_tool_timeout_sec,
        wall_timeout_sec=inp.run_skill_timeout
        if not is_food_truck
        else inp.fleet_default_timeout_sec,
        explicit_idle_disabled=explicit_idle_disabled,
        caller_session_id=caller_session_id,
        authorized_sources=authorized_sources,
    )
    logger.debug(
        "session_liveness_spec_resolved",
        is_food_truck=is_food_truck,
        stdout_idle_timeout_sec=spec.stdout_idle_timeout_sec,
        operation_deadline_sec=spec.operation_deadline_sec,
        explicit_idle_disabled=spec.explicit_idle_disabled,
        caller_session_id=caller_session_id,
        legal_silence_window=legal_silence,
    )
    return spec
