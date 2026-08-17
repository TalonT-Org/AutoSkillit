#!/usr/bin/env python3
"""PostToolUse / PostToolUseFailure settlement — record claimed handle outcomes.

When ``join_required=true`` in the session flag (or env mirror), every
claimed direct ``Agent`` handle must be settled with one of:

    * ``success``         — substantive public result evidence received;
    * ``failure``         — explicit terminal failure;
    * ``timeout``         — declared timeout exceeded;
    * ``cancelled``       — user cancellation;
    * ``interruption``    — user interrupt;
    * ``missing``         — no terminal evidence at all.

Identical duplicate (tool_use_id, outcome) events are idempotent.
Conflicting terminal events for the same handle fail closed.

Empty / non-substantive results are mapped to ``missing`` and never
silently converted to success.

Stdlib-only — no autoskillit imports.
"""

from __future__ import annotations

import json
import os
import sys
import time
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
    OUTCOME_CANCELLED,
    OUTCOME_FAILURE,
    OUTCOME_INTERRUPTION,
    OUTCOME_MISSING,
    OUTCOME_SUCCESS,
    OUTCOME_TIMEOUT,
    JoinLedgerError,
    resolve_flag_dir,
    settle_assignment,
)


def _resolve_outcome(event_type: str, payload: dict[str, object]) -> str | None:
    """Map an upstream event to the canonical outcome, or None to skip."""
    if event_type == "PostToolUse":
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            return OUTCOME_MISSING
        if bool(payload.get("is_error")) or bool(payload.get("error")):
            return OUTCOME_FAILURE
        tool_response = payload.get("tool_response")
        if not tool_response:
            return OUTCOME_MISSING
        return OUTCOME_SUCCESS
    if event_type == "PostToolUseFailure":
        reason = payload.get("reason") or payload.get("error")
        text = str(reason).casefold() if isinstance(reason, str) else ""
        if "timeout" in text:
            return OUTCOME_TIMEOUT
        if "cancel" in text:
            return OUTCOME_CANCELLED
        if "interrupt" in text:
            return OUTCOME_INTERRUPTION
        return OUTCOME_FAILURE
    return None


def main() -> None:
    event_type = os.environ.get("AUTOSKILLIT_HOOK_EVENT", "").strip()
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)

    # Subagent contexts are exempt: the claimed child owns its own
    # settlement surface; re-evaluating the gate here would self-lock
    # every join. Mirrors the agent_id exemption in claim/followup guards.
    if isinstance(data, dict) and data.get("agent_id"):
        sys.exit(0)

    if not session_join_required():
        sys.exit(0)

    tool_name = data.get("tool_name")
    if tool_name != "Agent":
        sys.exit(0)

    outcome = _resolve_outcome(event_type, data)
    if outcome is None:
        sys.exit(0)

    tool_use_id = data.get("tool_use_id") or ""
    if not isinstance(tool_use_id, str) or not tool_use_id:
        sys.exit(0)

    sid = data.get("session_id", "")
    if not isinstance(sid, str) or not sid:
        # join_required=true is established; missing session_id means
        # we cannot record the settlement. Emit a structured diagnostic
        # so the missing record is observable instead of silent.
        write_join_diagnostic(
            {
                "gate": "join_settle_guard",
                "tool_use_id": tool_use_id,
                "outcome": outcome,
                "status": "settle_skipped",
                "denial_reason": "missing_session_id",
            },
            caller="join_settle_guard",
        )
        sys.exit(0)

    flag_dir = resolve_flag_dir(find_project_root())
    top_level_parent = "top_level"
    batch = None
    # Retry transient OSError up to 3 attempts with brief backoff. The
    # ledger acquires an exclusive fcntl.flock; contention surfaces as
    # OSError and is normally resolved on a follow-up attempt. A contract
    # error (JoinLedgerError) is NOT retried — the ledger is the authority
    # for wave state and retrying would only re-surface the same refusal.
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            batch = settle_assignment(
                flag_dir,
                session_id=sid,
                top_level_parent=top_level_parent,
                tool_use_id=tool_use_id,
                outcome=outcome,
            )
            last_exc = None
            break
        except JoinLedgerError as exc:
            last_exc = exc
            break
        except OSError as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(0.05 * (attempt + 1))
                continue
    if batch is None:
        write_join_diagnostic(
            {
                "gate": "join_settle_guard",
                "session_id": sid,
                "tool_use_id": tool_use_id,
                "status": "settle_refused",
                "selector_presence": [outcome],
                "denial_reason": "ledger_io_or_contract_error",
            },
            caller="join_settle_guard",
        )
        sys.stderr.write(f"join_settle_guard: settlement refused: {last_exc}\n")
        # Fail closed: after retries the ledger write still failed. PostToolUse
        # exit 2 does NOT replay (per Claude Code hooks contract), so the
        # wave remains pending. The diagnostic record makes the failure
        # observable to operators via join_diagnostics.jsonl.
        sys.exit(2)

    write_join_diagnostic(
        {
            "gate": "join_settle_guard",
            "session_id": sid,
            "tool_use_id": tool_use_id,
            "join_batch_id": batch.get("join_batch_id", ""),
            "wave_outcome": batch.get("wave_outcome", ""),
            "status": outcome,
        },
        caller="join_settle_guard",
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
