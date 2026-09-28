#!/usr/bin/env python3
"""PreToolUse hook — blocks dispatch_food_truck from headless callers.

Defense-in-depth: dispatch_food_truck must never be called from a headless
session regardless of SESSION_TYPE. This closes L3→L3 recursion where a
fleet session spawns another fleet session via dispatch.

Interactive callers (cook with kitchen open) are always permitted.
"""

import json
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)
_RUNTIME_DIR = str(Path(_HOOKS_DIR) / "_runtime")
if _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)


from _hook_output import deny_tool_use  # noqa: E402
from _hook_settings import (  # noqa: E402
    enforce_session_scope,
    hook_session_shape,
)

FLEET_DISPATCH_DENY_TRIGGER: str = "dispatch_food_truck cannot be called from headless sessions"


def main() -> None:
    enforce_session_scope("headless_only")

    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.stderr.write("fleet_dispatch_guard: malformed stdin — failing open\n")
        sys.exit(0)  # fail-open on malformed input

    if not isinstance(data, dict):
        sys.stderr.write("fleet_dispatch_guard: unexpected JSON root type — failing open\n")
        sys.exit(0)

    _headless, tier = hook_session_shape()
    if tier and tier != "fleet":
        deny_tool_use(f"dispatch_food_truck requires fleet session (current: {tier})")

    # Headless: check if this is dispatch_food_truck
    tool_name: str = data.get("tool_name", "")
    tool = tool_name.split("__")[-1]
    if tool == "dispatch_food_truck":
        deny_tool_use(
            "dispatch_food_truck cannot be called from headless sessions. "
            "This tool is only available to interactive callers (cook). "
            "Headless dispatch would create recursive L2 (food truck) sessions."
        )

    sys.exit(0)


if __name__ == "__main__":
    main()
