"""PreToolUse hook: block run_skill calls with non-slash skill_command.

Protocol: read PreToolUse JSON event from stdin, write decision JSON to stdout, exit 0.
Expected errors (malformed JSON): fail-open (approve). Unexpected errors: fail-closed (deny).
"""

from __future__ import annotations

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

SKILL_COMMAND_DENY_TRIGGER: str = "run_skill requires a slash-command prefix"

SKILL_COMMAND_PREFIX = "/"
AUTOSKILLIT_SKILL_PREFIX = "/autoskillit:"


def main() -> None:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw)
        tool_input = event.get("tool_input", {})
        skill_command = str(tool_input.get("skill_command", SKILL_COMMAND_PREFIX)).strip()
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)  # fail-open: malformed event → approve
    except Exception as e:
        # Unexpected error (bug, import issue, etc.) — deny rather than silently approve.
        # A security validator that silently passes on unknown errors is not a validator.
        deny_tool_use(
            f"skill_command_guard encountered an unexpected error "
            f"({type(e).__name__}: {e}). "
            "Denying as a safety measure — check hook configuration."
        )

    if not skill_command.startswith(SKILL_COMMAND_PREFIX):
        deny_tool_use(
            "run_skill requires a slash-command prefix.\n"
            f"Got: {skill_command!r}\n"
            f"Expected: skill_command must start with {SKILL_COMMAND_PREFIX!r} "
            f"(e.g. {AUTOSKILLIT_SKILL_PREFIX}investigate, "
            f"{AUTOSKILLIT_SKILL_PREFIX}make-plan, /audit-arch).\n"
            "Arbitrary prose prompts are not valid skill invocations."
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
