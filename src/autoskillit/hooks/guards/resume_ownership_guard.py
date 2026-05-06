#!/usr/bin/env python3
"""PreToolUse guard: validate resume_session_id ownership."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _deny(reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )


def _resolve_provenance_path() -> Path:
    override = os.environ.get("AUTOSKILLIT_STATE_DIR", "")
    if override:
        return Path(override) / "session_provenance.jsonl"
    campaign = os.environ.get("AUTOSKILLIT_CAMPAIGN_ID", "")
    base = Path.cwd() / ".autoskillit" / "temp"
    if campaign:
        base = base / campaign
    return base / "session_provenance.jsonl"


def _find_provenance(session_id: str, prov_path: Path) -> dict | None:
    if not prov_path.is_file():
        return None
    try:
        for line in prov_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if record.get("session_id") == session_id:
                    return record
            except json.JSONDecodeError:
                continue
    except OSError:
        return None
    return None


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    if os.environ.get("AUTOSKILLIT_HEADLESS") != "1":
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    resume_session_id = tool_input.get("resume_session_id")

    if not resume_session_id:
        sys.exit(0)

    prov_path = _resolve_provenance_path()
    record = _find_provenance(resume_session_id, prov_path)

    if record is None:
        sys.exit(0)

    if not record.get("recipe_name"):
        _deny(
            f"resume_session_id '{resume_session_id}' is not a food truck "
            f"session (no recipe_name in provenance). Cannot resume an L3 "
            f"orchestrator session."
        )
        sys.exit(0)

    caller_session_id = data.get("session_id", "")
    record_caller = record.get("caller_session_id", "")
    record_kitchen = record.get("kitchen_id", "")
    current_kitchen = os.environ.get("AUTOSKILLIT_CAMPAIGN_ID", "")

    caller_matches = record_caller and record_caller == caller_session_id
    kitchen_matches = record_kitchen and record_kitchen == current_kitchen

    if not caller_matches and not kitchen_matches:
        _deny(
            f"resume_session_id '{resume_session_id}' is not owned by this "
            f"session. Owner: caller={record_caller}, kitchen={record_kitchen}. "
            f"Current: caller={caller_session_id}, kitchen={current_kitchen}."
        )
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
