#!/usr/bin/env python3
"""PreToolUse guard — deny non-Agent follow-up effects while a wave is unresolved.

When the payload-identified session binding reports ``join_required=true``, a
top-level parent turn (no ``agent_id`` in the hook payload) may not issue any
non-Agent tool call before every expected direct ``Agent`` handle has settled.
The natural tool calls
inside a claimed child's own subagent context (``agent_id`` present) are
exempt — blocking them would self-lock every join.

The guard is matcherless so it runs on every PreToolUse event regardless
of tool name. Exit code 2 prevents Claude from proceeding and continues
the conversation per the Claude Code hooks contract.

Stdlib-only — no autoskillit imports.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _hook_payload import (  # type: ignore[import-not-found]  # noqa: E402
    normalize_payload_cwd,
    resolve_state_root,
)
from _hook_settings import (  # type: ignore[import-not-found]  # noqa: E402
    session_join_required,
    session_managed_codex_route,
    session_managed_scope,
    write_join_diagnostic,
)
from _join_ledger import (  # type: ignore[import-not-found]  # noqa: E402
    active_batch,
    resolve_flag_dir,
)

JOIN_FOLLOWUP_DENY_TRIGGER: str = (
    "required-join wave is unresolved: top-level parent may not invoke non-Agent "
    "follow-up effects before every declared Agent handle settles"
)
_MANAGED_PARENT_ALLOWED_TOOLS: frozenset[str] = frozenset(
    {"run_fixed_batch", "read_fixed_batch_result"}
)


def _resolve_session_id(data: dict[str, object]) -> str:
    sid = data.get("session_id", "")
    return sid if isinstance(sid, str) else ""


def _is_unresolved(batch: dict[str, object]) -> bool:
    """Return True when the wave is active but not yet ``complete``."""
    if batch.get("_corrupted"):
        return True
    wave_outcome = batch.get("wave_outcome", "pending")
    return wave_outcome != "complete"


def _denial_reason(tool_name: str) -> str:
    return (
        f"required-join wave is unresolved: top-level parent may not invoke "
        f"{tool_name!r} before every declared Agent handle settles. "
        "Wait for the JoinLedger wave_outcome to reach 'complete' before "
        "issuing side-effecting follow-up tools."
    )


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)

    if not isinstance(data, dict):
        sys.exit(0)
    if data.get("agent_id"):
        sys.exit(0)

    session_id = _resolve_session_id(data)
    payload_cwd = normalize_payload_cwd(data.get("cwd"))
    if not session_id or not payload_cwd:
        sys.exit(0)
    if not session_join_required(payload_cwd, session_id):
        sys.exit(0)

    tool_name = data.get("tool_name")
    managed_route = session_managed_codex_route(payload_cwd, session_id)
    if managed_route is not None:
        route, guards, _config_digest = managed_route
        if route == "leaf":
            sys.exit(0)
        if "join_followup_guard" not in guards:
            sys.stdout.write(
                json.dumps(
                    {
                        "decision": "block",
                        "reason": "managed Codex parent binding omits join_followup_guard.",
                    }
                )
                + "\n"
            )
            sys.exit(2)
        if (
            isinstance(tool_name, str)
            and tool_name.split("__")[-1] in _MANAGED_PARENT_ALLOWED_TOOLS
        ):
            sys.exit(0)
    if not isinstance(tool_name, str) or tool_name == "Agent":
        sys.exit(0)

    scope = session_managed_scope(payload_cwd, session_id)
    if scope is None:
        write_join_diagnostic(
            {
                "gate": "join_followup_guard",
                "session_id": session_id,
                "status": "block",
                "denial_reason": "invalid_managed_scope",
            },
            caller="join_followup_guard",
        )
        sys.stdout.write(
            json.dumps(
                {
                    "decision": "block",
                    "reason": "required-join binding has no valid managed scope.",
                }
            )
            + "\n"
        )
        sys.exit(2)
    top_level_parent, _managed_leaf_id = scope
    flag_dir = resolve_flag_dir(resolve_state_root(payload_cwd))
    batch = active_batch(
        flag_dir,
        session_id=session_id,
        top_level_parent=top_level_parent,
    )

    if batch is None or not _is_unresolved(batch):
        sys.exit(0)

    write_join_diagnostic(
        {
            "gate": "join_followup_guard",
            "session_id": session_id,
            "top_level_parent": top_level_parent,
            "tool_use_id": data.get("tool_use_id", "") if isinstance(data, dict) else "",
            "wave_outcome": batch.get("wave_outcome", ""),
            "status": "block",
            "selector_presence": [tool_name],
        },
        caller="join_followup_guard",
    )
    sys.stdout.write(json.dumps({"decision": "block", "reason": _denial_reason(tool_name)}) + "\n")
    sys.exit(2)


if __name__ == "__main__":
    main()
