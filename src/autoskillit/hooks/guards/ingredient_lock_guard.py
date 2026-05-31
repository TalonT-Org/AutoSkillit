"""PreToolUse guard: deny run_skill calls for steps locked by lock_ingredients.

Supplemental enforcement — the server-side check in run_skill is the primary
gate. This hook provides early UX feedback (deny message shown to the orchestrator)
and audit trail.

Fail-open: malformed input or missing files result in sys.exit(0) (allow).
"""

from __future__ import annotations

import json
import os
import sys

# Sibling import bootstrap (same pattern as quota_guard.py)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _hook_settings import read_merged_hook_config  # noqa: E402

INGREDIENT_LOCK_DENY_TRIGGER: str = "INGREDIENT LOCK ENFORCED"

DISPATCH_ID_ENV_VAR = "AUTOSKILLIT_DISPATCH_ID"


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.exit(0)
        event = json.loads(raw)
    except (json.JSONDecodeError, Exception):
        sys.exit(0)

    tool_input = event.get("tool_input", {})
    step_name = tool_input.get("step_name", "")
    if not step_name:
        sys.exit(0)

    config = read_merged_hook_config()
    locked_steps = config.get("locked_steps", {})
    if not locked_steps:
        sys.exit(0)

    order_id = tool_input.get("order_id", "") or os.environ.get(DISPATCH_ID_ENV_VAR, "")

    denied = False
    deny_pipeline = ""
    if order_id and order_id in locked_steps:
        if locked_steps[order_id].get(step_name) is False:
            denied = True
            deny_pipeline = order_id
    elif not order_id:
        for pid, steps in locked_steps.items():
            if steps.get(step_name) is False:
                denied = True
                deny_pipeline = pid
                break

    if denied:
        locked_ingredients = config.get("locked_ingredients", {}).get(deny_pipeline, {})
        reason = (
            f"{INGREDIENT_LOCK_DENY_TRIGGER}: Step '{step_name}' is locked out. "
            f"Locked ingredients for pipeline '{deny_pipeline}': {locked_ingredients}. "
            f"Call lock_ingredients(unlock=[...]) to release."
        )
        sys.stdout.write(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
