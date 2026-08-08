"""Tests for the Codex native shell capture PreToolUse hook."""

from __future__ import annotations

import io
import json
import shlex
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from autoskillit.hooks._capture_contract import (
    _MAX_COMMAND_BYTES,
    CAPTURE_REQUEST_PROTOCOL_VERSION,
    MANAGED_ATTEMPT_ID_ENV_VAR,
    MANAGED_LAUNCH_ID_ENV_VAR,
    MANAGED_LINEAGE_DIGEST_ENV_VAR,
    MANAGED_LINEAGE_REF_ENV_VAR,
    NATIVE_SHELL_CAPTURE_MODE_ENV_VAR,
    CaptureLineageRef,
    CaptureRequest,
    canonical_json_bytes,
    decode_capture_request,
    parse_capture_v2,
)

from .conftest import _FAILURE_GRADE_RE

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

_SENTINEL = "# autoskillit-shell-capture v1"
_TIMEOUT = 30


def _build_event(command, cwd: str = "/abs/project") -> dict:
    return {"cwd": cwd, "tool_input": {"command": command}}


def _run_hook(event_data, monkeypatch, *, env_backend: str | None = None) -> str:
    from autoskillit.hooks.shell_capture_hook import main  # noqa: PLC0415

    if env_backend is not None:
        monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", env_backend)
    else:
        monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)

    stdin_text = event_data if isinstance(event_data, str) else json.dumps(event_data)
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))

    output = io.StringIO()
    with pytest.raises(SystemExit), redirect_stdout(output):
        main()
    return output.getvalue()


def _updated_command(output: str) -> str:
    payload = json.loads(output)
    return payload["hookSpecificOutput"]["updatedInput"]["command"]


def _runner_argv(command: str) -> list[str]:
    lines = command.splitlines()
    assert lines[0] == _SENTINEL
    assert len(lines) >= 2
    tokens = shlex.split(lines[-1])
    # Strip env-var prefix (PYTHONDONTWRITEBYTECODE=1) from the harness line.
    while tokens and "=" in tokens[0]:
        tokens.pop(0)
    return tokens


def _runner_request(command: str) -> CaptureRequest:
    argv = _runner_argv(command)
    assert argv[1] == "-I"
    assert argv[2] == "-B"
    assert Path(argv[3]).name == "_capture_artifacts.py"
    assert len(argv) == 5
    return decode_capture_request(argv[4])


def _transported_command(command: str) -> str:
    request = _runner_request(command)
    assert request.action == "run"
    assert request.command is not None
    return request.command


def _set_managed_controls(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str,
) -> CaptureLineageRef:
    reference = CaptureLineageRef(
        schema_version=1,
        launch_id="a" * 32,
        lineage_digest="b" * 64,
        lineage_anchor="/managed/lineage/anchor",
        anchor_device=12,
        anchor_inode=34,
    )
    monkeypatch.setenv(NATIVE_SHELL_CAPTURE_MODE_ENV_VAR, mode)
    monkeypatch.setenv(MANAGED_LAUNCH_ID_ENV_VAR, reference.launch_id)
    monkeypatch.setenv(MANAGED_ATTEMPT_ID_ENV_VAR, "c" * 32)
    monkeypatch.setenv(MANAGED_LINEAGE_DIGEST_ENV_VAR, reference.lineage_digest)
    monkeypatch.setenv(
        MANAGED_LINEAGE_REF_ENV_VAR,
        canonical_json_bytes(
            {
                "schema_version": reference.schema_version,
                "launch_id": reference.launch_id,
                "lineage_digest": reference.lineage_digest,
                "lineage_anchor": reference.lineage_anchor,
                "anchor_device": reference.anchor_device,
                "anchor_inode": reference.anchor_inode,
            }
        ).decode("ascii"),
    )
    return reference


def test_silent_off_codex(monkeypatch):
    event = _build_event("echo hello")

    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)
    assert _run_hook(event, monkeypatch) == ""

    assert _run_hook(event, monkeypatch, env_backend="claude_code") == ""


def test_rewrites_on_codex_env(monkeypatch):
    output = _run_hook(_build_event("echo hello"), monkeypatch, env_backend="codex")

    payload = json.loads(output)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "allow"
    command = payload["hookSpecificOutput"]["updatedInput"]["command"]
    assert _SENTINEL in command
    assert _transported_command(command) == "echo hello"
    assert "echo hello" in command
    request = _runner_request(command)
    assert request.mode == "capture"
    assert request.attempt_id is None
    assert request.lineage_ref is None
    assert "using capture" in payload["hookSpecificOutput"]["additionalContext"]


