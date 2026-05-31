#!/usr/bin/env python3
"""PreToolUse guard: advisory unmet-dependency warning for pipeline steps.

Non-blocking advisory — permissionDecision is always "allow". The server-side
_check_pipeline_deps in run_skill is the primary enforcer.

Stdlib-only — runs under any Python interpreter without the autoskillit package.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_SUFFIX_RE = re.compile(r"-\d+$")


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

    try:
        tracker = json.loads(tracker_path.read_text())
    except (json.JSONDecodeError, OSError):
        sys.exit(0)

    deps = tracker.get("dependencies", {}).get(canonical, [])
    if not deps:
        sys.exit(0)

    steps = tracker.get("steps", {})
    unmet = [d for d in deps if steps.get(d, {}).get("status") not in ("complete", "skipped")]
    if not unmet:
        sys.exit(0)

    pipeline_id = tracker.get("pipeline_id", order_id)
    msg = (
        f"Pipeline '{pipeline_id}': step '{step_name}' depends on {unmet} "
        f"which have not completed. The server will block this call — "
        f"run the missing step(s) first."
    )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "additionalContext": msg,
                }
            }
        )
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
