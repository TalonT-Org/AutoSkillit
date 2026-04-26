#!/usr/bin/env python3
"""PreToolUse hook — blocks open_kitchen from headless sessions and writes kitchen marker.

Headless sessions launched by run_skill have AUTOSKILLIT_HEADLESS=1 in their
environment. This hook denies open_kitchen calls from those sessions, enforcing
that only humans (via /autoskillit:open-kitchen) can open the kitchen.

On the permit path (non-headless), writes a kitchen-open session marker so that
ask_user_question_guard can verify the kitchen is open before allowing AskUserQuestion.
"""

import json
import os
import sys
from datetime import UTC


def _write_kitchen_marker(session_id: str, recipe_name: str | None) -> None:
    """Write the kitchen-open session marker (stdlib-only, inline implementation)."""
    import tempfile
    from datetime import datetime
    from pathlib import Path as _Path

    state_override = os.environ.get("AUTOSKILLIT_STATE_DIR")
    if state_override:
        state_dir = _Path(state_override) / "kitchen_state"
    else:
        campaign_id = os.environ.get("AUTOSKILLIT_CAMPAIGN_ID", "")
        base = _Path.cwd() / ".autoskillit" / "temp" / "kitchen_state"
        state_dir = base / campaign_id if campaign_id else base
    state_dir.mkdir(parents=True, exist_ok=True)
    marker_path = state_dir / f"{session_id}.json"
    payload = json.dumps(
        {
            "session_id": session_id,
            "opened_at": datetime.now(UTC).isoformat(),
            "recipe_name": recipe_name,
            "marker_version": 1,
            "content_hash": "",
            "composite_hash": "",
        }
    )
    fd, tmp = tempfile.mkstemp(dir=state_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, marker_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _bridge_session_registry(session_id: str) -> None:
    """Bridge AUTOSKILLIT_LAUNCH_ID to claude_session_id in the session registry."""
    import tempfile
    from pathlib import Path as _Path

    launch_id = os.environ.get("AUTOSKILLIT_LAUNCH_ID", "")
    if not launch_id or not session_id:
        return

    registry_file = _Path.cwd() / ".autoskillit" / "temp" / "session_registry.json"
    try:
        registry: dict = json.loads(registry_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    if launch_id not in registry:
        return

    registry[launch_id]["claude_session_id"] = session_id

    registry_file.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(registry)
    fd, tmp = tempfile.mkstemp(dir=registry_file.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, registry_file)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)  # fail-open on malformed input or broken pipe

    if os.environ.get("AUTOSKILLIT_HEADLESS") == "1":
        session_type = os.environ.get("AUTOSKILLIT_SESSION_TYPE", "").lower()

        if session_type in ("fleet",):
            payload = json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"open_kitchen cannot be called from {session_type!r} sessions."
                        ),
                    }
                }
            )
            sys.stdout.write(payload + "\n")
            sys.exit(0)

        if session_type not in ("orchestrator",):
            # leaf, unset, or invalid — deny (fail-closed)
            payload = json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            "open_kitchen cannot be called from leaf sessions. "
                            "Open the kitchen in your orchestrator session using "
                            "/autoskillit:open-kitchen."
                        ),
                    }
                }
            )
            sys.stdout.write(payload + "\n")
            sys.exit(0)

        # HEADLESS + orchestrator — fall through to permit path

    # Permit path: write a kitchen-open session marker so ask_user_question_guard
    # can verify the kitchen is open before allowing AskUserQuestion.
    # The marker is written here (from the PreToolUse hook) rather than from the
    # MCP server tool because the hook receives the Claude Code session_id on stdin;
    # the FastMCP Context does not expose it.
    try:
        session_id = data.get("session_id", "")
        recipe_name: str | None = None
        tool_input = data.get("tool_input") or {}
        if isinstance(tool_input, dict):
            recipe_name = tool_input.get("name") or None
        if session_id:
            _write_kitchen_marker(session_id, recipe_name)
            try:
                _bridge_session_registry(session_id)
            except Exception as _bridge_err:
                print(
                    f"[open_kitchen_guard] registry bridge failed: {_bridge_err}",
                    file=sys.stderr,
                )
    except Exception as e:
        print(f"[open_kitchen_guard] marker write failed: {e}", file=sys.stderr)
        # Surface the failure so the user knows AskUserQuestion will be blocked
        # in headless sub-sessions (ask_user_question_guard relies on the marker).
        payload = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "message": (
                        f"Warning: kitchen marker write failed ({e}). "
                        "AskUserQuestion may be blocked in headless sub-sessions."
                    ),
                }
            }
        )
        sys.stdout.write(payload + "\n")

    sys.exit(0)


if __name__ == "__main__":
    main()
