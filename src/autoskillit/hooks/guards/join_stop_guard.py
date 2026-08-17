#!/usr/bin/env python3
"""Stop completion gate — block success/Stop until the active wave is complete.

When the session flag (or ``AUTOSKILLIT_JOIN_REQUIRED=1``) reports
``join_required=true``, the Stop event may only release Claude when the
ledger shows a fully-complete wave. Partial, failed, cancelled,
interrupted, missing, or unresolved waves block Stop with a
deterministic reason so the existing AutoSkillit success/completion marker
cannot be emitted prematurely.

In a clean session (no join-bearing skill loaded) this guard is a no-op.

``Stop`` is the correct gate surface — per official documentation it
fires once per turn and exit code 2 prevents Claude from stopping while
continuing the conversation. This blocks premature completion between
waves as well as at the end of the whole conversation.

Stdlib-only — no autoskillit imports.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _hook_settings import (  # type: ignore[import-not-found]  # noqa: E402
    write_join_diagnostic,
)
from _hook_utils import find_project_root  # type: ignore[import-not-found]  # noqa: E402
from _join_ledger import (  # type: ignore[import-not-found]  # noqa: E402
    can_release_stop,
)


def _session_binding() -> dict[str, object] | None:
    flag_path = os.environ.get("AUTOSKILLIT_JOIN_FLAG_PATH", "").strip()
    if not flag_path:
        return None
    try:
        with open(flag_path, encoding="utf-8") as handle:
            raw = handle.read()
    except OSError:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def main() -> None:
    try:
        sys.stdin.read()  # Stop hook payload is informational; we read & discard.
    except OSError:
        pass

    binding = _session_binding()
    if not binding or not binding.get("join_required"):
        sys.exit(0)

    sid = os.environ.get("AUTOSKILLIT_SESSION_ID", "").strip()
    if not sid:
        # Mirror the same env key the claim/settle guards read.
        sid = os.environ.get("AUTOSKILLIT_JOIN_SESSION_ID", "").strip()
    if not sid:
        # join_required=true is established; missing session_id is a
        # fail-closed condition — we cannot verify wave completion, so
        # block Stop rather than silently release.
        write_join_diagnostic(
            {
                "gate": "join_stop_guard",
                "status": "block",
                "denial_reason": "missing_session_id",
            },
            caller="join_stop_guard",
        )
        sys.stdout.write(
            json.dumps(
                {
                    "decision": "block",
                    "reason": (
                        "required-join session has no session_id; cannot verify "
                        "wave completion before Stop. Set AUTOSKILLIT_SESSION_ID "
                        "or AUTOSKILLIT_JOIN_SESSION_ID."
                    ),
                }
            )
            + "\n"
        )
        sys.exit(2)

    top_level_parent = os.environ.get("AUTOSKILLIT_JOIN_PARENT", "top_level").strip()
    flag_dir = find_project_root() / ".autoskillit" / "temp"
    allow_stop, reason = can_release_stop(
        flag_dir,
        session_id=sid,
        top_level_parent=top_level_parent,
        session_binding=binding,
    )
    write_join_diagnostic(
        {
            "gate": "join_stop_guard",
            "session_id": sid,
            "top_level_parent": top_level_parent,
            "status": "allow" if allow_stop else "block",
            "binding_valid": bool(binding.get("binding_valid", True)),
            "wave_outcome": reason,
        },
        caller="join_stop_guard",
    )
    if allow_stop:
        sys.exit(0)

    # Per Claude docs, exit code 2 prevents Claude from stopping and
    # continues the conversation. We use stdout to communicate the reason
    # to the harness.
    sys.stdout.write(
        json.dumps(
            {
                "decision": "block",
                "reason": reason,
            }
        )
        + "\n"
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
