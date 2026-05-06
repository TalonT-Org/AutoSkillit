"""
PreToolUse hook — blocks run_cmd and Bash tool calls that would install an editable
package into system Python without an explicit --python .venv target.

This guards the interactive orchestrator path (skill sessions cannot call
run_cmd at all — they are blocked by skill_orchestration_guard.py).
"""

import json
import sys

UNSAFE_INSTALL_DENY_TRIGGER: str = "Blocked: editable install without --python .venv"

_UNSAFE_PATTERNS = (
    "pip install -e",
    "pip install --editable",
    "uv pip install -e",
    "uv pip install --editable",
    "maturin develop",
)

_SYSTEM_FLAG_PATTERN = "pip install"


def _is_unsafe_editable_install(cmd: str) -> bool:
    """Return True if cmd is an editable install not targeting a .venv Python."""
    cmd_lower = cmd.lower()
    if not any(p in cmd_lower for p in _UNSAFE_PATTERNS):
        return False
    # Allow if the command explicitly targets a .venv Python via --python.
    # Use token-level parsing to avoid substring false-positives (e.g. /tmp/.venv-poison/).
    tokens = cmd_lower.split()
    for i, token in enumerate(tokens):
        if token == "--python" and i + 1 < len(tokens):
            python_arg = tokens[i + 1]
            if python_arg.startswith(".venv") or "/.venv/" in python_arg:
                return False
    return True


def _is_system_install(cmd: str) -> bool:
    """Return True if cmd is a pip/uv install with --system flag."""
    cmd_lower = cmd.lower()
    if _SYSTEM_FLAG_PATTERN not in cmd_lower:
        return False
    tokens = cmd_lower.split()
    return "--system" in tokens


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
        tool_input = data.get("tool_input", {})
        cmd = tool_input.get("command", "") or tool_input.get("cmd", "")
    except (json.JSONDecodeError, AttributeError, OSError):
        sys.exit(0)

    if not isinstance(cmd, str) or not cmd:
        sys.exit(0)

    if _is_unsafe_editable_install(cmd):
        payload = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "Blocked: editable install without --python .venv. "
                        "Use `task install-worktree` or add `--python .venv/bin/python`. "
                        "Installing into system Python creates dangling entry points when "
                        "the worktree is deleted."
                    ),
                }
            }
        )
        sys.stdout.write(payload + "\n")
        sys.exit(0)

    if _is_system_install(cmd):
        payload = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "Blocked: --system install from worktree contaminates global environment. "
                        "Use `task install-worktree` or add `--python .venv/bin/python`."
                    ),
                }
            }
        )
        sys.stdout.write(payload + "\n")
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
