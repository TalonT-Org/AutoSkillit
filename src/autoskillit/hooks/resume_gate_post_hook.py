"""PostToolUse hook: record resume attempts for the reset_dispatch resume gate."""

from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
from pathlib import Path

_STATE_FILE_RELPATH = (".autoskillit", "temp", "resume_gate_state.json")


def _read_modify_write(state_file: Path, dispatch_id: str) -> None:
    """Atomically read-modify-write the resume gate state under flock."""
    state_file.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_file.with_suffix(".lock")
    with open(lock_path, "wb") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        existing: dict = {}
        if state_file.is_file():
            try:
                existing = json.loads(state_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        ra = existing.setdefault("resume_attempted", {})
        ra[dispatch_id] = True
        fd, tmp = tempfile.mkstemp(dir=state_file.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(existing))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, state_file)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    tool_name: str = data.get("tool_name", "")
    tool_input: dict = data.get("tool_input", {}) or {}

    if "dispatch_food_truck" not in tool_name:
        sys.exit(0)

    resume_session_id = tool_input.get("resume_session_id")
    prior_dispatch_id = tool_input.get("prior_dispatch_id")

    if not resume_session_id or not prior_dispatch_id:
        sys.exit(0)

    state_file = Path.cwd().joinpath(*_STATE_FILE_RELPATH)
    try:
        _read_modify_write(state_file, prior_dispatch_id)
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
