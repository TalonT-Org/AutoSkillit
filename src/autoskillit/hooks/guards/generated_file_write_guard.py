#!/usr/bin/env python3
"""
PreToolUse hook — denies file-edit calls targeting generated files
(hooks.json and .claude/settings.json). These files are machine-local
generated artifacts managed by 'autoskillit install'. Direct edits bypass
hook_registry.py and create ghost entries that cause ENOENT fatal denials.
"""

import json
import os
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)
_RUNTIME_DIR = str(Path(_HOOKS_DIR) / "_runtime")
if _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)

from _hook_output import deny_tool_use  # noqa: E402
from _hook_payload import edit_target_paths  # noqa: E402

GENERATED_FILE_DENY_TRIGGER: str = "is a generated file"

_GENERATED_FILE_SUFFIXES = ("/hooks/hooks.json", ".claude/settings.json")
# Infix (not prefix) matching: guards receive absolute paths and cannot compute repo-relative.
_GENERATED_DIR_INFIXES = ("/recipes/contracts/",)


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)

    for file_path in edit_target_paths(data):
        # Normalize to forward slashes for cross-platform suffix matching
        normalized = file_path.replace(os.sep, "/")
        if not (
            any(normalized.endswith(suffix) for suffix in _GENERATED_FILE_SUFFIXES)
            or any(infix in normalized for infix in _GENERATED_DIR_INFIXES)
        ):
            continue

        deny_tool_use(
            f"'{file_path}' is a generated file with machine-local absolute paths. "
            f"Direct edits bypass hook_registry.py and create ghost hook entries "
            f"that block all tool calls with ENOENT. "
            f"Use 'autoskillit install' to regenerate, or "
            f"'autoskillit init' to sync settings.json."
        )


if __name__ == "__main__":
    main()
