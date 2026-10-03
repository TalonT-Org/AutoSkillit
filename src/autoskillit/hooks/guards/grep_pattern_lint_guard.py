#!/usr/bin/env python3
"""PreToolUse hook — denies Grep calls with BRE \\| alternation syntax.

The Grep tool wraps ripgrep which uses ERE/PCRE syntax where `|` is
alternation. Models trained on Bash data emit `\\|` (POSIX BRE), which
ripgrep treats as a literal backslash-pipe, silently returning 0 results.

This guard intercepts the pattern before ripgrep sees it, denies the call,
and surfaces the corrected pattern so the model can retry immediately.

Stdlib-only — runs under any Python interpreter without the autoskillit package.
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

# Sentinel: backslash followed by pipe — BRE alternation, invalid in ripgrep ERE
_BRE_ALTERNATION = "\\|"


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)  # fail-open on malformed input

    tool_name = data.get("tool_name", "")
    if tool_name != "Grep":
        sys.exit(0)  # guard is Grep-specific

    pattern = (data.get("tool_input") or {}).get("pattern", None)
    if not isinstance(pattern, str) or _BRE_ALTERNATION not in pattern:
        sys.exit(0)  # no BRE alternation present — allow

    corrected = pattern.replace(_BRE_ALTERNATION, "|")
    deny_tool_use(
        "The Grep tool uses ripgrep (ERE/PCRE) syntax where `|` is alternation. "
        "`\\|` is not alternation in ripgrep — it matches a literal backslash-pipe "
        "character, which is extremely rare in source code. "
        f"Corrected pattern: `{corrected}` "
        "(If you genuinely need to match a literal backslash-pipe, use `\\\\|`.)"
    )


if __name__ == "__main__":
    main()
