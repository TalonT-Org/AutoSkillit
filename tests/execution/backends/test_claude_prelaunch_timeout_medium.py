"""Bounded Claude pre-launch timeout behavior."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autoskillit.core import atomic_write, resolve_executable_launch_binding
from autoskillit.execution.backends import ClaudeCodeBackend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def test_prelaunch_probe_times_out_after_five_seconds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "claude"
    atomic_write(executable, "#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    binding = resolve_executable_launch_binding(
        binary_name="claude",
        environment={"PATH": str(tmp_path)},
        cwd=tmp_path,
    )

    def timeout(cmd, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["timeout"] == 5
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)

    readiness = ClaudeCodeBackend().ensure_pre_launch(executable=binding)

    assert readiness.errors == ("Claude Code capability probe timed out",)
    assert readiness.attested_env == {}
