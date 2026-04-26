"""PreToolUse gate: block wait_for_ci/enqueue_pr when check_review_loop not yet called."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_DENY_REASON = (
    "REVIEW LOOP REQUIRED: review_pr returned changes_requested and "
    "resolve_review applied fixes, but check_review_loop was not called. "
    "You MUST call run_python with callable="
    "'autoskillit.smoke_utils.check_review_loop' with the correct "
    "pr_number, cwd, current_iteration, max_iterations, and "
    "previous_verdict parameters BEFORE proceeding to "
    "wait_for_ci/enqueue_pr. "
    "Recipe routing: re_push_review → on_success: check_review_loop."
)

_STATE_FILE_RELPATH = (".autoskillit", "temp", "review_gate_state.json")


def main() -> None:
    try:
        json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    state_file = Path.cwd().joinpath(*_STATE_FILE_RELPATH)
    if not state_file.exists():
        sys.exit(0)

    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        sys.exit(0)

    if state.get("gate") != "LOOP_REQUIRED":
        sys.exit(0)

    if state.get("check_review_loop_called"):
        sys.exit(0)

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": _DENY_REASON,
                }
            }
        )
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
