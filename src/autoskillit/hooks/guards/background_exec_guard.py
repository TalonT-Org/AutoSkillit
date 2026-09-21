#!/usr/bin/env python3
"""PreToolUse hook — blocks run_in_background=true in skill sessions (ADR-0001)
and enforces Claude required-join dispatch boundaries when the session has
loaded a join-bearing skill (REQ-JOIN-005, REQ-BACK-005).

Background execution causes race conditions and lost results. Skill sessions
must use foreground execution only. Multiple foreground tool calls in a single
message execute concurrently without this risk.

Join-bound sessions additionally reject:
  * ``name`` or ``team_name`` selectors (which spawn teammates when agent
    teams are active — confirmed via code.claude.com/docs/en/agent-teams);
  * ``run_in_background=true`` (the original ADR-0001 prohibition);
  * ``ScheduleWakeup`` (deferral/stall escape hatch).
The guard derives session identity from the hook payload; a binding with
``join_required=true`` activates the join-bound deny set. An absent, unreadable,
or malformed binding defaults to non-join semantics because a transient
file-system error during hook invocation must not lock the agent out of
legitimate work.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)
_RUNTIME_DIR = str(Path(_HOOKS_DIR) / "_runtime")
if _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)


from _hook_payload import normalize_payload_cwd  # noqa: E402
from _hook_settings import (  # noqa: E402
    hook_session_shape,
    payload_managed_codex_route,
    session_join_required,
)

BACKGROUND_EXEC_DENY_TRIGGER: str = "run_in_background=true is prohibited in skill sessions"
SCHEDULE_WAKEUP_DENY_TRIGGER: str = "ScheduleWakeup is prohibited in skill sessions"
JOIN_DENY_TRIGGER: str = (
    "required-join session forbids named/teammate dispatch — declare a wave "
    "via declare_join_batch and use unnamed foreground Agent(...) calls"
)
MANAGED_CODEX_CHILD_DENY_TRIGGER: str = (
    "managed Codex routes cannot launch native or background child work"
)


def _governed_skill_session() -> bool:
    """Whether this hook is acting in a governed Claude skill session tier.

    Active for Claude-code sessions on the skill tier, so orchestrator, fleet,
    and Codex sessions are excluded from governance. The headless axis is not
    consulted here — the caller in main() short-circuits interactive sessions
    via `if not headless: sys.exit(0)` before this helper runs.
    """
    backend = os.environ.get("AUTOSKILLIT_AGENT_BACKEND", "").strip()
    if backend == "codex":
        return False
    if backend not in ("", "claude-code"):
        return False
    _, session_type = hook_session_shape()
    if session_type in ("orchestrator", "fleet"):
        return False
    return True


def _emit_deny(reason: str) -> None:
    payload = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    )
    sys.stdout.write(payload + "\n")


def _managed_route_denial(
    payload_cwd: str | None,
    session_id: object,
    tool_name: object,
    tool_input: dict[str, object],
) -> str | None:
    managed_route = payload_managed_codex_route(payload_cwd, session_id)
    if managed_route is None:
        return None
    route, guards, _config_digest = managed_route
    if "background_exec_guard" not in guards:
        return f"{MANAGED_CODEX_CHILD_DENY_TRIGGER} ({route} binding omits background_exec_guard)."
    blocked_names = {"Agent", "spawn_agent", "code_mode", "background"}
    if tool_name in blocked_names or tool_input.get("run_in_background"):
        return f"{MANAGED_CODEX_CHILD_DENY_TRIGGER} ({route} route)."
    return None


def _join_bound_denial(
    is_governed: bool,
    in_subagent_context: bool,
    payload_cwd: str | None,
    session_id: object,
    tool_name: object,
    tool_input: dict[str, object],
) -> str | None:
    join_required = (
        is_governed
        and not in_subagent_context
        and isinstance(session_id, str)
        and bool(session_id)
        and bool(payload_cwd)
        and session_join_required(payload_cwd, session_id)
    )
    if not join_required:
        return None
    if tool_name == "Agent":
        selector = [
            name for name in ("name", "team_name", "run_in_background") if tool_input.get(name)
        ]
        if selector:
            return (
                f"{JOIN_DENY_TRIGGER} (selectors rejected: {', '.join(selector)}; "
                "background execution and teammate routing are prohibited in a "
                "join-bound session — declare a wave via declare_join_batch and "
                "issue every member as one ordinary unnamed foreground Agent call)."
            )
    if tool_name == "ScheduleWakeup":
        return (
            f"{SCHEDULE_WAKEUP_DENY_TRIGGER} (ADR-0001) — ScheduleWakeup is "
            "prohibited in a join-bound session because deferral cannot "
            "produce the declared-batch evidence the join contract requires."
        )
    return None


def _headless_background_denial(
    session_type: str,
    tool_name: object,
    tool_input: dict[str, object],
) -> str | None:
    if tool_name != "ScheduleWakeup" and not tool_input.get("run_in_background"):
        return None
    deny_trigger = (
        SCHEDULE_WAKEUP_DENY_TRIGGER
        if tool_name == "ScheduleWakeup"
        else BACKGROUND_EXEC_DENY_TRIGGER
    )
    denial_reason = (
        f"{deny_trigger} (ADR-0001). "
        "Background execution causes race conditions and lost results. "
        "Use foreground execution — multiple tool calls in a single message "
        "execute concurrently."
    )
    if session_type and session_type != "skill":
        denial_reason += (
            f" (AUTOSKILLIT_SESSION_TYPE={session_type!r} is not a recognized tier;"
            " expected: orchestrator, fleet, or skill)"
        )
    return denial_reason


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)  # fail-open on malformed input
    if not isinstance(data, dict):
        sys.exit(0)

    in_subagent_context = bool(data.get("agent_id"))

    headless, session_type = hook_session_shape()
    if session_type in ("orchestrator", "fleet"):
        sys.exit(0)  # permitted tiers

    is_governed = _governed_skill_session()

    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        sys.exit(0)  # fail-open: missing or malformed tool_input

    tool_name = data.get("tool_name")
    session_id = data.get("session_id")
    payload_cwd = normalize_payload_cwd(data.get("cwd"))
    denial_reason = _managed_route_denial(payload_cwd, session_id, tool_name, tool_input)
    if denial_reason is not None:
        _emit_deny(denial_reason)
        sys.exit(0)

    # --- Join-bound session enforcement (Claude, all session types) ---
    # Inside a claimed child's own subagent context, exempt join re-evaluation:
    # blocking them would self-lock every join.
    denial_reason = _join_bound_denial(
        is_governed,
        in_subagent_context,
        payload_cwd,
        session_id,
        tool_name,
        tool_input,
    )
    if denial_reason is not None:
        _emit_deny(denial_reason)
        sys.exit(0)

    if not headless:
        # Interactive non-governed sessions fall through after the join check.
        sys.exit(0)

    # --- ADR-0001 background/SessionWakeup gate (headless only) ---
    denial_reason = _headless_background_denial(session_type, tool_name, tool_input)
    if denial_reason is not None:
        _emit_deny(denial_reason)

    sys.exit(0)


if __name__ == "__main__":
    main()
