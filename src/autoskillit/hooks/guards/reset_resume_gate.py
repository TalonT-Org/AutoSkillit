"""PreToolUse guard: require resume attempt before reset_dispatch."""

from __future__ import annotations

import json
import sys
from pathlib import Path

RESET_RESUME_DENY_TRIGGER: str = "RESUME ATTEMPT REQUIRED"

_STATE_FILE_RELPATH = (".autoskillit", "temp", "resume_gate_state.json")

_DENY_REASON = (
    f"{RESET_RESUME_DENY_TRIGGER}: reset_dispatch was called without a prior resume "
    "attempt on this dispatch. You must attempt to resume the failed session first "
    "(pass resume_session_id and prior_dispatch_id to dispatch_food_truck). "
    "If resume is impossible, call reset_dispatch with force=true."
)


def _iter_campaign_state_files(project_root: Path):
    """Yield parsed campaign state dicts from the dispatches directory.

    Campaign state files live at .autoskillit/temp/dispatches/*.json (flat glob).
    """
    dispatches_dir = project_root / ".autoskillit" / "temp" / "dispatches"
    if not dispatches_dir.is_dir():
        return
    try:
        for state_file in dispatches_dir.iterdir():
            if state_file.suffix != ".json":
                continue
            try:
                yield json.loads(state_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
    except OSError:
        pass


def _resolve_dispatch_uuid(dispatch_id: str, project_root: Path) -> str | None:
    """Resolve a dispatch name to its canonical UUID by scanning campaign state files.

    Returns the UUID if found, or None if not resolvable. Fail-open on all errors.
    """
    try:
        for state in _iter_campaign_state_files(project_root):
            for d in state.get("dispatches", []):
                if d.get("name") == dispatch_id and d.get("dispatch_id"):
                    return d["dispatch_id"]
    except Exception:
        pass
    return None


def _is_refused(dispatch_id: str, project_root: Path) -> bool:
    """Check if a dispatch is in REFUSED status by scanning campaign state files."""
    try:
        for state in _iter_campaign_state_files(project_root):
            for d in state.get("dispatches", []):
                if d.get("dispatch_id") == dispatch_id or d.get("name") == dispatch_id:
                    return d.get("status") == "REFUSED"
    except Exception:
        pass
    return False


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    tool_name: str = data.get("tool_name", "")
    if "reset_dispatch" not in tool_name:
        sys.exit(0)

    tool_input: dict = data.get("tool_input", {}) or {}

    if tool_input.get("force"):
        sys.exit(0)

    dispatch_id = tool_input.get("dispatch_id", "")
    if not dispatch_id:
        sys.exit(0)

    project_root = Path.cwd()

    if _is_refused(dispatch_id, project_root):
        sys.exit(0)

    state_file = project_root.joinpath(*_STATE_FILE_RELPATH)
    if not state_file.is_file():
        sys.exit(0)

    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        sys.exit(0)

    resume_attempted = state.get("resume_attempted", {})

    if resume_attempted.get(dispatch_id):
        sys.exit(0)

    resolved_uuid = _resolve_dispatch_uuid(dispatch_id, project_root)
    if resolved_uuid and resume_attempted.get(resolved_uuid):
        sys.exit(0)

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": _DENY_REASON,
                }
            }
        )
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
