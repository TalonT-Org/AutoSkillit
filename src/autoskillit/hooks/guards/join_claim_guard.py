#!/usr/bin/env python3
"""PreToolUse guard — atomically claim one declared assignment per direct Agent call.

When the session flag (or ``AUTOSKILLIT_JOIN_REQUIRED=1``) reports
``join_required=true``, every top-level direct ``Agent`` tool_use_id must
claim one slot in the active wave declared by ``declare_join_batch``.
The claim is recorded in the shared join ledger (``_join_ledger.py``).

Denial reasons:
    * no active wave for this (session_id, top_level_parent) pair,
    * duplicate claim for the same tool_use_id,
    * no unclaimed assignment available,
    * tool call carries ``name``, ``team_name``, or ``run_in_background``
      (delegated to ``background_exec_guard`` for the join selectors),
    * tool input lacks a usable ``tool_use_id`` (we cannot correlate).

Nested descendants (``agent_id`` present in the payload) are exempt —
the agent_id belongs to a claimed child's own context. Tool calls made
inside a claimed child's subagent context are exempt from this check;
blocking them would self-lock every join.

Stdlib-only — no autoskillit imports.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _hook_settings import (  # type: ignore[import-not-found]  # noqa: E402
    session_join_required,
    write_join_diagnostic,
)
from _hook_utils import find_project_root  # type: ignore[import-not-found]  # noqa: E402
from _join_ledger import (  # type: ignore[import-not-found]  # noqa: E402
    JoinLedgerError,
    claim_assignment,
    resolve_flag_dir,
)

JOIN_CLAIM_DENY_TRIGGER: str = (
    "required-join session requires a declared batch with an unclaimed assignment"
)


def _resolve_session_id(data: dict[str, object]) -> str:
    sid = data.get("session_id", "")
    return sid if isinstance(sid, str) else ""


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)

    if not isinstance(data, dict):
        sys.exit(0)
    if data.get("agent_id"):
        # Inside a claimed child's own subagent context — exempt.
        sys.exit(0)

    if not session_join_required():
        sys.exit(0)

    tool_name = data.get("tool_name")
    if tool_name != "Agent":
        sys.exit(0)

    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        sys.exit(0)

    tool_use_id = data.get("tool_use_id") or tool_input.get("id") or ""
    if not isinstance(tool_use_id, str) or not tool_use_id:
        denial_reason = (
            f"{JOIN_CLAIM_DENY_TRIGGER}: Agent tool_use_id was not provided by the harness."
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

    flag_dir = resolve_flag_dir(find_project_root())
    session_id = _resolve_session_id(data)
    top_level_parent = "top_level"
    if not session_id:
        # join_required=true is established; missing session_id is a
        # fail-closed condition — we cannot attribute this Agent call
        # to a declared batch, so deny rather than silently pass.
        write_join_diagnostic(
            {
                "gate": "join_claim_guard",
                "tool_use_id": tool_use_id,
                "status": "deny",
                "denial_reason": "missing_session_id",
            },
            caller="join_claim_guard",
        )
        denial_reason = (
            f"{JOIN_CLAIM_DENY_TRIGGER}: session_id was not provided by the harness "
            "for an Agent call in a join-required session."
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

    try:
        claimed = claim_assignment(
            flag_dir,
            session_id=session_id,
            top_level_parent=top_level_parent,
            tool_use_id=tool_use_id,
        )
    except (JoinLedgerError, OSError) as exc:
        write_join_diagnostic(
            {
                "gate": "join_claim_guard",
                "session_id": session_id,
                "top_level_parent": top_level_parent,
                "tool_use_id": tool_use_id,
                "status": "deny",
                "selector_presence": ["missing_or_invalid_tool_use_id"],
            },
            caller="join_claim_guard",
        )
        denial_reason = f"{JOIN_CLAIM_DENY_TRIGGER}: {exc}"
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
        # Exit 0 with a structured deny payload — Claude Code treats the
        # tool call as DENY (non-zero would be treated as non-blocking).
        # The exception was already translated by the ledger into a
        # JoinLedgerError when possible; OSError here means the ledger
        # could not confirm state, and the safe default is to deny.
        sys.exit(0)

    if claimed is None:
        write_join_diagnostic(
            {
                "gate": "join_claim_guard",
                "session_id": session_id,
                "top_level_parent": top_level_parent,
                "tool_use_id": tool_use_id,
                "status": "deny_no_open_wave",
            },
            caller="join_claim_guard",
        )
        denial_reason = (
            f"{JOIN_CLAIM_DENY_TRIGGER}: no declared batch is open for this turn. "
            "Call declare_join_batch with one assignment label per direct child first."
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

    write_join_diagnostic(
        {
            "gate": "join_claim_guard",
            "session_id": session_id,
            "top_level_parent": top_level_parent,
            "tool_use_id": tool_use_id,
            "join_batch_id": claimed.get("join_batch_id", ""),
            "assignment": claimed.get("label", ""),
            "status": "claim",
        },
        caller="join_claim_guard",
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
