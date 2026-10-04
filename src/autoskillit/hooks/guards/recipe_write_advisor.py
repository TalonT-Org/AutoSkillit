#!/usr/bin/env python3
"""PreToolUse hook — suggests the appropriate skill when writing recipe YAML files.

Non-blocking advisory: emits PreToolUse context, never a permission decision.
Skips headless sessions (AUTOSKILLIT_HEADLESS=1) to avoid noise in automated runs.

Stdlib-only — runs under any Python interpreter without the autoskillit package.
Patterns are inlined from SKILL_FILE_ADVISORY_MAP in core.types.constants._type_constants; the
contract test test_hook_patterns_match_type_constants asserts they stay in sync.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)
_RUNTIME_DIR = str(Path(_HOOKS_DIR) / "_runtime")
if _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)


from _hook_output import add_context  # noqa: E402
from _hook_payload import edit_target_paths  # noqa: E402
from _hook_settings import enforce_session_scope  # noqa: E402

# Inlined subset of SKILL_FILE_ADVISORY_MAP (recipe-related entries only).
# Must stay in sync with core.types.constants._type_constants.SKILL_FILE_ADVISORY_MAP.
# test_hook_patterns_match_type_constants enforces this.
_ADVISORY_PATTERNS: list[tuple[str, str]] = [
    (r"(?:\.autoskillit|src/autoskillit)/recipes/campaigns/.*\.ya?ml$", "make-campaign"),
    (r"(?:\.autoskillit|src/autoskillit)/recipes/.*\.ya?ml$", "write-recipe"),
]

_COMPILED: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pat), skill) for pat, skill in _ADVISORY_PATTERNS
]


def main() -> None:
    enforce_session_scope("interactive_only")

    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)

    for file_path in edit_target_paths(data):
        normalized = file_path.replace(os.sep, "/")
        for pattern, skill_name in _COMPILED:
            if pattern.search(normalized):
                add_context(
                    "PreToolUse",
                    f"Consider using /{skill_name} for this file. "
                    "It provides schema validation, worked examples, and "
                    "prevents common recipe errors.",
                )

    sys.exit(0)


if __name__ == "__main__":
    main()
