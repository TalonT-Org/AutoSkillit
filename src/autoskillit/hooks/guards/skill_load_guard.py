"""PreToolUse guard: deny native tools until Skill has been called.

Gates on three conditions (ALL must be true):
- ``AUTOSKILLIT_PROVIDER_PROFILE`` is non-empty and not ``anthropic`` (case-insensitive)
- ``AUTOSKILLIT_HEADLESS == "1"``
- ``AUTOSKILLIT_SESSION_TYPE == "skill"``

When gated, checks for ``.autoskillit/temp/skill_guard_{session_id}.flag``.
If absent, denies with a directive message instructing the model to call
the Skill tool first.

Stdlib-only — no autoskillit imports.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SKILL_LOAD_DENY_TRIGGER: str = "SKILL LOADING REQUIRED"

_DENY_MESSAGE: str = (
    "SKILL LOADING REQUIRED. You MUST call the Skill tool to load the skill "
    "instructions before using any other tools. Call ToolSearch with query "
    '"select:Skill" to load the Skill tool schema, then invoke Skill with the '
    "slash command name from your prompt. Do NOT use Read, Write, Edit, Bash, or "
    "any other tool until the skill is loaded. This is a MANDATORY step — skipping "
    "it will cause the session to fail."
)


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    profile = os.environ.get("AUTOSKILLIT_PROVIDER_PROFILE", "").strip()
    if not profile or profile.casefold() == "anthropic":
        sys.exit(0)

    if os.environ.get("AUTOSKILLIT_HEADLESS") != "1":
        sys.exit(0)

    if os.environ.get("AUTOSKILLIT_SESSION_TYPE") != "skill":
        sys.exit(0)

    session_id: str = data.get("session_id", "")
    if not session_id:
        sys.exit(0)

    flag_path = Path.cwd() / ".autoskillit" / "temp" / f"skill_guard_{session_id}.flag"
    if flag_path.exists():
        sys.exit(0)

    payload = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": _DENY_MESSAGE,
            }
        }
    )
    sys.stdout.write(payload + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
