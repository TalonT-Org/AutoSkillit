"""PreToolUse hook: blocks Write/Edit/Bash/apply_patch outside
the allowed prefix in write-scoped sessions."""

from __future__ import annotations

import json
import os
import re
import sys

WRITE_GUARD_DENY_TRIGGER = "read-only skill session"

# Patterns that detect file-modifying commands in a bash command string.
# Used to decide whether a Bash tool call warrants path extraction.
_IS_WRITE_CMD_RE = re.compile(
    r"\bsed\s+(?:\S+\s+)*(?:-i|--in-place)"
    r"|>+\s*/"
    r"|\btee\s+/"
    r"|\b(?:mv|cp)\s+"
    r"|\bpatch\s+"
    r"|\bgit\s+checkout\s+--"
    r"|\bgit\s+reset\s+--hard"
    r"|\b(?:rm|unlink)\s+"
)

# Each pattern extracts the target file path (group 1) from a file-modifying command.
_BASH_TARGET_PATTERNS: list[re.Pattern[str]] = [
    # sed -i 's/x/y/' /file  or  sed --in-place 's/x/y/' /file
    re.compile(
        r"\bsed\s+(?:\S+\s+)*(?:-i\S*|--in-place\S*)\s+"
        r"(?:'[^']*'|\"[^\"]*\"|\S+)\s+(/[^\s;|&]+)"
    ),
    # anything > /file  or  >> /file  (redirect to absolute path)
    re.compile(r">+\s*(/[^\s;|&>]+)"),
    # tee /file
    re.compile(r"\btee\s+(/[^\s;|&>]+)"),
    # mv /src /dst  or  cp /src /dst  (captures destination)
    re.compile(r"\b(?:mv|cp)\s+\S+\s+(/[^\s;|&>]+)"),
    # patch /file
    re.compile(r"\bpatch\s+(?:-\S+\s+)*(/[^\s;|&><]+)"),
    # git checkout -- /file
    re.compile(r"\bgit\s+checkout\s+--\s+(/[^\s;|&>]+)"),
    # rm /file  or  unlink /file
    re.compile(r"\b(?:rm|unlink)\s+(?:-\S+\s+)*(/[^\s;|&>]+)"),
]


def _extract_bash_write_targets(command: str) -> list[str] | None:
    """Return absolute target paths from a bash command, or None if no write command found.

    Returns an empty list when a write command is detected but no path can be reliably
    extracted — callers treat this as fail-open (ambiguous = allow).
    """
    if not _IS_WRITE_CMD_RE.search(command):
        return None
    targets: list[str] = []
    for pattern in _BASH_TARGET_PATTERNS:
        m = pattern.search(command)
        if m:
            targets.append(m.group(1))
    return targets


def _extract_paths_from_patch(command: str) -> list[str]:
    """Extract target file paths from a unified diff patch ('+++ b/' lines)."""
    if not command:
        return []
    paths: list[str] = []
    for line in command.split("\n"):
        if line.startswith("+++ b/"):
            paths.append(line[6:])
    return paths


def _deny(reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    sys.exit(0)


def main() -> None:
    if not os.environ.get("AUTOSKILLIT_HEADLESS"):
        sys.exit(0)

    allowed_prefix = os.environ.get("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "")
    if not allowed_prefix:
        sys.exit(0)

    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        _deny(
            f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER} (malformed hook input)."
        )
        return

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Write", "Edit", "Bash", "apply_patch") and "run_cmd" not in tool_name:
        sys.exit(0)

    tool_input = data.get("tool_input", {})

    real_prefix = os.path.realpath(allowed_prefix)
    norm_prefix = real_prefix.rstrip("/") + "/"

    def _within_prefix(path: str) -> bool:
        resolved = os.path.realpath(path)
        return resolved.startswith(norm_prefix) or resolved == norm_prefix.rstrip("/")

    if tool_name == "Bash" or "run_cmd" in tool_name:
        command = tool_input.get("command", "") or tool_input.get("cmd", "")
        targets = _extract_bash_write_targets(command)
        if targets is None:
            sys.exit(0)
        if not targets:
            # Write command detected but path not extractable — fail-open.
            sys.exit(0)
        for target in targets:
            if not _within_prefix(target):
                _deny(
                    f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER}. "
                    f"Only writes to {allowed_prefix} are permitted."
                )
                return
        sys.exit(0)

    if tool_name == "apply_patch":
        command = tool_input.get("command", "")
        paths = _extract_paths_from_patch(command)
        if not paths:
            _deny(
                f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER} "
                f"(no target paths found in patch)."
            )
            return
        for p in paths:
            if not _within_prefix(p):
                _deny(
                    f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER}. "
                    f"Only writes to {allowed_prefix} are permitted."
                )
                return
        sys.exit(0)

    # Write or Edit
    file_path = tool_input.get("file_path", "")
    if not file_path:
        _deny(f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER} (no file_path).")
        return

    if _within_prefix(file_path):
        sys.exit(0)

    _deny(
        f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER}. "
        f"Only writes to {allowed_prefix} are permitted."
    )


if __name__ == "__main__":
    main()
