#!/usr/bin/env python3
"""PostToolUse hook: writes recipe-load-confirmed marker after first successful run_skill.

After the first pipeline step executes successfully, this hook writes a marker
file that open_kitchen_guard.py reads to block mid-run recipe reloads.
The marker is scoped by session_id so stale markers from crashed sessions
do not affect new sessions.

Stdlib-only — runs under any Python interpreter without the autoskillit package.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _hook_payload import (  # noqa: E402
    normalize_payload_cwd,
    resolve_kitchen_state_dir,
)


def _is_successful(tool_response: str) -> bool:
    """Check if the run_skill response indicates success (double-JSON unwrap)."""
    try:
        outer = json.loads(tool_response)
        if isinstance(outer, dict):
            result = outer.get("result", "")
            if isinstance(result, str):
                inner = json.loads(result)
            elif isinstance(result, dict):
                inner = result
            else:
                return False
            return bool(inner.get("success", False))
        return False
    except (json.JSONDecodeError, ValueError, TypeError):
        return False


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)

    session_id = data.get("session_id", "")
    if not session_id:
        sys.exit(0)

    state_dir = resolve_kitchen_state_dir(normalize_payload_cwd(data.get("cwd")))
    marker_path = state_dir / f"{session_id}_recipe_confirmed.json"

    if marker_path.exists():
        sys.exit(0)

    tool_response = data.get("tool_response", "")
    if not _is_successful(tool_response):
        sys.exit(0)

    state_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "session_id": session_id,
            "confirmed_at": datetime.now(UTC).isoformat(),
        }
    )
    fd, tmp = tempfile.mkstemp(dir=str(state_dir), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, str(marker_path))
    except Exception as e:
        sys.stderr.write(
            f"recipe_confirmed_post_hook: failed to write marker {marker_path}: {e}\n"
        )
        try:
            os.unlink(tmp)
        except OSError:
            pass

    sys.exit(0)


if __name__ == "__main__":
    main()
