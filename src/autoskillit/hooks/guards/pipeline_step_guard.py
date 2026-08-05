#!/usr/bin/env python3
"""PreToolUse guard: advisory unmet-dependency warning for pipeline steps.

Non-blocking advisory — permissionDecision is always "allow". The server-side
_check_pipeline_deps in run_skill is the primary enforcer.

Tracker resolution uses the kitchen_id from the merged hook config (the same
rule the server uses) rather than the fragile single-file discovery heuristic.

Stdlib-only — runs under any Python interpreter without the autoskillit package.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _hook_payload import resolve_state_root  # type: ignore[import-not-found]  # noqa: E402
from _hook_settings import read_merged_hook_config  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import STEP_SUFFIX_RE  # type: ignore[import-not-found]  # noqa: E402


def _resolve_order_id_from_kitchen(tracker_dir: Path, kitchen_id: str) -> str:
    """Select tracker by internal kitchen_id field (same rule as the server).

    Skips the self-named file (``{kitchen_id}.json``) from the candidate scan,
    matching ``resolve_tracker_order_id`` in ``tools_pipeline_tracker.py``.
    When exactly one non-self candidate exists, returns that candidate's stem.
    When no non-self candidates exist, returns ``kitchen_id`` (the self-named
    file is the implicit default). Returns ``""`` on ambiguity (>1 candidate).
    """
    if not tracker_dir.is_dir():
        return ""
    active: set[str] = set()
    for f in tracker_dir.iterdir():
        if f.suffix != ".json" or f.name.startswith("."):
            continue
        if f.stem == kitchen_id:
            continue
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("kitchen_id") == kitchen_id:
            active.add(f.stem)
    if len(active) > 1:
        return ""
    if len(active) == 1:
        return next(iter(active))
    return kitchen_id


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    step_name = tool_input.get("step_name", "")
    if not step_name:
        sys.exit(0)

    raw_payload_cwd = data.get("cwd", "")
    payload_cwd = raw_payload_cwd if isinstance(raw_payload_cwd, str) else ""
    project_root = resolve_state_root(payload_cwd)

    order_id = tool_input.get("order_id", "") or os.environ.get("AUTOSKILLIT_DISPATCH_ID", "")
    if not order_id:
        tracker_dir = project_root / ".autoskillit" / "temp" / "pipeline_tracker"
        hook_config = read_merged_hook_config(root=project_root)
        kitchen_id = hook_config.get("kitchen_id", "")
        if kitchen_id:
            order_id = _resolve_order_id_from_kitchen(tracker_dir, kitchen_id)
        if not order_id:
            print(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "allow",
                            "additionalContext": (
                                "Pipeline step guard: cannot resolve tracker — "
                                "no order_id, no kitchen_id, or ambiguous tracker state. "
                                "The server-side enforcer will handle dependency checks."
                            ),
                        }
                    }
                )
            )
            sys.exit(0)

    canonical = STEP_SUFFIX_RE.sub("", step_name)
    tracker_path = project_root / ".autoskillit" / "temp" / "pipeline_tracker" / f"{order_id}.json"
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
