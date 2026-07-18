#!/usr/bin/env python3
"""PreToolUse input-rewrite hook for Codex native shell lossless capture.

Rewrites every native shell command on Codex into a capture harness: the
original command runs unmodified in a subshell, its complete combined output
goes to a project artifact, and only a bounded inline slice enters context.

Codex-only (#4286 / ADR-0006).  Claude Code sessions are unaffected.
stdlib-only; no autoskillit imports.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path
from uuid import uuid4

_HOOKS_DIR = str(Path(__file__).resolve().parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _hook_settings import read_merged_hook_config  # type: ignore[import-not-found]  # noqa: E402
from _policy_event import (  # type: ignore[import-not-found]  # noqa: E402
    PolicyEvent,
    render_capture_marker,
)

_HARNESS_SENTINEL = "# autoskillit-shell-capture v1"
_DEFAULT_SHELL_MAX_INLINE_BYTES = 12_000
_CAPTURE_SUBDIR = ".autoskillit/temp/shell_capture"


def _is_codex_session(payload: dict) -> bool:
    return os.environ.get("AUTOSKILLIT_AGENT_BACKEND") == "codex" or "turn_id" in payload


def _positive_int(value: object, default: int) -> int:
    return (
        value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default
    )


def _read_policy(cwd: str) -> tuple[bool, int]:
    try:
        config = read_merged_hook_config(root=Path(cwd))
        section = config.get("output_budget_policy", {})
        if not isinstance(section, dict):
            section = {}
    except (OSError, AttributeError, TypeError, json.JSONDecodeError):
        section = {}
    return (
        section.get("disabled") is True,
        _positive_int(section.get("shell_max_inline_bytes"), _DEFAULT_SHELL_MAX_INLINE_BYTES),
    )


def _build_harness(command: str, cwd: str, inline_bytes: int) -> str:
    uid = uuid4().hex[:8]
    capture_dir = str(Path(cwd) / _CAPTURE_SUBDIR)
    capture_file = str(Path(cwd) / _CAPTURE_SUBDIR / f"shell_{uid}.log")
    head = (2 * inline_bytes) // 3
    tail = inline_bytes - head

    marker_event = PolicyEvent(
        hook_id="shell_capture_hook",
        hook_version=1,
        event="PreToolUse",
        decision="input rewrite",
        reason_code="SHELL_OUTPUT_CAPTURED",
    )
    marker_prefix = render_capture_marker(marker_event)

    fail_event = PolicyEvent(
        hook_id="shell_capture_hook",
        hook_version=1,
        event="PreToolUse",
        decision="deny",
        reason_code="CAPTURE_FAILED",
    )
    fail_msg = render_capture_marker(fail_event) + " cannot create capture directory]"

    if not command.endswith("\n"):
        command += "\n"

    q_dir = shlex.quote(capture_dir)
    q_file = shlex.quote(capture_file)

    sha_line = (
        "  if command -v sha256sum >/dev/null 2>&1; then"
        ' __as_sha=$(sha256sum "$__as_f" | cut -d" " -f1);'
        " else __as_sha=unavailable; fi"
    )
    marker_line = (
        f"  printf '\\n%s\\n' '{marker_prefix}"
        " full output '\"$__as_sz\"' bytes"
        " -> '\"$__as_f\"' sha256='\"$__as_sha\"' complete=true]'"
    )

    return f"""{_HARNESS_SENTINEL}
__as_d={q_dir}
__as_f={q_file}
mkdir -p "$__as_d" || {{ echo '{fail_msg}' >&2; exit 1; }}
(
trap '__as_user_ec=$?; wait; exit "$__as_user_ec"' EXIT
{command})  > "$__as_f" 2>&1
__as_ec=$?
__as_sz=$(wc -c < "$__as_f")
if [ "$__as_sz" -le {inline_bytes} ]; then
  cat "$__as_f"
  rm -f -- "$__as_f"
else
{sha_line}
  head -c {head} "$__as_f"
{marker_line}
  tail -c {tail} "$__as_f"
fi
exit $__as_ec
"""


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

    disabled, inline_bytes = _read_policy(cwd)
    if disabled:
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    command = tool_input.get("command", "")
    if not isinstance(command, str) or not command:
        sys.exit(0)

    harness = _build_harness(command, cwd, inline_bytes)
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
