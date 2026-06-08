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


def _get_state_dir() -> Path:
    """Resolve the kitchen_state directory (mirrors open_kitchen_guard.py)."""
    state_override = os.environ.get("AUTOSKILLIT_STATE_DIR")
    if state_override:
        return Path(state_override) / "kitchen_state"
    campaign_id = os.environ.get("AUTOSKILLIT_CAMPAIGN_ID", "")
    base = Path.cwd() / ".autoskillit" / "temp" / "kitchen_state"
    return base / campaign_id if campaign_id else base


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

    state_dir = _get_state_dir()
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
