"""PostToolUse hook: record resume attempts for the reset_dispatch resume gate."""

from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
import time
from pathlib import Path

_STATE_FILE_RELPATH = (".autoskillit", "temp", "resume_gate_state.json")
_LOCK_ACQUIRE_TIMEOUT_SECONDS = 0.25
_LOCK_RETRY_INTERVAL_SECONDS = 0.01


def _acquire_lock(fd: int) -> None:
    """Acquire the resume-gate lock without waiting indefinitely."""
    deadline = time.monotonic() + _LOCK_ACQUIRE_TIMEOUT_SECONDS
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(_LOCK_RETRY_INTERVAL_SECONDS, remaining))
        else:
            return


def _read_modify_write(state_file: Path, dispatch_id: str) -> None:
    """Atomically read-modify-write the resume gate state under flock."""
    state_file.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_file.with_suffix(".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o644)
    locked = False
    try:
        _acquire_lock(fd)
        locked = True
        existing: dict = {}
        if state_file.is_file():
            try:
                existing = json.loads(state_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        ra = existing.setdefault("resume_attempted", {})
        ra[dispatch_id] = True
        tmp_fd, tmp = tempfile.mkstemp(dir=state_file.parent, suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
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
    finally:
        try:
            if locked:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            os.close(fd)


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
