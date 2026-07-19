"""Tests for the Codex native shell capture PreToolUse hook."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

_SENTINEL = "# autoskillit-shell-capture v1"


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
    assert "echo hello" in command


def test_rewrites_on_turn_id_payload(monkeypatch):
    event = _build_event("echo hello")
    event["turn_id"] = "turn-1"

    output = _run_hook(event, monkeypatch)

    payload = json.loads(output)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "allow"
    command = payload["hookSpecificOutput"]["updatedInput"]["command"]
    assert _SENTINEL in command
    assert "echo hello" in command


def test_always_wraps_sentinel_prefixed_command(monkeypatch):
    already_wrapped = f"{_SENTINEL}\necho hi"
    output = _run_hook(_build_event(already_wrapped), monkeypatch, env_backend="codex")

    command = _updated_command(output)
    assert command.count(_SENTINEL) == 2
    assert already_wrapped in command


def test_newline_normalization_applied(monkeypatch):
    output = _run_hook(_build_event("echo test"), monkeypatch, env_backend="codex")

    command = _updated_command(output)
    assert "echo test\n)" in command


def test_disabled_policy_no_rewrite(monkeypatch, tmp_path):
    config_dir = tmp_path / ".autoskillit" / "temp"
    config_dir.mkdir(parents=True)
    (config_dir / ".hook_config.json").write_text(
        json.dumps({"output_budget_policy": {"disabled": True}})
    )

    event = _build_event("echo hello", cwd=str(tmp_path))
    assert _run_hook(event, monkeypatch, env_backend="codex") == ""


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


def test_marker_provenance_and_shell_safety(monkeypatch):
    output = _run_hook(_build_event("echo hello"), monkeypatch, env_backend="codex")
    command = _updated_command(output)

    assert "AutoSkillit" in command
    assert "shell_capture_hook" in command

    import re

    marker_matches = re.findall(r"printf '\\n%s\\n' (.+)", command)
    assert marker_matches, "expected a printf-embedded capture marker in the harness"
    marker = marker_matches[0]

    assert "`" not in marker

    for var in ("$__as_sz", "$__as_f", "$__as_sha"):
        assert f'"{var}"' in marker, f"expected expansion-safe {var} in marker"

    stripped = marker
    for var in ("$__as_sz", "$__as_f", "$__as_sha"):
        stripped = stripped.replace(f'"{var}"', "")
    assert "$" not in stripped