def test_valid_direct_controls_are_bound_into_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = _set_managed_controls(monkeypatch, mode="direct")

    output = _run_hook(
        _build_event("echo direct"),
        monkeypatch,
        env_backend="codex",
    )
    payload = json.loads(output)
    request = _runner_request(_updated_command(output))

    assert request.mode == "direct"
    assert request.attempt_id == "c" * 32
    assert request.lineage_ref == reference
    assert "additionalContext" not in payload["hookSpecificOutput"]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (NATIVE_SHELL_CAPTURE_MODE_ENV_VAR, "invalid"),
        (MANAGED_LAUNCH_ID_ENV_VAR, "d" * 32),
        (MANAGED_ATTEMPT_ID_ENV_VAR, "invalid"),
        (MANAGED_LINEAGE_DIGEST_ENV_VAR, "e" * 64),
        (MANAGED_LINEAGE_REF_ENV_VAR, '{"not":"canonical lineage"}'),
    ],
)
def test_invalid_or_mismatched_controls_fall_back_atomically_to_capture(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    _set_managed_controls(monkeypatch, mode="direct")
    monkeypatch.setenv(name, value)

    output = _run_hook(
        _build_event("echo fallback"),
        monkeypatch,
        env_backend="codex",
    )
    payload = json.loads(output)
    request = _runner_request(_updated_command(output))

    assert request.mode == "capture"
    assert request.attempt_id is None
    assert request.lineage_ref is None
    diagnostic = payload["hookSpecificOutput"]["additionalContext"]
    assert "incomplete managed native-shell controls" in diagnostic
    assert "falling back to capture" in diagnostic
    assert len(diagnostic.encode("utf-8")) <= 320


def test_resolve_control_declared_capture_mode_has_no_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(i) mode=capture, no managed-identity vars: a normal declared state —
    absence of the 4 identity vars is expected, not anomalous."""
    from autoskillit.hooks.shell_capture_hook import _resolve_control  # noqa: PLC0415

    monkeypatch.setenv(NATIVE_SHELL_CAPTURE_MODE_ENV_VAR, "capture")
    for var in (
        MANAGED_LAUNCH_ID_ENV_VAR,
        MANAGED_ATTEMPT_ID_ENV_VAR,
        MANAGED_LINEAGE_DIGEST_ENV_VAR,
        MANAGED_LINEAGE_REF_ENV_VAR,
    ):
        monkeypatch.delenv(var, raising=False)

    control = _resolve_control()

    assert control.mode == "capture"
    assert control.attempt_id is None
    assert control.lineage_ref is None
    assert control.diagnostic is None


def test_resolve_control_complete_direct_identity_has_no_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(ii) mode=direct + complete valid identity tuple: managed path, no
    diagnostic."""
    from autoskillit.hooks.shell_capture_hook import _resolve_control  # noqa: PLC0415

    reference = _set_managed_controls(monkeypatch, mode="direct")

    control = _resolve_control()

    assert control.mode == "direct"
    assert control.attempt_id == "c" * 32
    assert control.lineage_ref == reference
    assert control.diagnostic is None


def test_resolve_control_undeclared_note_is_neutral(monkeypatch: pytest.MonkeyPatch) -> None:
    """(iv) mode unset entirely: a neutral note, no failure-grade words."""
    from autoskillit.hooks.shell_capture_hook import _resolve_control  # noqa: PLC0415

    for var in (
        NATIVE_SHELL_CAPTURE_MODE_ENV_VAR,
        MANAGED_LAUNCH_ID_ENV_VAR,
        MANAGED_ATTEMPT_ID_ENV_VAR,
        MANAGED_LINEAGE_DIGEST_ENV_VAR,
        MANAGED_LINEAGE_REF_ENV_VAR,
    ):
        monkeypatch.delenv(var, raising=False)

    control = _resolve_control()

    assert control.mode == "capture"
    assert control.diagnostic is not None
    assert "native-shell control undeclared; using capture" in control.diagnostic
    assert not _FAILURE_GRADE_RE.search(control.diagnostic)


def test_rewrites_on_turn_id_payload(monkeypatch):
    event = _build_event("echo hello")
    event["turn_id"] = "turn-1"

    output = _run_hook(event, monkeypatch)

    payload = json.loads(output)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "allow"
    command = payload["hookSpecificOutput"]["updatedInput"]["command"]
    assert _SENTINEL in command
    assert _transported_command(command) == "echo hello"


def test_always_wraps_sentinel_prefixed_command(monkeypatch):
    already_wrapped = f"{_SENTINEL}\necho hi"
    output = _run_hook(_build_event(already_wrapped), monkeypatch, env_backend="codex")

    command = _updated_command(output)
    assert command.splitlines()[0] == _SENTINEL
    assert _transported_command(command) == already_wrapped


@pytest.mark.parametrize("original", ["echo test", "echo test\n"])
def test_command_transport_preserves_original(monkeypatch, original):
    output = _run_hook(_build_event(original), monkeypatch, env_backend="codex")

    command = _updated_command(output)
    assert _transported_command(command) == original


def test_verified_disabled_policy_is_runner_owned(monkeypatch, tmp_path):
    config_dir = tmp_path / ".autoskillit" / "temp"
    config_dir.mkdir(parents=True)
    (config_dir / ".hook_config.json").write_text(
        json.dumps({"output_budget_policy": {"disabled": True}})
    )

    event = _build_event("echo hello", cwd=str(tmp_path))
    output = _run_hook(event, monkeypatch, env_backend="codex")
    command = _updated_command(output)
    argv = _runner_argv(command)

    assert argv[0] == sys.executable
    assert argv[1] == "-I"
    assert argv[2] == "-B"
    assert Path(argv[3]).name == "_capture_artifacts.py"
    request = decode_capture_request(argv[4])
    assert request.action == "run"
    assert request.command == "echo hello"
    assert request.cwd == str(tmp_path)
    assert len(request.capture_id) == 16

    completed = subprocess.run(
        ["bash", "-c", command],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=_TIMEOUT,
    )

    assert completed.returncode == 0
    assert completed.stdout == "hello\n"
    assert "shell capture v2:" not in completed.stdout
    assert not (config_dir / "shell_capture").exists()


def test_malformed_stdin_fails_open(monkeypatch):
    assert _run_hook("not json", monkeypatch, env_backend="codex") == ""


@pytest.mark.parametrize("serialized", ["[]", "null", "42", '"event"'])
def test_non_mapping_hook_payload_fails_open(
    monkeypatch: pytest.MonkeyPatch,
    serialized: str,
) -> None:
    assert _run_hook(serialized, monkeypatch, env_backend="codex") == ""


def test_missing_cwd_fails_open(monkeypatch):
    event = {"tool_input": {"command": "echo hello"}}
    assert _run_hook(event, monkeypatch, env_backend="codex") == ""

    relative_event = _build_event("echo hello", cwd="relative/path")
    assert _run_hook(relative_event, monkeypatch, env_backend="codex") == ""


def test_non_string_command_fails_open(monkeypatch):
    event = {"cwd": "/abs/project", "tool_input": {"command": 5}}
    assert _run_hook(event, monkeypatch, env_backend="codex") == ""


@pytest.mark.parametrize("tool_input", [None, [], "command", 42])
def test_non_mapping_tool_input_fails_open_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
    tool_input: object,
) -> None:
    event = {"cwd": "/abs/project", "tool_input": tool_input}
    assert _run_hook(event, monkeypatch, env_backend="codex") == ""


