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
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)
_RUNTIME_DIR = str(Path(_HOOKS_DIR) / "_runtime")
if _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)


from _hook_payload import (  # type: ignore[import-not-found]  # noqa: E402
    parse_hook_command,
    resolve_kitchen_state_dir,
)
from _hook_settings import hook_session_shape  # noqa: E402
from _session_registry_bridge import bridge_session_registry  # noqa: E402

OPEN_KITCHEN_DENY_TRIGGER: str = "open_kitchen cannot be called"


def _write_kitchen_marker(session_id: str, recipe_name: str | None, payload_cwd: str = "") -> None:
    """Write the kitchen-open session marker (stdlib-only, inline implementation)."""
    import tempfile
    from datetime import datetime

    state_dir = resolve_kitchen_state_dir(payload_cwd)
    state_dir.mkdir(parents=True, exist_ok=True)
    marker_path = state_dir / f"{session_id}.json"
    payload = json.dumps(
        {
            "session_id": session_id,
            "opened_at": datetime.now(UTC).isoformat(),
            "recipe_name": recipe_name,
            "marker_version": 1,
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


def _check_recipe_reload_block(
    session_id: str, tool_input: dict, payload_cwd: str = ""
) -> dict | None:
    """Block open_kitchen(name=...) if a recipe-confirmed marker exists for this session.

    Returns a deny payload dict if blocked, or None if allowed.
    """
    recipe_name = tool_input.get("name") or None
    if not recipe_name:
        return None

    if tool_input.get("ingredients_only", False):
        return None

    state_dir = resolve_kitchen_state_dir(payload_cwd)

    confirmed_path = state_dir / f"{session_id}_recipe_confirmed.json"
    if not confirmed_path.exists():
        return None

    try:
        marker = json.loads(confirmed_path.read_text(encoding="utf-8"))
        if marker.get("session_id") != session_id:
            return None
    except (json.JSONDecodeError, OSError):
        return None

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"RECIPE ALREADY LOADED: This session already loaded recipe "
                f"'{recipe_name}'. The recipe is instructions, not runtime state "
                "— re-loading cannot change pipeline behavior. If the user "
                "requests enabling or disabling a step mid-run, execute or skip "
                "it directly. To re-open the kitchen gate without loading a "
                "recipe, call open_kitchen() without a name parameter."
            ),
        }
    }


def _deny(reason: str) -> None:
    payload = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    )
    sys.stdout.write(payload + "\n")
    sys.exit(0)


def _enforce_session_authorization() -> None:
    headless, tier = hook_session_shape()
    if headless:
        if tier in ("fleet",):
            _deny(f"open_kitchen cannot be called from {tier!r} sessions.")

        if tier not in ("orchestrator",):
            # skill session, unset, or invalid — deny (fail-closed)
            _deny(
                "open_kitchen cannot be called from skill sessions. "
                "Open the kitchen in your orchestrator session using "
                "/autoskillit:open-kitchen."
            )

        # HEADLESS + orchestrator — fall through to permit path


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)  # fail-open on malformed input or broken pipe

    _enforce_session_authorization()

    # Permit path: write a kitchen-open session marker so ask_user_question_guard
    # can verify the kitchen is open before allowing AskUserQuestion.
    # The marker is written here (from the PreToolUse hook) rather than from the
    # MCP server tool because the hook receives the Claude Code session_id on stdin;
    # the FastMCP Context does not expose it.
    try:
        session_id = data.get("session_id", "")
        recipe_name: str | None = None
        tool_input = data.get("tool_input") or {}
        payload_cwd = parse_hook_command(data).payload_cwd
        if isinstance(tool_input, dict):
            recipe_name = tool_input.get("name") or None

        # Check for recipe reload block before writing the marker
        if session_id and isinstance(tool_input, dict):
            denial = _check_recipe_reload_block(session_id, tool_input, payload_cwd)
            if denial:
                sys.stdout.write(json.dumps(denial) + "\n")
                sys.exit(0)

        if session_id:
            _write_kitchen_marker(session_id, recipe_name, payload_cwd)
            try:
                bridge_session_registry(session_id, payload_cwd)
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
