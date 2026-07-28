#!/usr/bin/env python3
"""Cleanup-only SessionStart owner for retained shell captures."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _capture._authority import (  # type: ignore[import-not-found]  # noqa: E402
    CaptureSetupError,
    CaptureStoreAbsentError,
    open_capture_lifecycle,
)
from _capture_lifecycle import (  # type: ignore[import-not-found]  # noqa: E402
    CaptureLifecycleError,
)

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
        try:
            with open_capture_lifecycle(payload_cwd, create=False) as lifecycle:
                outcome = lifecycle.sweep(max_items=32, max_duration_seconds=0.05)
                if outcome.errors:
                    _bounded_stderr(
                        "[AutoSkillit capture lifecycle cleanup deferred after "
                        f"{outcome.errors} errors]\n"
                    )
        except CaptureStoreAbsentError:
            return 0
        except CaptureSetupError as exc:
            detail = " ".join(str(exc).split()).replace("]", "\\u005d")
            _bounded_stderr(f"[AutoSkillit capture lifecycle cleanup failed: {detail}]\n")
        except (CaptureLifecycleError, OSError) as exc:
            detail = " ".join(str(exc).split()).replace("]", "\\u005d")
            _bounded_stderr(f"[AutoSkillit capture lifecycle cleanup failed: {detail}]\n")
    except Exception as exc:
        detail = " ".join(str(exc).split()).replace("]", "\\u005d")
        _bounded_stderr(f"[AutoSkillit capture lifecycle hook failed: {detail}]\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
