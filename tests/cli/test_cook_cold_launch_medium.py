"""Cold Claude cook attempt transaction tests."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import pytest

from autoskillit import cli
from autoskillit.config import AutomationConfig
from autoskillit.core import AUTOSKILLIT_ATTESTED_META_SUPPORT, CookSessionHandle, atomic_write
from autoskillit.execution.backends import ClaudeCodeBackend, CodexBackend
from tests.cli._cook_launch_helpers import arrange_cook

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def _write_shim(path: Path, probe_log: Path) -> None:
    atomic_write(
        path,
        "#!/bin/sh\n"
        'if [ "${1-}" = "--version" ]; then\n'
        '  if [ -n "${ANTHROPIC_API_KEY-}" ]; then exit 73; fi\n'
        f"  printf 'probe\\n' >> '{probe_log}'\n"
        "  printf '%s\\n' '2.1.220 (Claude Code)'\n"
        "fi\n"
        "exit 0\n",
    )
    path.chmod(0o755)


def test_cook_probes_without_provider_secret_then_spawns_with_attestation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    shim = tmp_path / "claude"
    probe_log = tmp_path / "probes"
    _write_shim(shim, probe_log)
    monkeypatch.setenv("PATH", str(tmp_path))
    config = AutomationConfig(features={"providers": True}, experimental_enabled=True)
    config.providers.profiles = {"anthropic": {"ANTHROPIC_API_KEY": "session-secret"}}
    captured = arrange_cook(monkeypatch, tmp_path, config=config)
    monkeypatch.setattr(
        "autoskillit.cli.session._session_reload.consume_reload_sentinel",
        lambda _project: None,
    )

    cli.cook(profile="anthropic", backend=ClaudeCodeBackend())

    assert probe_log.read_text(encoding="utf-8") == "probe\n"
    assert len(captured) == 1
    spec = captured[0]
    assert spec.env[AUTOSKILLIT_ATTESTED_META_SUPPORT] == "1"
    assert spec.env["ANTHROPIC_API_KEY"] == "session-secret"


def test_cook_executable_drift_aborts_before_spawn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shim = tmp_path / "claude"
    _write_shim(shim, tmp_path / "probes")
    monkeypatch.setenv("PATH", str(tmp_path))
    captured = arrange_cook(monkeypatch, tmp_path)
    backend = ClaudeCodeBackend()
    real_probe = ClaudeCodeBackend.ensure_pre_launch

    def probe_then_replace(self, **kwargs):  # type: ignore[no-untyped-def]
        readiness = real_probe(self, **kwargs)
        atomic_write(shim, "#!/bin/sh\nexit 9\n")
        shim.chmod(0o755)
        return readiness

    monkeypatch.setattr(ClaudeCodeBackend, "ensure_pre_launch", probe_then_replace)

    with pytest.raises(SystemExit, match="1"):
        cli.cook(backend=backend)

    assert "identity changed between probe and launch preparation" in capsys.readouterr().err
    assert captured == []


def test_cook_rejects_executable_drift_before_spawn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shim = tmp_path / "claude"
    _write_shim(shim, tmp_path / "probes")
    monkeypatch.setenv("PATH", str(tmp_path))
    captured = arrange_cook(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "autoskillit.cli.session._session_cook.executable_binding_matches_current_file",
        lambda _binding: False,
    )

    with pytest.raises(SystemExit, match="1"):
        cli.cook(backend=ClaudeCodeBackend())

    assert capsys.readouterr().err == (
        "ERROR: interactive executable changed after capability probing\n"
    )
    assert captured == []


def test_codex_cook_does_not_resolve_or_run_prelaunch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    codex = tmp_path / "codex"
    atomic_write(codex, "#!/bin/sh\nexit 0\n")
    codex.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    captured = arrange_cook(monkeypatch, tmp_path)
    backend = CodexBackend()
    monkeypatch.setattr(
        "autoskillit.cli.session._session_launch.resolve_executable_launch_binding",
        lambda **_kwargs: pytest.fail("Codex cook must not resolve an executable binding"),
    )
    monkeypatch.setattr(
        CodexBackend,
        "ensure_pre_launch",
        lambda _self, **_kwargs: pytest.fail("Codex cook must not run prelaunch"),
    )
    monkeypatch.setattr(
        CodexBackend,
        "cook_session_context",
        lambda _self, **_kwargs: nullcontext(
            CookSessionHandle(
                view_id="codex",
                pass_fds=(),
                _record_spawn=lambda _pid, _pgid: None,
                _record_reaped=lambda _pid, _pgid: None,
            )
        ),
    )
    monkeypatch.setattr(
        CodexBackend,
        "validate_interactive_invocation",
        lambda _self, _spec: [],
    )
    monkeypatch.setattr(
        "autoskillit.cli.session._session_reload.consume_reload_sentinel",
        lambda _project: None,
    )

    cli.cook(backend=backend)

    assert len(captured) == 1
    assert captured[0].cmd[0] == "codex"
