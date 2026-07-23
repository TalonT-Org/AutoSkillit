#!/usr/bin/env python3
"""PreToolUse input-rewrite hook for Codex native shell lossless capture.

Rewrites every native shell command on Codex into a minimal isolated-Python
runner invocation. The runner establishes the project/capture trust anchor,
executes the command, and emits only a bounded inline slice.

Codex-only (#4286 / ADR-0006).  Claude Code sessions are unaffected.
stdlib-only; no autoskillit imports.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import sys
from pathlib import Path
from uuid import uuid4

_HARNESS_SENTINEL = "# autoskillit-shell-capture v1"
_CAPTURE_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_MAX_COMMAND_BYTES = 64 * 1024
_RUNNER_PATH = Path(__file__).resolve().with_name("_capture_artifacts.py")


def _is_codex_session(payload: dict) -> bool:
    return os.environ.get("AUTOSKILLIT_AGENT_BACKEND") == "codex" or "turn_id" in payload


def _build_harness(command: str, cwd: str, capture_id: str) -> str:
    """Build a shell-safe isolated runner invocation without embedding ``command``."""

    if not _CAPTURE_ID_RE.fullmatch(capture_id):
        raise ValueError("capture_id must contain exactly 16 lowercase hex characters")
    try:
        command_bytes = command.encode("utf-8")
    except UnicodeEncodeError:
        command_bytes = b""
    if "\x00" in command or not command_bytes or len(command_bytes) > _MAX_COMMAND_BYTES:
        argv = [sys.executable, "-I", str(_RUNNER_PATH), "reject", capture_id]
    else:
        encoded = base64.b64encode(command_bytes).decode("ascii")
        argv = [
            sys.executable,
            "-I",
            str(_RUNNER_PATH),
            "run",
            encoded,
            cwd,
            capture_id,
        ]
    return f"{_HARNESS_SENTINEL}\n{shlex.join(argv)}"


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, AttributeError, OSError, TypeError):
        sys.exit(0)

    if not isinstance(data, dict):
        sys.exit(0)

    if not _is_codex_session(data):
        sys.exit(0)

    cwd = data.get("cwd", "")
    if not isinstance(cwd, str) or not cwd or not os.path.isabs(cwd):
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    command = tool_input.get("command", "")
    if not isinstance(command, str) or not command:
        sys.exit(0)

    harness = _build_harness(command, cwd, uuid4().hex[:16])
    payload = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": {"command": harness},
            }
        }
    )
    sys.stdout.write(payload + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
