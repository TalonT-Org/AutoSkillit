#!/usr/bin/env python3
"""PostToolUse hook: auto-marks pipeline steps complete after run_skill.

Stdlib-only — runs under any Python interpreter without the autoskillit package.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

_SUFFIX_RE = __import__("re").compile(r"-\d+$")


def _atomic_write(path: Path, content: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_")
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        os.replace(tmp, path)
    except OSError:
        os.close(fd)
        os.unlink(tmp)
        raise


def _extract_run_skill_result(tool_response: str) -> dict:
    try:
        outer = json.loads(tool_response)
        if isinstance(outer, dict):
            result = outer.get("result", "")
            if isinstance(result, str):
                return json.loads(result)
            return outer if isinstance(result, dict) else {}
        return {}
    except (json.JSONDecodeError, ValueError, TypeError, OSError):
        return {}


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    step_name = tool_input.get("step_name", "")
    if not step_name:
        sys.exit(0)

    order_id = tool_input.get("order_id", "") or os.environ.get("AUTOSKILLIT_DISPATCH_ID", "")
    if not order_id:
        sys.exit(0)

    canonical = _SUFFIX_RE.sub("", step_name)
    tracker_path = Path.cwd() / ".autoskillit" / "temp" / "pipeline_tracker" / f"{order_id}.json"
    if not tracker_path.exists():
        sys.exit(0)

    tool_response = data.get("tool_response", "")
    inner = _extract_run_skill_result(tool_response)
    if not inner.get("success", False):
        sys.exit(0)

    lock_path = (
        Path.cwd() / ".autoskillit" / "temp" / "pipeline_tracker" / ".pipeline_tracker.lock"
    )
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
    except OSError:
        sys.exit(0)

    try:
        if not tracker_path.exists():
            sys.exit(0)
        tracker = json.loads(tracker_path.read_text())
        steps = tracker.get("steps", {})
        if canonical in steps:
            steps[canonical]["status"] = "complete"
            steps[canonical]["completed_at"] = datetime.now(UTC).isoformat()
        tracker["steps"] = steps
        _atomic_write(tracker_path, json.dumps(tracker))

        done = sum(1 for s in steps.values() if s.get("status") in ("complete", "skipped"))
        total = len(steps)
        pipeline_id = tracker.get("pipeline_id", order_id)

        result_summary = ""
        if inner:
            result_summary = json.dumps(inner)[:200]

        banner = (
            f"--- Pipeline Tracker ---\n"
            f"Step '{step_name}' complete ({pipeline_id}: {done}/{total} steps done)\n"
        )
        if result_summary:
            banner = f"{result_summary}\n{banner}"

        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "updatedMCPToolOutput": banner,
                    }
                }
            )
        )
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

    sys.exit(0)


if __name__ == "__main__":
    main()
