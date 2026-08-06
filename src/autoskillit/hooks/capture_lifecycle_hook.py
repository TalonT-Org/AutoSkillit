#!/usr/bin/env python3
"""Cleanup-only SessionStart owner for retained shell captures."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import ModuleType

_HOOKS_DIR = str(Path(__file__).resolve().parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _capture._reconcile import (  # type: ignore[import-not-found]  # noqa: E402
    DIAGNOSTIC_MAX_BYTES,
    SESSION_START_BUDGET,
    emit_bounded_diagnostic,
    emit_owner_diagnostic,
    reconcile_capture_store,
)
from _policy_event import (  # type: ignore[import-not-found]  # noqa: E402
    PolicyEvent,
    render_provenance_prefix,
)

_capture_package = sys.modules["_capture"]
_hooks_package: ModuleType | None
try:
    _hooks_package = sys.modules["autoskillit.hooks"]
except KeyError:
    _hooks_package = None
if _hooks_package is not None:
    try:
        _package_binding = sys.modules["autoskillit.hooks._capture"]
    except KeyError:
        _package_binding = _capture_package
        sys.modules["autoskillit.hooks._capture"] = _package_binding
    setattr(_hooks_package, "_capture", _package_binding)

_MAX_INPUT_BYTES = 64 * 1024
_OWNER = "session_start"


def _emit_crash_diagnostic() -> None:
    event = PolicyEvent(
        hook_id="capture_lifecycle_hook",
        hook_version=1,
        event="capture_cleanup",
        decision="failed",
        reason_code="capture lifecycle hook raised an unexpected exception",
    )
    emit_bounded_diagnostic(
        render_provenance_prefix(event),
        maximum_bytes=DIAGNOSTIC_MAX_BYTES,
        write=sys.stderr.write,
    )


def main() -> int:
    try:
        raw_bytes = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
        if len(raw_bytes) > _MAX_INPUT_BYTES:
            return 0
        raw = raw_bytes.decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return 0
        payload_cwd = payload.get("cwd")
        if (
            not isinstance(payload_cwd, str)
            or not payload_cwd
            or not os.path.isabs(payload_cwd)
            or "\x00" in payload_cwd
        ):
            return 0
        outcome = reconcile_capture_store(payload_cwd, SESSION_START_BUDGET)
        emit_owner_diagnostic(outcome, owner=_OWNER, write=sys.stderr.write)
    except Exception:
        _emit_crash_diagnostic()
    return 0


if __name__ == "__main__":
    sys.exit(main())
