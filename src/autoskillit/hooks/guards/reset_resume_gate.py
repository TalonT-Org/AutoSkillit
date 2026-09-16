"""PreToolUse guard: require resume attempt before reset_dispatch."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)
_RUNTIME_DIR = str(Path(_HOOKS_DIR) / "_runtime")
if _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)


from _hook_payload import (  # type: ignore[import-not-found]  # noqa: E402
    parse_hook_command,
    resolve_state_root,
)

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


def _reset_dispatch_id(data: dict) -> str | None:
    """Return an actionable non-forced reset dispatch ID, if any."""
    tool_name: str = data.get("tool_name", "")
    if "reset_dispatch" not in tool_name:
        return None

    tool_input: dict = data.get("tool_input", {}) or {}
    if not isinstance(tool_input, dict) or tool_input.get("force"):
        return None

    return tool_input.get("dispatch_id", "") or None


def _reset_is_allowed(dispatch_id: str, project_root: Path) -> bool:
    """Return whether reset has a prior resume attempt or a REFUSED exemption."""
    if _is_refused(dispatch_id, project_root):
        return True

    state_file = project_root.joinpath(*_STATE_FILE_RELPATH)
    if not state_file.is_file():
        return True

    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True

    resume_attempted = state.get("resume_attempted", {})
    if resume_attempted.get(dispatch_id):
        return True

    resolved_uuid = _resolve_dispatch_uuid(dispatch_id, project_root)
    return bool(resolved_uuid and resume_attempted.get(resolved_uuid))


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
        if not isinstance(data, dict):
            sys.exit(0)
    except Exception:
        sys.exit(0)

    dispatch_id = _reset_dispatch_id(data)
    if not dispatch_id:
        sys.exit(0)

    try:
        payload_cwd = parse_hook_command(data).payload_cwd
        project_root = resolve_state_root(payload_cwd)
    except Exception:
        sys.exit(0)

    if _reset_is_allowed(dispatch_id, project_root):
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
