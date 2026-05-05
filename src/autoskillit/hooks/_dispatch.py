"""Stable hook dispatcher — NEVER RENAME THIS FILE.

This file is the sole hook command target for all Claude Code hook entries.
Its path stability is a contract: renaming it would break in-flight sessions.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Stdin is one-shot from Claude Code — must buffer before subprocess.
_HOOKS_DIR = Path(__file__).parent

_RETIRED_MAPPING: dict[str, str] = {
    "guards/leaf_orchestration_guard": "guards/skill_orchestration_guard",
    "guards/franchise_dispatch_guard": "guards/fleet_dispatch_guard",
    "guards/headless_orchestration_guard": "guards/skill_orchestration_guard",
}


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: _dispatch.py <logical_hook_name>", file=sys.stderr)
        sys.exit(1)

    logical_name = sys.argv[1]
    target = _HOOKS_DIR / (logical_name + ".py")

    if not target.is_file():
        resolved = _RETIRED_MAPPING.get(logical_name)
        if resolved:
            target = _HOOKS_DIR / (resolved + ".py")
        else:
            print(
                f"[autoskillit dispatch] unknown hook: {logical_name} — degrading gracefully",
                file=sys.stderr,
            )
            sys.exit(0)

    if not target.is_file():
        print(
            f"[autoskillit dispatch] retired target missing: {target} — degrading gracefully",
            file=sys.stderr,
        )
        sys.exit(0)

    stdin_data = sys.stdin.buffer.read()

    try:
        result = subprocess.run(
            [sys.executable, str(target)],
            input=stdin_data,
            capture_output=False,
        )
    except OSError as exc:
        print(
            f"[autoskillit dispatch] exec failed for {target}: {exc} — degrading gracefully",
            file=sys.stderr,
        )
        sys.exit(0)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
