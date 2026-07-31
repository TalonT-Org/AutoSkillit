"""Exact executable and environment binding contracts for interactive launches."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from autoskillit.core import (
    CLAUDE_MCP_CONNECT_TIMEOUT_ENV_VAR,
    CLAUDE_MCP_CONNECT_TIMEOUT_MS,
    CLAUDE_MCP_CONNECTION_NONBLOCKING,
    executable_binding_matches_current_file,
    resolve_executable_launch_binding,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _executable(path: Path, content: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_binding_prefers_explicit_canonical_executable(tmp_path: Path) -> None:
    fallback = _executable(tmp_path / "fallback")
    explicit = _executable(tmp_path / "explicit")
    environment = {
        "PATH": str(tmp_path),
        "CLAUDE_CODE_EXECPATH": str(explicit),
        "MCP_CONNECTION_NONBLOCKING": CLAUDE_MCP_CONNECTION_NONBLOCKING,
        CLAUDE_MCP_CONNECT_TIMEOUT_ENV_VAR: str(CLAUDE_MCP_CONNECT_TIMEOUT_MS),
    }

    binding = resolve_executable_launch_binding(
        binary_name=fallback.name,
        environment=environment,
        cwd=tmp_path,
        explicit_path_env="CLAUDE_CODE_EXECPATH",
    )

    assert binding.path == explicit.resolve()
    assert binding.launch_environment == environment
    assert binding.cwd == tmp_path.resolve()
    assert binding.file_sha256


def test_binding_uses_effective_path_when_no_explicit_path(tmp_path: Path) -> None:
    executable = _executable(tmp_path / "claude")

    binding = resolve_executable_launch_binding(
        binary_name="claude",
        environment={"PATH": str(tmp_path)},
        cwd=tmp_path,
    )

    assert binding.path == executable.resolve()


def test_binding_detects_same_path_replacement(tmp_path: Path) -> None:
    executable = _executable(tmp_path / "claude")
    binding = resolve_executable_launch_binding(
        binary_name="claude",
        environment={"PATH": str(tmp_path)},
        cwd=tmp_path,
    )

    executable.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    os.utime(executable, None)

    assert not executable_binding_matches_current_file(binding)


def test_binding_rejects_non_executable_explicit_path(tmp_path: Path) -> None:
    candidate = tmp_path / "claude"
    candidate.write_text("not executable", encoding="utf-8")

    with pytest.raises(ValueError, match="not executable"):
        resolve_executable_launch_binding(
            binary_name="claude",
            environment={
                "PATH": str(tmp_path),
                "CLAUDE_CODE_EXECPATH": str(candidate),
            },
            cwd=tmp_path,
            explicit_path_env="CLAUDE_CODE_EXECPATH",
        )
