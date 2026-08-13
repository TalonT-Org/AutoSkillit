"""Real Claude probe behavior across cook reload attempts."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit import cli
from autoskillit.core import atomic_write
from autoskillit.execution.backends import ClaudeCodeBackend
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
        "  printf '%s\\n' '2.1.220 (Claude Code)'\n"
        "fi\n"
        "exit 0\n",
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    captured = arrange_cook(monkeypatch, tmp_path)
    reloads = iter(("session-2", None))
    monkeypatch.setattr(
        "autoskillit.cli.session._session_reload.consume_reload_sentinel",
        lambda _project: next(reloads),
    )

    cli.cook(backend=ClaudeCodeBackend())

    assert len(captured) == 2
    assert probe_log.read_text(encoding="utf-8") == "probe\nprobe\n"
