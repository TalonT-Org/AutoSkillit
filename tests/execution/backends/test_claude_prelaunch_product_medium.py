"""Claude pre-launch readiness product tests using exact executable bindings."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from autoskillit.core import (
    AUTOSKILLIT_ATTESTED_META_SUPPORT,
    CLAUDE_MCP_CONNECT_TIMEOUT_ENV_VAR,
    CLAUDE_MCP_CONNECT_TIMEOUT_MS,
    CLAUDE_MCP_CONNECTION_NONBLOCKING,
    ExecutableLaunchBinding,
    atomic_write,
    resolve_executable_launch_binding,
)
from autoskillit.execution.backends import ClaudeCodeBackend
from autoskillit.execution.backends import claude as claude_module

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def _shim_binding(tmp_path: Path, version: str = "2.1.220") -> ExecutableLaunchBinding:
    executable = tmp_path / "claude"
    atomic_write(
        executable,
        f"#!/bin/sh\nprintf '%s\\n' '{version} (Claude Code)'\n",
    )
    executable.chmod(0o755)
    return resolve_executable_launch_binding(
        binary_name="claude",
        environment={
            "PATH": str(tmp_path),
            "MCP_CONNECTION_NONBLOCKING": CLAUDE_MCP_CONNECTION_NONBLOCKING,
            CLAUDE_MCP_CONNECT_TIMEOUT_ENV_VAR: str(CLAUDE_MCP_CONNECT_TIMEOUT_MS),
        },
        cwd=tmp_path,
    )


def test_supported_probe_does_not_leak_attestation_to_failed_probe(tmp_path: Path) -> None:
    backend = ClaudeCodeBackend()
    readiness = backend.ensure_pre_launch(executable=_shim_binding(tmp_path))
    failed_readiness = backend.ensure_pre_launch()

    assert readiness.errors == ()
    assert readiness.attested_env[AUTOSKILLIT_ATTESTED_META_SUPPORT] == "1"
    assert failed_readiness.errors
    assert failed_readiness.attested_env == {}


def test_identity_drift_returns_no_attestation(tmp_path: Path) -> None:
    binding = _shim_binding(tmp_path)
    atomic_write(binding.path, "#!/bin/sh\nexit 9\n")
    binding.path.chmod(0o755)

    readiness = ClaudeCodeBackend().ensure_pre_launch(executable=binding)

    assert readiness.errors
    assert "changed after capability probing" in readiness.errors[0]
    assert readiness.attested_env == {}


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("timeout", "timed out"),
        ("os-error", "probe failed"),
        ("nonzero", "exit code 7"),
        ("empty", "empty"),
        ("unparseable", "unparseable"),
        ("minimum", "requires Claude Code"),
    ],
)
def test_failed_probe_results_never_carry_attestation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    outcome: str,
    expected: str,
) -> None:
    binding = _shim_binding(tmp_path)

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        if outcome == "os-error":
            raise OSError("exec denied")
        if outcome == "nonzero":
            return subprocess.CompletedProcess(cmd, 7, stdout="", stderr="denied")
        if outcome == "empty":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if outcome == "unparseable":
            return subprocess.CompletedProcess(cmd, 0, stdout="Claude unknown", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="2.1.141", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    readiness = ClaudeCodeBackend().ensure_pre_launch(executable=binding)

    assert readiness.errors
    assert expected in readiness.errors[0]
    assert readiness.attested_env == {}


def test_invalid_minimum_version_returns_no_attestation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        claude_module,
        "CLAUDE_CODE_CAPABILITIES",
        replace(claude_module.CLAUDE_CODE_CAPABILITIES, min_version="invalid"),
    )

    readiness = ClaudeCodeBackend().ensure_pre_launch(executable=_shim_binding(tmp_path))

    assert readiness.errors
    assert "unparseable" in readiness.errors[0]
    assert readiness.attested_env == {}
