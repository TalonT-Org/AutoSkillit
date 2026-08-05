#!/usr/bin/env python3
"""PreToolUse input-rewrite hook for Codex native shell lossless capture.

Rewrites every native shell command on Codex into a minimal isolated-Python
runner invocation. The runner establishes the project/capture trust anchor,
executes the command, and emits only a bounded inline slice.

Codex-only (#4286 / ADR-0006).  Claude Code sessions are unaffected.
stdlib-only; no autoskillit imports.
"""

from __future__ import annotations

import json
import os
import shlex
import struct
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

_HOOKS_DIR = str(Path(__file__).resolve().parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

if TYPE_CHECKING:
    from autoskillit.hooks._capture_contract import (
        _CAPTURE_ID_RE,
        _IDENTITY_RE,
        _MAX_COMMAND_BYTES,
        CAPTURE_REQUEST_PROTOCOL_VERSION,
        MANAGED_ATTEMPT_ID_ENV_VAR,
        MANAGED_LAUNCH_ID_ENV_VAR,
        MANAGED_LINEAGE_DIGEST_ENV_VAR,
        MANAGED_LINEAGE_REF_ENV_VAR,
        NATIVE_SHELL_CAPTURE_MODE_ENV_VAR,
        CaptureLineageRef,
        CaptureProtocolError,
        CaptureRequest,
        decode_lineage_ref_json,
        encode_capture_request,
    )
    from autoskillit.hooks._policy_event import PolicyEvent, render_provenance_prefix
else:
    from _capture_contract import (
        _CAPTURE_ID_RE,
        _IDENTITY_RE,
        _MAX_COMMAND_BYTES,
        CAPTURE_REQUEST_PROTOCOL_VERSION,
        MANAGED_ATTEMPT_ID_ENV_VAR,
        MANAGED_LAUNCH_ID_ENV_VAR,
        MANAGED_LINEAGE_DIGEST_ENV_VAR,
        MANAGED_LINEAGE_REF_ENV_VAR,
        NATIVE_SHELL_CAPTURE_MODE_ENV_VAR,
        CaptureLineageRef,
        CaptureProtocolError,
        CaptureRequest,
        decode_lineage_ref_json,
        encode_capture_request,
    )
    from _policy_event import PolicyEvent, render_provenance_prefix

_HARNESS_SENTINEL = "# autoskillit-shell-capture v1"
_ARG_MAX_FALLBACK_BYTES = 128 * 1024
_ARG_MAX_HEADROOM_BYTES = 32 * 1024
_RUNNER_BASENAME = "_capture_artifacts.py"
_HOOK_ID = "shell_capture_hook"
_HOOK_VERSION = 1
_POLICY_EVENT_NAME = "native_shell_control"
_UNDECLARED_DETAIL = "native-shell control undeclared; using capture"
_INCOMPLETE_DETAIL = "incomplete managed native-shell controls; falling back to capture"
_LOCAL_REJECTION_DETAIL = "AutoSkillit shell capture request rejected before execution"


@dataclass(frozen=True, slots=True)
class _ResolvedControl:
    mode: str
    attempt_id: str | None
    lineage_ref: CaptureLineageRef | None
    diagnostic: str | None = None


def _is_codex_session(payload: dict) -> bool:
    return os.environ.get("AUTOSKILLIT_AGENT_BACKEND") == "codex" or "turn_id" in payload


def _system_arg_max() -> int:
    try:
        value = os.sysconf("SC_ARG_MAX")
    except (AttributeError, OSError, ValueError):
        return _ARG_MAX_FALLBACK_BYTES
    return value if isinstance(value, int) and value > 0 else _ARG_MAX_FALLBACK_BYTES


def _exec_footprint(
    argv: list[str],
    environment: Mapping[str, str] | None = None,
) -> int:
    actual_environment = os.environ if environment is None else environment
    argv_bytes = sum(len(os.fsencode(argument)) + 1 for argument in argv)
    environment_bytes = sum(
        len(os.fsencode(key)) + len(os.fsencode(value)) + 2
        for key, value in actual_environment.items()
    )
    pointer_bytes = (len(argv) + len(actual_environment) + 2) * struct.calcsize("P")
    return argv_bytes + environment_bytes + pointer_bytes


def _fits_arg_max(
    argv: list[str],
    environment: Mapping[str, str] | None = None,
) -> bool:
    return _exec_footprint(argv, environment) + _ARG_MAX_HEADROOM_BYTES <= _system_arg_max()


def _render_harness(argv: list[str], *, policy_command: str | None = None) -> str:
    lines = [_HARNESS_SENTINEL]
    if policy_command is not None:
        policy_text = json.dumps(policy_command, ensure_ascii=True)
        lines.append(f": {shlex.quote(policy_text)}")
    lines.append(shlex.join(argv))
    return "\n".join(lines)


def _runner_path() -> Path:
    return Path(__file__).resolve().with_name(_RUNNER_BASENAME)


def _runner_argv(request: CaptureRequest) -> list[str]:
    return [
        sys.executable,
        "-I",
        str(_runner_path()),
        encode_capture_request(request),
    ]


def _request_harness(
    request: CaptureRequest,
    *,
    policy_command: str | None,
) -> tuple[list[str], str]:
    argv = _runner_argv(request)
    return argv, _render_harness(argv, policy_command=policy_command)


def _harness_fits_arg_max(
    argv: list[str],
    harness: str,
    environment: Mapping[str, str] | None = None,
) -> bool:
    return _fits_arg_max(argv, environment) and _fits_arg_max(
        ["bash", "-c", harness],
        environment,
    )


def _local_rejection_harness() -> str:
    return "\n".join(
        (
            _HARNESS_SENTINEL,
            f"printf '%s\\n' {shlex.quote(_LOCAL_REJECTION_DETAIL)} >&2",
            "exit 1",
        )
    )


def _build_harness(
    command: str,
    cwd: str,
    capture_id: str,
    *,
    mode: str = "capture",
    attempt_id: str | None = None,
    lineage_ref: CaptureLineageRef | None = None,
    environment: Mapping[str, str] | None = None,
) -> str:
    """Build one preflighted isolated runner request or a local rejection."""

    if not _CAPTURE_ID_RE.fullmatch(capture_id):
        raise ValueError("capture_id must contain exactly 16 lowercase hex characters")
    try:
        command_bytes = command.encode("utf-8")
    except UnicodeEncodeError:
        command_bytes = b""
    command_valid = (
        isinstance(command, str)
        and "\x00" not in command
        and bool(command_bytes)
        and len(command_bytes) <= _MAX_COMMAND_BYTES
    )
    if command_valid:
        run_request = CaptureRequest(
            protocol_version=CAPTURE_REQUEST_PROTOCOL_VERSION,
            action="run",
            mode=mode,
            attempt_id=attempt_id,
            lineage_ref=lineage_ref,
            cwd=cwd,
            capture_id=capture_id,
            command=command,
        )
        run_argv, run_harness = _request_harness(
            run_request,
            policy_command=command,
        )
        if _harness_fits_arg_max(run_argv, run_harness, environment):
            return run_harness

    reject_request = CaptureRequest(
        protocol_version=CAPTURE_REQUEST_PROTOCOL_VERSION,
        action="reject",
        mode=mode,
        attempt_id=attempt_id,
        lineage_ref=lineage_ref,
        cwd=cwd,
        capture_id=capture_id,
    )
    reject_argv, reject_harness = _request_harness(
        reject_request,
        policy_command=None,
    )
    if _harness_fits_arg_max(reject_argv, reject_harness, environment):
        return reject_harness
    return _local_rejection_harness()


def _render_control_diagnostic(reason_code: str) -> str:
    event = PolicyEvent(
        hook_id=_HOOK_ID,
        hook_version=_HOOK_VERSION,
        event=_POLICY_EVENT_NAME,
        decision="capture",
        reason_code=reason_code,
    )
    return render_provenance_prefix(event)


def _undeclared_fallback() -> _ResolvedControl:
    return _ResolvedControl(
        mode="capture",
        attempt_id=None,
        lineage_ref=None,
        diagnostic=_render_control_diagnostic(_UNDECLARED_DETAIL),
    )


def _incomplete_fallback() -> _ResolvedControl:
    return _ResolvedControl(
        mode="capture",
        attempt_id=None,
        lineage_ref=None,
        diagnostic=_render_control_diagnostic(_INCOMPLETE_DETAIL),
    )


def _resolve_control(environment: Mapping[str, str] | None = None) -> _ResolvedControl:
    actual_environment = os.environ if environment is None else environment
    mode = actual_environment.get(NATIVE_SHELL_CAPTURE_MODE_ENV_VAR)
    launch_id = actual_environment.get(MANAGED_LAUNCH_ID_ENV_VAR)
    attempt_id = actual_environment.get(MANAGED_ATTEMPT_ID_ENV_VAR)
    lineage_digest = actual_environment.get(MANAGED_LINEAGE_DIGEST_ENV_VAR)
    serialized_ref = actual_environment.get(MANAGED_LINEAGE_REF_ENV_VAR)
    values = (mode, launch_id, attempt_id, lineage_digest, serialized_ref)
    identity_values = (launch_id, attempt_id, lineage_digest, serialized_ref)
    if all(value is None for value in values):
        # Nothing at all was declared — the pre-#4460 ambient state. Neutral,
        # not a failure: most non-cook Codex launch paths still don't declare.
        return _undeclared_fallback()
    if mode == "capture" and all(value is None for value in identity_values):
        # Cook sessions declare exactly this: capture mode, no managed
        # identity — a normal, declared state that needs no diagnostic at all.
        return _ResolvedControl(mode="capture", attempt_id=None, lineage_ref=None)
    if (
        mode not in {"capture", "direct"}
        or not isinstance(launch_id, str)
        or not isinstance(attempt_id, str)
        or _IDENTITY_RE.fullmatch(attempt_id) is None
        or not isinstance(lineage_digest, str)
        or not isinstance(serialized_ref, str)
    ):
        return _incomplete_fallback()
    try:
        lineage_ref = decode_lineage_ref_json(serialized_ref)
    except CaptureProtocolError:
        return _incomplete_fallback()
    if lineage_ref.launch_id != launch_id or lineage_ref.lineage_digest != lineage_digest:
        return _incomplete_fallback()
    return _ResolvedControl(
        mode=mode,
        attempt_id=attempt_id,
        lineage_ref=lineage_ref,
    )


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
    if not isinstance(tool_input, Mapping):
        sys.exit(0)
    command = tool_input.get("command", "")
    if not isinstance(command, str) or not command:
        sys.exit(0)

    control = _resolve_control()
    harness = _build_harness(
        command,
        cwd,
        uuid4().hex[:16],
        mode=control.mode,
        attempt_id=control.attempt_id,
        lineage_ref=control.lineage_ref,
    )
    hook_output = {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "updatedInput": {"command": harness},
    }
    if control.diagnostic is not None:
        hook_output["additionalContext"] = control.diagnostic
    payload = json.dumps({"hookSpecificOutput": hook_output})
    sys.stdout.write(payload + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
