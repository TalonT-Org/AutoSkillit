"""Real Claude probe behavior across cook reload attempts."""

from __future__ import annotations

from pathlib import Path

import pytest

import autoskillit.cli.session._session_reload as _patch_session__session_reload
from autoskillit import cli
from autoskillit.core import atomic_write
from autoskillit.execution.backends import ClaudeCodeBackend
from tests._realistic_project import PINNED_CLAUDE_SHIM_VERSION_OUTPUT
from tests.cli._cook_launch_helpers import arrange_cook

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def test_reload_reprobes_exact_executable_for_each_attempt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    probe_log = tmp_path / "probes"
    shim = tmp_path / "claude"
    atomic_write(
        shim,
        "#!/bin/sh\n"
        'if [ "${1-}" = "--version" ]; then\n'
        f"  printf 'probe\\n' >> '{probe_log}'\n"
        f"  printf '%s\\n' '{PINNED_CLAUDE_SHIM_VERSION_OUTPUT}'\n"
        "fi\n"
        "exit 0\n",
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    captured = arrange_cook(monkeypatch, tmp_path)
    reloads = iter(("session-2", None))
    monkeypatch.setattr(
        _patch_session__session_reload,
        "consume_reload_sentinel",
        lambda _project: next(reloads),
    )

    cli.cook(backend=ClaudeCodeBackend())

    assert len(captured) == 2
    assert probe_log.read_text(encoding="utf-8") == "probe\nprobe\n"
