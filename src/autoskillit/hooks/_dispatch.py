"""Stable hook dispatcher — NEVER RENAME THIS FILE.

This file is the sole hook command target for all Claude Code hook entries.
Its path stability is a contract: renaming it would break in-flight sessions.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Stdin is one-shot from Claude Code — must buffer before subprocess.
_HOOKS_DIR = Path(__file__).parent
_HOOKS_DIR_TEXT = str(_HOOKS_DIR.resolve())
if _HOOKS_DIR_TEXT not in sys.path:
    sys.path.insert(0, _HOOKS_DIR_TEXT)

from _hook_settings import (  # type: ignore[import-not-found]  # noqa: E402
    write_dispatch_diagnostic,
)

_RETIRED_MAPPING: dict[str, str] = {
    "guards/leaf_orchestration_guard": "guards/skill_orchestration_guard",
    "guards/franchise_dispatch_guard": "guards/fleet_dispatch_guard",
    "guards/headless_orchestration_guard": "guards/skill_orchestration_guard",
    "guards/mcp_health_guard": "guards/mcp_health_advisor",
}


def _degrade(
    *,
    event_kind: str,
    logical_name: str,
    reason: str,
    message: str,
) -> None:
    try:
        write_dispatch_diagnostic(event_kind, logical_name, reason)
    except Exception:
        pass
    print(message, file=sys.stderr)
    raise SystemExit(0)


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
            _degrade(
                event_kind="unknown_target",
                logical_name=logical_name,
                reason=f"unknown hook: {logical_name}",
                message=(
                    f"[autoskillit dispatch] unknown hook: {logical_name} — degrading gracefully"
                ),
            )

    if not target.is_file():
        _degrade(
            event_kind="retired_target_missing",
            logical_name=logical_name,
            reason=f"retired target missing: {target}",
            message=(
                f"[autoskillit dispatch] retired target missing: {target} — degrading gracefully"
            ),
        )

    stdin_data = sys.stdin.buffer.read()

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    try:
        result = subprocess.run(
            [sys.executable, "-B", str(target)],
            input=stdin_data,
            capture_output=False,
            env=env,
        )
    except OSError as exc:
        _degrade(
            event_kind="exec_failure",
            logical_name=logical_name,
            reason=f"exec failed for {target}: {exc}",
            message=(
                f"[autoskillit dispatch] exec failed for {target}: {exc} — degrading gracefully"
            ),
        )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
