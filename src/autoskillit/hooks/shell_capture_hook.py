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
import shlex
import struct
import sys
from pathlib import Path
from uuid import uuid4

_HOOKS_DIR = str(Path(__file__).resolve().parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _capture_contract import (  # type: ignore[import-not-found]  # noqa: E402
    _CAPTURE_ID_RE,
    _MAX_COMMAND_BYTES,
)

_HARNESS_SENTINEL = "# autoskillit-shell-capture v1"
_ARG_MAX_FALLBACK_BYTES = 128 * 1024
_ARG_MAX_HEADROOM_BYTES = 32 * 1024
_RUNNER_BASENAME = "_capture_artifacts.py"


def _is_codex_session(payload: dict) -> bool:
    return os.environ.get("AUTOSKILLIT_AGENT_BACKEND") == "codex" or "turn_id" in payload


def _system_arg_max() -> int:
    try:
        value = os.sysconf("SC_ARG_MAX")
    except (AttributeError, OSError, ValueError):
        return _ARG_MAX_FALLBACK_BYTES
    return value if isinstance(value, int) and value > 0 else _ARG_MAX_FALLBACK_BYTES


def _exec_footprint(argv: list[str]) -> int:
    argv_bytes = sum(len(os.fsencode(argument)) + 1 for argument in argv)
    environment_bytes = sum(
        len(os.fsencode(key)) + len(os.fsencode(value)) + 2 for key, value in os.environ.items()
    )
    pointer_bytes = (len(argv) + len(os.environ) + 2) * struct.calcsize("P")
    return argv_bytes + environment_bytes + pointer_bytes


def _fits_arg_max(argv: list[str]) -> bool:
    return _exec_footprint(argv) + _ARG_MAX_HEADROOM_BYTES <= _system_arg_max()


def _render_harness(argv: list[str], *, policy_command: str | None = None) -> str:
    lines = [_HARNESS_SENTINEL]
    if policy_command is not None:
        policy_text = json.dumps(policy_command, ensure_ascii=True)
        lines.append(f": {shlex.quote(policy_text)}")
    lines.append(shlex.join(argv))
    return "\n".join(lines)


def _runner_path() -> Path:
    return Path(__file__).resolve().with_name(_RUNNER_BASENAME)


def _build_harness(command: str, cwd: str, capture_id: str) -> str:
    """Build a shell-safe isolated runner invocation without embedding ``command``."""

    if not _CAPTURE_ID_RE.fullmatch(capture_id):
        raise ValueError("capture_id must contain exactly 16 lowercase hex characters")
    try:
        command_bytes = command.encode("utf-8")
    except UnicodeEncodeError:
        command_bytes = b""
    if "\x00" in command or not command_bytes or len(command_bytes) > _MAX_COMMAND_BYTES:
        argv = [sys.executable, "-I", str(_runner_path()), "reject", capture_id]
        harness = _render_harness(argv)
    else:
        encoded = base64.b64encode(command_bytes).decode("ascii")
        argv = [
            sys.executable,
            "-I",
            str(_runner_path()),
            "run",
            encoded,
            cwd,
            capture_id,
        ]
        harness = _render_harness(argv, policy_command=command)
        outer_argv = ["bash", "-c", harness]
        if not _fits_arg_max(argv) or not _fits_arg_max(outer_argv):
            argv = [sys.executable, "-I", str(_runner_path()), "reject", capture_id]
            harness = _render_harness(argv)
    return harness


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
