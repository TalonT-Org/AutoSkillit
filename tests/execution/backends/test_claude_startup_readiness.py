"""Production Claude startup-readiness capability and binding tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autoskillit.core import (
    CLAUDE_MCP_CONNECT_TIMEOUT_ENV_VAR,
    CLAUDE_MCP_CONNECT_TIMEOUT_MS,
    CLAUDE_MCP_CONNECTION_NONBLOCKING,
    ExecutableLaunchBinding,
)
from autoskillit.execution.backends import ClaudeCodeBackend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _binding(tmp_path: Path) -> ExecutableLaunchBinding:
    executable = tmp_path / "claude"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    return ExecutableLaunchBinding.resolve(
        binary_name="claude",
        environment={
            "PATH": str(tmp_path),
            "MCP_CONNECTION_NONBLOCKING": CLAUDE_MCP_CONNECTION_NONBLOCKING,
            CLAUDE_MCP_CONNECT_TIMEOUT_ENV_VAR: str(CLAUDE_MCP_CONNECT_TIMEOUT_MS),
        },
        cwd=tmp_path,
    )


def test_interactive_cmd_uses_bound_path_and_sealed_readiness_values(tmp_path: Path) -> None:
    executable = tmp_path / "claude"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    backend = ClaudeCodeBackend()
    extras = {
        "PATH": str(tmp_path),
        "MCP_CONNECTION_NONBLOCKING": "1",
        "MCP_CONNECT_TIMEOUT_MS": "1",
    }
    candidate = backend.build_interactive_cmd(
        env_extras=extras,
    )
    binding = ExecutableLaunchBinding.resolve(
        binary_name="claude",
        environment=candidate.env,
        cwd=tmp_path,
    )

    spec = backend.build_interactive_cmd(
        executable=binding,
        env_extras=extras,
    )

    assert spec.cmd[0] == str(binding.path)
    assert spec.env["MCP_CONNECTION_NONBLOCKING"] == CLAUDE_MCP_CONNECTION_NONBLOCKING
    assert spec.env[CLAUDE_MCP_CONNECT_TIMEOUT_ENV_VAR] == str(CLAUDE_MCP_CONNECT_TIMEOUT_MS)
    assert spec.env == binding.launch_environment


def test_pre_launch_probes_bound_executable_with_bound_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    binding = _binding(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        captured["cmd"] = cmd
        captured.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="2.1.197 (Claude Code)\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert ClaudeCodeBackend().ensure_pre_launch(executable=binding) == []
    assert captured["cmd"] == (str(binding.path), "--version")
    assert captured["env"] == dict(binding.launch_environment)
    assert captured["cwd"] == str(binding.cwd)


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [
        (1, "", "failed"),
        (0, "", "empty"),
        (0, "Claude Code unknown", "unparseable"),
        (0, "2.1.141 (Claude Code)", "requires Claude Code"),
    ],
)
def test_pre_launch_fails_closed_for_unusable_capability_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    returncode: int,
    stdout: str,
    expected: str,
) -> None:
    binding = _binding(tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            cmd, returncode, stdout=stdout, stderr=""
        ),
    )

    errors = ClaudeCodeBackend().ensure_pre_launch(executable=binding)

    assert errors
    assert expected in errors[0]


def test_pre_launch_retains_bounded_nonzero_probe_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    binding = _binding(tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            cmd,
            2,
            stdout="",
            stderr="\x1b[31m" + ("x" * 2_000) + " loader policy denied",
        ),
    )

    errors = ClaudeCodeBackend().ensure_pre_launch(executable=binding)

    assert len(errors) == 1
    assert "loader policy denied" in errors[0]
    assert "\x1b" not in errors[0]
    assert len(errors[0]) < 1_200
