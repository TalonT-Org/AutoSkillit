#!/usr/bin/env python3
"""PreToolUse hook — blocks unauthorized recipe/skill/agent file reads.

Defense-in-depth over the prompt-based prohibitions in `_backend_supplement()`.
If Codex auto-compaction destroys the recipe content loaded by `open_kitchen`,
the agent may attempt to re-read the recipe YAML, skill SKILL.md, or agent
definition files via `run_cmd`/`run_python`/`Bash`. This guard blocks those
recovery attempts at the tool-call boundary so the agent cannot self-recover
from a state that should be treated as a hard failure (the recipe is gone).
"""

import json
import os
import re
import sys
from importlib import import_module
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)
command_has_blocked_protected_path_read = import_module(
    "_command_classification"
).command_has_blocked_protected_path_read

RECIPE_READ_DENY_TRIGGER: str = "must not read recipe/skill/agent files directly"

_CMD_PATH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:\.autoskillit|src/autoskillit)/recipes/.*\.ya?ml"),
    re.compile(r"src/autoskillit/skills(?:_extended)?/.*/SKILL\.md"),
    re.compile(r"src/autoskillit/agents/.*\.md"),
]

_CALLABLE_PATTERN: re.Pattern[str] = re.compile(r"^autoskillit\.recipe\.(?!_cmd_rpc)")


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.stderr.write("recipe_read_guard: malformed stdin — failing open\n")
        sys.exit(0)

    if os.environ.get("AUTOSKILLIT_HEADLESS") != "1":
        sys.exit(0)

    tool_name: str = data.get("tool_name", "")
    tool_input: dict = data.get("tool_input", {})

    tool = tool_name.split("__")[-1]

    if tool == "run_cmd" or tool_name == "Bash":
        cmd_key = "command" if tool_name == "Bash" else "cmd"
        cmd: str = tool_input.get(cmd_key, "")
        if command_has_blocked_protected_path_read(cmd, _CMD_PATH_PATTERNS):
            _deny(
                f"run_cmd/Bash {RECIPE_READ_DENY_TRIGGER}. "
                "Use load_recipe to recall step definitions or the Skill tool "
                "for skill instructions."
            )

    if tool == "run_python":
        callable_name: str = tool_input.get("callable", "")
        if _CALLABLE_PATTERN.search(callable_name):
            _deny(
                f"run_python {RECIPE_READ_DENY_TRIGGER}. "
                "Use load_recipe to recall step definitions."
            )

    sys.exit(0)


def _deny(reason: str) -> None:
    payload = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    )
    sys.stdout.write(payload + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
