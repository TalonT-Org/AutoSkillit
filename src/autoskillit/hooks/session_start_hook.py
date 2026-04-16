#!/usr/bin/env python3
"""SessionStart hook — injects /autoskillit:open-kitchen reminder on session resume.

Detects resume by checking whether the transcript file at transcript_path has content.
An empty or non-existent transcript means a fresh session; a non-empty transcript means
a resumed session that needs kitchen tools restored.

Stdlib-only — runs under any Python interpreter without the autoskillit package.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)  # fail-open on malformed input

    # Best-effort TTL sweep of stale kitchen markers. Fail-open — must not raise.
    _best_recipe_name: str | None = None
    _best_opened_at = None
    try:
        _state_override = os.environ.get("AUTOSKILLIT_STATE_DIR")
        if _state_override:
            _state_dir = Path(_state_override) / "kitchen_state"
        else:
            _state_dir = Path.cwd() / ".autoskillit" / "temp" / "kitchen_state"
        if _state_dir.is_dir():
            _ttl_hours = 24
            for _p in _state_dir.glob("*.json"):
                try:
                    _d = json.loads(_p.read_text(encoding="utf-8"))
                    _opened_at = datetime.fromisoformat(_d["opened_at"])
                    _age = datetime.now(UTC) - _opened_at
                    if _age.total_seconds() >= _ttl_hours * 3600:
                        _p.unlink()
                    else:
                        # Fresh — track most recent recipe name
                        _this_recipe = _d.get("recipe_name")
                        if _best_opened_at is None or _opened_at > _best_opened_at:
                            _best_opened_at = _opened_at
                            _best_recipe_name = (
                                _this_recipe if isinstance(_this_recipe, str) else None
                            )
                except Exception:
                    try:
                        _p.unlink()
                    except OSError:
                        pass
    except Exception:
        pass  # SessionStart hooks that raise break session start for the user

    transcript_path = data.get("transcript_path", "")
    if not transcript_path:
        sys.exit(0)

    try:
        size = Path(transcript_path).stat().st_size
    except OSError:
        sys.exit(0)  # fail-open if file is unreadable or missing

    if size == 0:
        sys.exit(0)  # fresh session — no reminder needed

    _base_msg = (
        "RESUME REMINDER: You are resuming a previous AutoSkillit session. "
        "MCP tool access (kitchen) is not automatically restored on resume. "
    )
    if _best_recipe_name:
        _detail = (
            f"You were running recipe '{_best_recipe_name}' — "
            f"call open_kitchen(name='{_best_recipe_name}') to regain access to all "
            "AutoSkillit MCP tools before continuing your work."
        )
    else:
        _detail = (
            "Call /autoskillit:open-kitchen first to regain access to all "
            "AutoSkillit MCP tools before continuing your work."
        )
    payload = json.dumps({"additionalContext": _base_msg + _detail})
    sys.stdout.write(payload + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
