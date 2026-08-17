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
The guard reads the session flag as JSON; ``join_required=true`` activates the
join-bound deny set. When the binding flag is configured but unreadable
or malformed, the guard defaults to non-join semantics rather than promoting
to ``join_required=true`` — the launch policy and active session binding are
authoritative, and a transient file-system error during hook invocation must
not lock the agent out of legitimate work.
"""

from __future__ import annotations

import json
import os
import sys

BACKGROUND_EXEC_DENY_TRIGGER: str = "run_in_background=true is prohibited in skill sessions"
SCHEDULE_WAKEUP_DENY_TRIGGER: str = "ScheduleWakeup is prohibited in skill sessions"
JOIN_DENY_TRIGGER: str = (
    "required-join session forbids named/teammate dispatch — declare a wave "
    "via declare_join_batch and use unnamed foreground Agent(...) calls"
)


def _read_session_binding() -> dict[str, object] | None:
    """Read the session flag as JSON. Returns None when absent or unreadable."""
    flag_path = os.environ.get("AUTOSKILLIT_JOIN_FLAG_PATH", "").strip()
    if not flag_path:
        return None
    try:
        with open(flag_path, encoding="utf-8") as handle:
            raw = handle.read()
    except OSError:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _governed_skill_session() -> bool:
    """Whether this hook is acting in a governed Claude skill session.

    The guard is now active in interactive Claude sessions too (the previous
    headless-only early-exit was removed because it left an interactive escape
    hatch for the #4575 class of lost teammate results). It is still inert in
    orchestrator/fleet tiers and in clean interactive sessions that have not
    loaded a join-bearing skill.
    """
    backend = os.environ.get("AUTOSKILLIT_AGENT_BACKEND", "").strip()
    if backend == "codex":
        return False
    if backend not in ("", "claude-code"):
        return False
    raw_session_type = os.environ.get("AUTOSKILLIT_SESSION_TYPE", "")
    session_type = raw_session_type.lower()
    if session_type in ("orchestrator", "fleet"):
        return False
    return True


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)  # fail-open on malformed input

    in_subagent_context = bool(data.get("agent_id"))

    raw_session_type = os.environ.get("AUTOSKILLIT_SESSION_TYPE", "")
    session_type = raw_session_type.lower()
    if session_type in ("orchestrator", "fleet"):
        sys.exit(0)  # permitted tiers

    is_governed = _governed_skill_session()
    headless = os.environ.get("AUTOSKILLIT_HEADLESS") == "1"

    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        sys.exit(0)  # fail-open: missing or malformed tool_input

    tool_name = data.get("tool_name")

    join_required = False  # default; tightened below when the binding is consulted

    # --- Join-bound session enforcement (Claude, all session types) ---
    # Inside a claimed child's own subagent context, exempt join re-evaluation:
    # blocking them would self-lock every join.
    if is_governed and not in_subagent_context:
        binding = _read_session_binding()
        join_required = bool(binding.get("join_required", False)) if binding is not None else False
        if not join_required and os.environ.get("AUTOSKILLIT_JOIN_REQUIRED") == "1":
            join_required = True

        if join_required and tool_name == "Agent":
            selector = []
            if tool_input.get("name"):
                selector.append("name")
            if tool_input.get("team_name"):
                selector.append("team_name")
            if tool_input.get("run_in_background"):
                selector.append("run_in_background")
            if selector:
                denial_reason = (
                    f"{JOIN_DENY_TRIGGER} (selectors rejected: {', '.join(selector)}; "
                    "background execution and teammate routing are prohibited in a "
                    "join-bound session — declare a wave via declare_join_batch and "
                    "issue every member as one ordinary unnamed foreground Agent call)."
                )
                payload = json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": denial_reason,
                        }
                    }
                )
                sys.stdout.write(payload + "\n")
                sys.exit(0)

    # --- Join-bound ScheduleWakeup rejection (independent of headless state) ---
    # Deferral/stall is an escape hatch that could let a wave close with an
    # empty child set. Reject ScheduleWakeup whenever the session reports a
    # join-bearing skill load, even in interactive Claude sessions that have
    # not entered the headless tier.
    if is_governed and join_required and tool_name == "ScheduleWakeup":
        denial_reason = (
            f"{SCHEDULE_WAKEUP_DENY_TRIGGER} (ADR-0001) — ScheduleWakeup is "
            "prohibited in a join-bound session because deferral cannot "
            "produce the declared-batch evidence the join contract requires."
        )
        payload = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": denial_reason,
                }
            }
        )
        sys.stdout.write(payload + "\n")
        sys.exit(0)

    if not headless:
        # Interactive non-governed sessions fall through after the join check.
        sys.exit(0)

    # --- ADR-0001 background/SessionWakeup gate (headless only) ---
    if tool_name == "ScheduleWakeup" or tool_input.get("run_in_background"):
        deny_trigger = (
            SCHEDULE_WAKEUP_DENY_TRIGGER
            if tool_name == "ScheduleWakeup"
            else BACKGROUND_EXEC_DENY_TRIGGER
        )
        _unrecognized_tier = bool(session_type) and session_type != "skill"
        denial_reason = (
            f"{deny_trigger} (ADR-0001). "
            "Background execution causes race conditions and lost results. "
            "Use foreground execution — multiple tool calls in a single message "
            "execute concurrently."
        )
        if _unrecognized_tier:
            denial_reason += (
                f" (AUTOSKILLIT_SESSION_TYPE={raw_session_type!r} is not a recognized tier;"
                " expected: orchestrator, fleet, or skill)"
            )
        payload = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": denial_reason,
                }
            }
        )
        sys.stdout.write(payload + "\n")

    sys.exit(0)


if __name__ == "__main__":
    main()
