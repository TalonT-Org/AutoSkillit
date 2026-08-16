#!/usr/bin/env python3
"""PreToolUse guard — deny non-Agent follow-up effects while a wave is unresolved.

When the session flag (or ``AUTOSKILLIT_JOIN_REQUIRED=1``) reports
``join_required=true``, a top-level parent turn (no ``agent_id`` in the
hook payload) may not issue any non-Agent tool call before every
expected direct ``Agent`` handle has settled. The natural tool calls
inside a claimed child's own subagent context (``agent_id`` present) are
exempt — blocking them would self-lock every join.

The guard is matcherless so it runs on every PreToolUse event regardless
of tool name. Exit code 2 prevents Claude from proceeding and continues
the conversation per the Claude Code hooks contract.

Stdlib-only — no autoskillit imports.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _hook_settings import (  # type: ignore[import-not-found]  # noqa: E402
    write_join_diagnostic,
)
from _hook_utils import find_project_root  # type: ignore[import-not-found]  # noqa: E402
from _join_ledger import (  # type: ignore[import-not-found]  # noqa: E402
    JoinLedgerError,
    active_batch,
)

JOIN_FOLLOWUP_DENY_TRIGGER: str = (
    "required-join wave is unresolved: top-level parent may not invoke non-Agent "
    "follow-up effects before every declared Agent handle settles"
)


def _session_join_required() -> bool:
    flag_path = os.environ.get("AUTOSKILLIT_JOIN_FLAG_PATH", "").strip()
    if flag_path:
        try:
            raw = open(flag_path, encoding="utf-8").read()
        except OSError:
            raw = ""
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            parsed = None
        if isinstance(parsed, dict) and bool(parsed.get("join_required", False)):
            return True
    return os.environ.get("AUTOSKILLIT_JOIN_REQUIRED") == "1"


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

    if data.get("agent_id"):
        sys.exit(0)

    if not _session_join_required():
        sys.exit(0)

    tool_name = data.get("tool_name")
    if not isinstance(tool_name, str) or tool_name == "Agent":
        sys.exit(0)

    session_id = _resolve_session_id(data)
    if not session_id:
        sys.exit(0)

    top_level_parent = "top_level"
    flag_dir = find_project_root() / ".autoskillit" / "temp"
    try:
        batch = active_batch(
            flag_dir,
            session_id=session_id,
            top_level_parent=top_level_parent,
        )
    except JoinLedgerError:
        sys.exit(0)

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
