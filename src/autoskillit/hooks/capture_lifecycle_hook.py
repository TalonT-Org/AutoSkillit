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
    SESSION_START_BUDGET,
    cleanup_diagnostic,
    reconcile_capture_store,
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
_MAX_DIAGNOSTIC_BYTES = 512


def _bounded_stderr(message: str) -> None:
    try:
        bounded = message.encode("utf-8")[:_MAX_DIAGNOSTIC_BYTES].decode(
            "utf-8",
            errors="ignore",
        )
        sys.stderr.write(bounded)
    except (OSError, RuntimeError, UnicodeError):
        pass


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
        detail = cleanup_diagnostic(outcome, owner="session_start")
        if detail is not None:
            _bounded_stderr(f"[AutoSkillit capture lifecycle cleanup deferred: {detail}]\n")
    except Exception:
        _bounded_stderr("[AutoSkillit capture lifecycle hook failed]\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
