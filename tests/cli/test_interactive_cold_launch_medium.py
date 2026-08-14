"""Cold, unmanaged Claude interactive-launch transaction tests."""

from __future__ import annotations

import subprocess
from contextlib import nullcontext
from pathlib import Path

import pytest

from autoskillit.cli.session._session_launch import _run_interactive_session
from autoskillit.core import (
    AUTOSKILLIT_ATTESTED_META_SUPPORT,
    PluginLoadMode,
    atomic_write,
)
from autoskillit.execution.backends import ClaudeCodeBackend
from tests.cli._interactive_process import InteractiveProcessStub

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def _write_claude_shim(path: Path, version: str) -> None:
    atomic_write(
        path,
        "#!/bin/sh\n"
        'if [ "${1-}" = "--version" ]; then\n'
        f"  printf '%s\\n' '{version} (Claude Code)'\n"
        "fi\n"
        "exit 0\n",
    )
    path.chmod(0o755)


@pytest.fixture
def cold_launch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, dict[str, object]]:
    shim = tmp_path / "claude"
    captured: dict[str, object] = {"spawn_count": 0}
    real_run = subprocess.run
    real_popen = subprocess.Popen

    def capture_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if len(cmd) > 1 and cmd[1] == "--version":
            return real_run(cmd, **kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def capture_final_spawn(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if len(cmd) > 1 and cmd[1] == "--version":
            return real_popen(cmd, **kwargs)
        captured["spawn_count"] = int(captured["spawn_count"]) + 1
        captured["cmd"] = tuple(cmd)
        captured["env"] = dict(kwargs["env"])
        return InteractiveProcessStub()

    monkeypatch.delenv("CLAUDE_CODE_EXECPATH", raising=False)
    monkeypatch.setattr(subprocess, "run", capture_run)
    monkeypatch.setattr(subprocess, "Popen", capture_final_spawn)
    monkeypatch.setattr(
        "autoskillit.cli._plugin_artifact.interactive_plugin_authority",
        lambda **_kwargs: (None, PluginLoadMode.NONE),
    )
    monkeypatch.setattr(
        "autoskillit.cli.ui._terminal.terminal_guard",
        nullcontext,
    )
    return shim, captured


@pytest.mark.parametrize("version", ["2.1.220"])
def test_supported_cold_launch_spawns_with_probed_attestation(
    cold_launch: tuple[Path, dict[str, object]],
    tmp_path: Path,
    version: str,
) -> None:
    shim, captured = cold_launch
    _write_claude_shim(shim, version)

    result = _run_interactive_session(
        system_prompt="cold launch",
        extra_env={"PATH": str(tmp_path)},
        project_dir=tmp_path,
        backend=ClaudeCodeBackend(),
    )

    assert result is None
    assert captured["spawn_count"] == 1
    env = captured["env"]
    assert isinstance(env, dict)
    assert env[AUTOSKILLIT_ATTESTED_META_SUPPORT] == "1"


def test_unsupported_cold_launch_exits_without_spawn(
    cold_launch: tuple[Path, dict[str, object]],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shim, captured = cold_launch
    _write_claude_shim(shim, "2.1.90")

    with pytest.raises(SystemExit, match="1"):
        _run_interactive_session(
            system_prompt="cold launch",
            extra_env={"PATH": str(tmp_path)},
            project_dir=tmp_path,
            backend=ClaudeCodeBackend(),
        )

    assert "requires Claude Code" in capsys.readouterr().err
    assert captured["spawn_count"] == 0


def test_executable_identity_drift_exits_without_spawn(
    cold_launch: tuple[Path, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shim, captured = cold_launch
    _write_claude_shim(shim, "2.1.220")
    backend = ClaudeCodeBackend()
    real_ensure_pre_launch = ClaudeCodeBackend.ensure_pre_launch

    def probe_then_replace(self, **kwargs):  # type: ignore[no-untyped-def]
        readiness = real_ensure_pre_launch(self, **kwargs)
        atomic_write(shim, "#!/bin/sh\nexit 9\n")
        shim.chmod(0o755)
        return readiness

    monkeypatch.setattr(ClaudeCodeBackend, "ensure_pre_launch", probe_then_replace)

    with pytest.raises(SystemExit, match="1"):
        _run_interactive_session(
            system_prompt="cold launch",
            extra_env={"PATH": str(tmp_path)},
            project_dir=tmp_path,
            backend=backend,
        )

    assert (
        "interactive executable identity changed between probe and launch preparation"
        in capsys.readouterr().err
    )
    assert captured["spawn_count"] == 0


def test_unmanaged_launch_rejects_executable_drift_before_spawn(
    cold_launch: tuple[Path, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shim, captured = cold_launch
    _write_claude_shim(shim, "2.1.220")
    monkeypatch.setattr(
        "autoskillit.cli.session._session_launch.executable_binding_matches_current_file",
        lambda _binding: False,
    )

    with pytest.raises(SystemExit, match="1"):
        _run_interactive_session(
            system_prompt="cold launch",
            extra_env={"PATH": str(tmp_path)},
            project_dir=tmp_path,
            backend=ClaudeCodeBackend(),
        )

    assert "interactive executable changed after capability probing" in capsys.readouterr().err
    assert captured["spawn_count"] == 0
