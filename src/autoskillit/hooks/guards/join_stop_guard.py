#!/usr/bin/env python3
"""Stop completion gate — block success/Stop until the active wave is complete.

When the payload-identified session binding reports ``join_required=true``, the
Stop event may only release Claude when the ledger shows a fully-complete wave.
Partial, failed, cancelled, interrupted, missing, or unresolved waves block Stop
with a deterministic reason so the existing AutoSkillit success/completion
marker cannot be emitted prematurely.

In a clean session (no join-bearing skill loaded) this guard is a no-op.

``Stop`` is the correct gate surface — per official documentation it
fires once per turn and exit code 2 prevents Claude from stopping while
continuing the conversation. This blocks premature completion between
waves as well as at the end of the whole conversation.

Unlike PreToolUse and PostToolUse guards, Stop fails closed for malformed input
or a missing session identity: a false release would lose the active wave.

Stdlib-only — no autoskillit imports.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import NoReturn

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)
_RUNTIME_DIR = str(Path(_HOOKS_DIR) / "_runtime")
if _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)


from _hook_payload import (  # type: ignore[import-not-found]  # noqa: E402
    normalize_payload_cwd,
    resolve_state_root,
)
from _hook_settings import (  # type: ignore[import-not-found]  # noqa: E402
    resolve_binding_session_id,
    session_join_admission,
    session_managed_codex_route,
    session_managed_scope,
    write_join_diagnostic,
)
from _join_ledger import (  # type: ignore[import-not-found]  # noqa: E402
    can_release_stop,
    resolve_flag_dir,
)
from _session_registry_bridge import (  # type: ignore[import-not-found]  # noqa: E402
    is_authenticated_top_level_cook,
)


def _block_stop(*, reason: str, denial_reason: str) -> NoReturn:
    write_join_diagnostic(
        {
            "gate": "join_stop_guard",
            "status": "block",
            "denial_reason": denial_reason,
        },
        caller="join_stop_guard",
    )
    sys.stdout.write(json.dumps({"decision": "block", "reason": reason}) + "\n")
    raise SystemExit(2)


def _read_stop_payload() -> tuple[dict[str, object], str]:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        _block_stop(
            reason="Stop payload is malformed; cannot verify required-join wave completion.",
            denial_reason="malformed_payload",
        )
    if not isinstance(data, dict):
        _block_stop(
            reason="Stop payload must be a JSON object to verify required-join wave completion.",
            denial_reason="non_object_payload",
        )

    sid = resolve_binding_session_id(data)
    if not sid:
        _block_stop(
            reason=(
                "Stop payload has no session_id; cannot verify required-join wave completion."
            ),
            denial_reason="missing_session_id",
        )
    return data, sid


def main() -> None:
    data, sid = _read_stop_payload()

    payload_cwd = normalize_payload_cwd(data.get("cwd"))
    if is_authenticated_top_level_cook(data, payload_cwd, sid):
        sys.exit(0)
    admission = session_join_admission(payload_cwd, sid)
    if not admission.enforce or admission.binding_dict is None:
        sys.exit(0)
    binding = admission.binding_dict

    managed_route = session_managed_codex_route(payload_cwd, sid)
    if managed_route is not None:
        route, guards, _config_digest = managed_route
        if route == "leaf":
            sys.exit(0)
        if "join_stop_guard" not in guards:
            _block_stop(
                reason="Managed Codex parent binding omits the Stop guard.",
                denial_reason="missing_managed_stop_guard",
            )

    scope = session_managed_scope(payload_cwd, sid)
    if scope is None:
        _block_stop(
            reason="Stop cannot verify the required-join binding scope.",
            denial_reason="invalid_managed_scope",
        )
    top_level_parent, _managed_leaf_id = scope
    flag_dir = resolve_flag_dir(resolve_state_root(payload_cwd))
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