@pytest.mark.parametrize("command", ["x" * (_MAX_COMMAND_BYTES + 1), "printf bad\x00command"])
def test_invalid_command_transport_builds_nonexecuting_rejection(command):
    from autoskillit.hooks.shell_capture_hook import _build_harness

    harness = _build_harness(command, "/abs/project", "0123456789abcdef")
    argv = _runner_argv(harness)

    request = decode_capture_request(argv[4])
    assert request == CaptureRequest(
        protocol_version=CAPTURE_REQUEST_PROTOCOL_VERSION,
        action="reject",
        mode="capture",
        attempt_id=None,
        lineage_ref=None,
        cwd="/abs/project",
        capture_id="0123456789abcdef",
    )
    assert command not in harness


def test_reject_envelope_retains_valid_managed_direct_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = _set_managed_controls(monkeypatch, mode="direct")

    output = _run_hook(
        _build_event("x" * (_MAX_COMMAND_BYTES + 1)),
        monkeypatch,
        env_backend="codex",
    )
    request = _runner_request(_updated_command(output))

    assert request.action == "reject"
    assert request.mode == "direct"
    assert request.attempt_id == "c" * 32
    assert request.lineage_ref == reference
    assert request.command is None


def test_valid_command_remains_policy_visible_without_becoming_shell_code() -> None:
    from autoskillit.hooks.shell_capture_hook import _build_harness  # noqa: PLC0415

    command = "printf safe; rm -rf /policy-probe"

    harness = _build_harness(command, "/abs/project", "0123456789abcdef")
    preview_argv = shlex.split(harness.splitlines()[1])

    assert command in harness
    assert preview_argv == [":", json.dumps(command)]


