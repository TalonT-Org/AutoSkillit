"""PreToolUse gate: block wait_for_ci/enqueue_pr when check_review_loop not yet called."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _hook_payload import (  # type: ignore[import-not-found]  # noqa: E402
    parse_hook_command,
    resolve_state_root,
)

REVIEW_LOOP_DENY_TRIGGER: str = "REVIEW LOOP REQUIRED"

_DENY_REASON = (
    "REVIEW LOOP REQUIRED: review_pr returned changes_requested and "
    "resolve_review applied fixes, but check_review_loop was not called. "
    "You MUST call run_python with callable="
    "'autoskillit.smoke_utils.check_review_loop' with the correct "
    "pr_number, cwd, current_iteration, max_iterations, and "
    "previous_verdict parameters BEFORE proceeding to "
    "wait_for_ci/enqueue_pr. "
    "Recipe routing: resolve_review → pre_review_rebase (run_python) → "
    "[clean → re_push_review | conflicts → resolve_pre_review_conflicts → "
    "re_push_review] → check_review_loop."
)

_STATE_FILE_RELPATH = (".autoskillit", "temp", "review_gate_state.json")


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
        if not isinstance(data, dict):
            sys.exit(0)
        payload_cwd = parse_hook_command(data).payload_cwd
        project_root = resolve_state_root(payload_cwd)
    except Exception:
        sys.exit(0)

    state_file = project_root.joinpath(*_STATE_FILE_RELPATH)
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
