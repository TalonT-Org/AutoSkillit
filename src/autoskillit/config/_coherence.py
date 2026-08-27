"""Timeout-coherence gates for cross-cutting runtime configuration.

Owns: ``_timeout_coherence_gate`` (idle_output_timeout vs known long-poll tools),
``_codex_mcp_timeout_coherence_gate`` and ``_claude_mcp_timeout_coherence_gate``
(MCP tool-timeout vs session-max), ``_process_tether_coherence_gate`` (orphan
ceiling vs session-max), the helper ``compute_codex_mcp_tool_timeout``, and the
shared time-limit constants ``_MERGE_QUEUE_DEFAULT``, ``_MERGE_QUEUE_RECIPE_MAX``,
``_CI_WATCH_DEFAULT``.

The gates are warning-only: existing configs continue to work even when the gate
fires. The implementation simply emits a structlog ``logger.warning`` so on-call
operators see the race condition before it bites.
"""

from __future__ import annotations

import math

from autoskillit.config._dataclasses_execution import RunSkillConfig
from autoskillit.config._dataclasses_fleet import FleetConfig, ProcessTetherConfig
from autoskillit.core import get_logger

logger = get_logger(__name__)

# Known tool timeouts for coherence validation.
# These are the maximum observed blocking durations for tools that may produce
# zero stdout during execution — used to validate idle_output_timeout coherence.
_MERGE_QUEUE_DEFAULT = 600
_MERGE_QUEUE_RECIPE_MAX = 900
_CI_WATCH_DEFAULT = 300


def _timeout_coherence_gate(run_skill: RunSkillConfig) -> None:
    """Warn when idle_output_timeout is too low relative to known long-polling tool durations.

    The idle stall watchdog monitors raw stdout byte growth with no awareness of MCP tool
    execution state. When idle_output_timeout <= a known tool's max duration, the watchdog
    can fire and kill legitimate sessions that are simply waiting on a long poll.

    This is a WARNING-only gate — existing configs continue working.
    """
    idle = run_skill.idle_output_timeout
    if idle == 0:
        return
    if idle <= _MERGE_QUEUE_RECIPE_MAX:
        logger.warning(
            "idle_output_timeout_coherence",
            idle_output_timeout=idle,
            merge_queue_recipe_max=_MERGE_QUEUE_RECIPE_MAX,
            merge_queue_default=_MERGE_QUEUE_DEFAULT,
            ci_watch_default=_CI_WATCH_DEFAULT,
            message=(
                f"idle_output_timeout={idle}s is at or below the maximum known blocking tool "
                f"duration ({_MERGE_QUEUE_RECIPE_MAX}s for wait_for_merge_queue recipe override). "
                f"This creates a race condition where the idle stall watchdog fires before the "
                f"long-polling tool returns. Consider raising idle_output_timeout to at least "
                f"{_MERGE_QUEUE_RECIPE_MAX + 100}s, or set it to 0 to disable the watchdog "
                f"for L2 food truck sessions."
            ),
        )


def compute_codex_mcp_tool_timeout(
    run_skill: RunSkillConfig | None = None,
    fleet: FleetConfig | None = None,
) -> float:
    """Derive tool_timeout_sec from the system's timeout hierarchy.

    Uses loaded config values when provided, falls back to dataclass defaults.
    The result is always >= the floor derived from dataclass defaults.
    """
    rs = run_skill or RunSkillConfig()
    fc = fleet or FleetConfig()
    max_fleet = fc.default_timeout_sec + fc.max_extension_seconds
    max_skill = rs.timeout
    return max(max_fleet, max_skill) * 1.33


def _codex_mcp_timeout_coherence_gate(
    run_skill: RunSkillConfig, fleet: FleetConfig, *, tool_timeout: float | None = None
) -> None:
    """Warn when Codex MCP tool_timeout_sec is below the maximum session duration."""
    if tool_timeout is None:
        tool_timeout = compute_codex_mcp_tool_timeout(run_skill=run_skill, fleet=fleet)
    max_fleet = fleet.default_timeout_sec + fleet.max_extension_seconds
    max_skill = run_skill.timeout
    max_session = max(max_fleet, max_skill)
    if tool_timeout < max_session:
        logger.warning(
            "codex_mcp_tool_timeout_coherence",
            tool_timeout_sec=tool_timeout,
            max_session_duration=max_session,
            message=(
                f"Codex MCP tool_timeout_sec ({tool_timeout}s) is below the maximum "
                f"possible session duration ({max_session}s). This will cause Codex to kill "
                f"long-running MCP tool calls before autoskillit's own session management."
            ),
        )


def _claude_mcp_timeout_coherence_gate(
    run_skill: RunSkillConfig, fleet: FleetConfig, *, tool_timeout: float | None = None
) -> None:
    """Warn when Claude's MCP idle-abort timeout is below the maximum session duration.

    ``tool_timeout`` mirrors the Codex gate's signature; when omitted it defaults
    to ``run_skill.mcp_tool_timeout_sec``. Deployed/on-disk unset cases are
    covered by ``autoskillit doctor``, not this gate.
    """
    if tool_timeout is None:
        tool_timeout = run_skill.mcp_tool_timeout_sec
    if (
        not isinstance(tool_timeout, (int, float))
        or isinstance(tool_timeout, bool)
        or not math.isfinite(tool_timeout)
        or tool_timeout <= 0
    ):
        # Reject NaN/Inf/boolean/zero/negative: NaN comparison silently returns
        # False, so the gate would otherwise pass an unsound value through.
        raise ValueError(
            f"mcp_tool_timeout_sec must be a positive number of seconds, got {tool_timeout!r}."
        )
    max_fleet = fleet.default_timeout_sec + fleet.max_extension_seconds
    max_skill = run_skill.timeout
    max_session = max(max_fleet, max_skill)
    if tool_timeout < max_session:
        logger.warning(
            "claude_mcp_tool_timeout_coherence",
            tool_timeout_sec=tool_timeout,
            max_session_duration=max_session,
            message=(
                f"Claude MCP mcp_tool_timeout_sec ({tool_timeout}s) is below "
                f"the maximum possible session duration ({max_session}s). This will cause "
                f"Claude Code to idle-abort long-running MCP tool calls before autoskillit's "
                f"own session management."
            ),
        )


def _process_tether_coherence_gate(
    process_tether: ProcessTetherConfig, fleet: FleetConfig, run_skill: RunSkillConfig
) -> None:
    """Warn when orphan_ceiling_seconds undercuts the maximum session duration.

    Defaults are safe today (10800s max session vs 86400s ceiling default),
    but a user raising session timeouts past the ceiling would otherwise get
    a tether sweep whose ceiling fires before the session it's supposed to
    guard has even finished — this is the only configuration in which the
    sweep's periodic cadence becomes the binding constraint instead of the
    per-session timeout.
    """
    max_fleet = fleet.default_timeout_sec + fleet.max_extension_seconds
    max_session = max(max_fleet, run_skill.timeout)
    if process_tether.orphan_ceiling_seconds < max_session:
        logger.warning(
            "process_tether_ceiling_coherence",
            orphan_ceiling_seconds=process_tether.orphan_ceiling_seconds,
            max_session_duration=max_session,
            message=(
                f"process_tether.orphan_ceiling_seconds "
                f"({process_tether.orphan_ceiling_seconds}s) is below the maximum possible "
                f"session duration ({max_session}s). A dead-spawner sweep could reap a "
                f"headless child before its own session timeout would have fired."
            ),
        )