def test_arg_max_exhaustion_builds_nonexecuting_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoskillit.hooks.shell_capture_hook as shell_capture_hook

    command = "printf must-not-run"
    cwd = "/abs/project"
    capture_id = "0123456789abcdef"
    run_request = shell_capture_hook.CaptureRequest(
        protocol_version=shell_capture_hook.CAPTURE_REQUEST_PROTOCOL_VERSION,
        action="run",
        mode="capture",
        attempt_id=None,
        lineage_ref=None,
        cwd=cwd,
        capture_id=capture_id,
        command=command,
    )
    run_argv, run_harness = shell_capture_hook._request_harness(
        run_request,
        policy_command=command,
    )
    run_candidates = (run_argv, ["bash", "-c", run_harness])

    def _argv_bytes(argv: list[str]) -> int:
        return sum(len(shell_capture_hook.os.fsencode(argument)) + 1 for argument in argv)

    environment_bytes = sum(
        len(shell_capture_hook.os.fsencode(key)) + len(shell_capture_hook.os.fsencode(value)) + 2
        for key, value in shell_capture_hook.os.environ.items()
    )
    pointer_size = shell_capture_hook.struct.calcsize("P")
    for argv in run_candidates:
        pointer_bytes = (len(argv) + len(shell_capture_hook.os.environ) + 2) * pointer_size
        assert shell_capture_hook._exec_footprint(argv) == (
            _argv_bytes(argv) + environment_bytes + pointer_bytes
        )

    arg_max = (
        max(shell_capture_hook._exec_footprint(argv) for argv in run_candidates)
        + shell_capture_hook._ARG_MAX_HEADROOM_BYTES
        - 1
    )
    monkeypatch.setattr(shell_capture_hook.os, "sysconf", lambda _name: arg_max)

    harness = shell_capture_hook._build_harness(
        command,
        cwd,
        capture_id,
    )
    request = _runner_request(harness)

    assert request.action == "reject"
    assert request.cwd == cwd
    assert request.capture_id == capture_id
    assert command not in harness


def test_environment_only_arg_max_exhaustion_uses_local_fail_closed_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoskillit.hooks.shell_capture_hook as shell_capture_hook

    monkeypatch.setenv("PHASE4_OVERSIZED_ENVIRONMENT", "x" * 8192)
    environment_floor = shell_capture_hook._exec_footprint(
        [sys.executable, "-I", str(shell_capture_hook._runner_path()), "request"]
    )
    monkeypatch.setattr(
        shell_capture_hook.os,
        "sysconf",
        lambda _name: environment_floor + shell_capture_hook._ARG_MAX_HEADROOM_BYTES - 1,
    )

    harness = shell_capture_hook._build_harness(
        "printf must-not-run",
        "/abs/project",
        "0123456789abcdef",
    )

    assert harness.splitlines()[0] == _SENTINEL
    assert harness.splitlines()[-1] == "exit 1"
    assert "_capture_artifacts.py" not in harness
    assert "must-not-run" not in harness


def test_marker_provenance_is_emitted_by_runner(monkeypatch, tmp_path):
    config_dir = tmp_path / ".autoskillit" / "temp"
    config_dir.mkdir(parents=True)
    (config_dir / ".hook_config.json").write_text(
        json.dumps({"output_budget_policy": {"shell_max_inline_bytes": 8}})
    )
    output = _run_hook(
        _build_event("printf 0123456789abcdef", cwd=str(tmp_path)),
        monkeypatch,
        env_backend="codex",
    )
    command = _updated_command(output)
    argv = _runner_argv(command)
    request = decode_capture_request(argv[4])

    assert argv[1] == "-I"
    assert argv[2] == "-B"
    assert Path(argv[3]).name == "_capture_artifacts.py"
    assert len(request.capture_id) == 16
    assert "printf 0123456789abcdef" in command
    assert "AutoSkillit hook shell_capture_hook" not in command
    assert "`" not in command

    completed = subprocess.run(
        ["bash", "-c", command],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=_TIMEOUT,
    )
    assert completed.returncode == 0
    candidates = [
        line.encode()
        for line in completed.stdout.splitlines()
        if line.startswith("[AutoSkillit shell capture v2:")
    ]
    assert len(candidates) == 1
    parsed = parse_capture_v2(candidates[0])
    assert parsed.reference_status == "published"
    assert parsed.reference is not None
    assert f"shell_{request.capture_id}.log" not in completed.stdout
