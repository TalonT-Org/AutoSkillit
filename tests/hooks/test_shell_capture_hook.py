"""Tests for the Codex native shell capture PreToolUse hook."""

from __future__ import annotations

import base64
import io
import json
import re
import shlex
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from autoskillit.hooks._capture_contract import _MAX_COMMAND_BYTES

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
    return shlex.split(lines[-1])


def _transported_command(command: str) -> str:
    argv = _runner_argv(command)
    assert argv[1] == "-I"
    assert Path(argv[2]).name == "_capture_artifacts.py"
    assert argv[3] == "run"
    return base64.b64decode(argv[4], validate=True).decode()


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
    assert Path(argv[2]).name == "_capture_artifacts.py"
    assert argv[3] == "run"
    assert base64.b64decode(argv[4], validate=True).decode() == "echo hello"
    assert argv[5] == str(tmp_path)
    assert re.fullmatch(r"[0-9a-f]{16}", argv[6])

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
    assert "SHELL_OUTPUT_CAPTURED" not in completed.stdout
    assert not (config_dir / "shell_capture").exists()


def test_malformed_stdin_fails_open(monkeypatch):
    assert _run_hook("not json", monkeypatch, env_backend="codex") == ""


def test_missing_cwd_fails_open(monkeypatch):
    event = {"tool_input": {"command": "echo hello"}}
    assert _run_hook(event, monkeypatch, env_backend="codex") == ""

    relative_event = _build_event("echo hello", cwd="relative/path")
    assert _run_hook(relative_event, monkeypatch, env_backend="codex") == ""


def test_non_string_command_fails_open(monkeypatch):
    event = {"cwd": "/abs/project", "tool_input": {"command": 5}}
    assert _run_hook(event, monkeypatch, env_backend="codex") == ""


@pytest.mark.parametrize("command", ["x" * (_MAX_COMMAND_BYTES + 1), "printf bad\x00command"])
def test_invalid_command_transport_builds_nonexecuting_rejection(command):
    from autoskillit.hooks.shell_capture_hook import _build_harness

    harness = _build_harness(command, "/abs/project", "0123456789abcdef")
    argv = _runner_argv(harness)

    assert argv[3] == "reject"
    assert command not in harness


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
    encoded = base64.b64encode(command.encode()).decode("ascii")
    runner_argv = [
        sys.executable,
        "-I",
        str(shell_capture_hook._runner_path()),
        "run",
        encoded,
        cwd,
        capture_id,
    ]
    preflight_harness = shell_capture_hook._render_harness(
        runner_argv,
        policy_command=command,
    )
    argv_candidates = (runner_argv, ["bash", "-c", preflight_harness])

    def _argv_bytes(argv: list[str]) -> int:
        return sum(len(shell_capture_hook.os.fsencode(argument)) + 1 for argument in argv)

    environment_bytes = sum(
        len(shell_capture_hook.os.fsencode(key)) + len(shell_capture_hook.os.fsencode(value)) + 2
        for key, value in shell_capture_hook.os.environ.items()
    )
    pointer_size = shell_capture_hook.struct.calcsize("P")
    for argv in argv_candidates:
        pointer_bytes = (len(argv) + len(shell_capture_hook.os.environ) + 2) * pointer_size
        assert shell_capture_hook._exec_footprint(argv) == (
            _argv_bytes(argv) + environment_bytes + pointer_bytes
        )

    arg_max = (
        max(shell_capture_hook._exec_footprint(argv) for argv in argv_candidates)
        + shell_capture_hook._ARG_MAX_HEADROOM_BYTES
        - 1
    )
    assert all(
        _argv_bytes(argv) + shell_capture_hook._ARG_MAX_HEADROOM_BYTES <= arg_max
        for argv in argv_candidates
    )
    monkeypatch.setattr(shell_capture_hook.os, "sysconf", lambda _name: arg_max)

    harness = shell_capture_hook._build_harness(
        command,
        cwd,
        capture_id,
    )
    argv = _runner_argv(harness)

    assert argv[3] == "reject"
    assert command not in harness


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

    assert argv[1] == "-I"
    assert Path(argv[2]).name == "_capture_artifacts.py"
    assert re.fullmatch(r"[0-9a-f]{16}", argv[-1])
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
    assert "AutoSkillit hook shell_capture_hook" in completed.stdout
    assert "SHELL_OUTPUT_CAPTURED" in completed.stdout
    assert f"shell_{argv[-1]}.log" in completed.stdout
