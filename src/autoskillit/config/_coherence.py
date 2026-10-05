"""Timeout-coherence gates for cross-cutting runtime configuration.

Owns the MCP tool-timeout and process-tether coherence gates plus the helper
``compute_codex_mcp_tool_timeout``.

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
