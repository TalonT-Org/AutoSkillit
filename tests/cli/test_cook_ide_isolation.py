"""End-to-end regression canary: _launch_cook_session under simulated IDE state.

This test encodes the exact scenario the ide-env-leak investigation reproduced:
VS Code (or any IDE) has ``CLAUDE_CODE_SSE_PORT`` set and an active
``~/.claude/ide/$PORT.lock`` file. When autoskillit launches the cook session,
the child must NOT attach to the IDE channel via either discovery path:

1. **Env scrub** — ``CLAUDE_CODE_SSE_PORT`` and the expanded IDE denylist are
   stripped from the child env.
2. **Auto-connect disable** — ``CLAUDE_CODE_AUTO_CONNECT_IDE=0`` is injected,
   which suppresses the ``~/.claude/ide/*.lock`` scan fallback that fires even
   when no IDE env vars are set.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def test_cook_session_ignores_ide_lock_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Launch cook under simulated IDE state; assert env scrub + auto-connect disable."""
    fake_home = tmp_path / "home"
    ide_dir = fake_home / ".claude" / "ide"
    ide_dir.mkdir(parents=True)
    lock_file = ide_dir / "65535.lock"
    lock_file.write_text('{"pid": 1, "transport": "ws"}')

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("CLAUDE_CODE_SSE_PORT", "65535")
    monkeypatch.setenv("ENABLE_IDE_INTEGRATION", "1")
    monkeypatch.setenv("VSCODE_GIT_ASKPASS_MAIN", "/fake/vscode")
    monkeypatch.setenv("CLAUDE_CODE_IDE_HOST_OVERRIDE", "localhost")

    from autoskillit.cli.session._session_launch import _launch_cook_session

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("autoskillit.cli._init_helpers._is_plugin_installed", return_value=False),
        patch(
            "autoskillit.cli.session._session_launch.subprocess.run",
            return_value=MagicMock(returncode=0),
        ) as mock_run,
    ):
        _launch_cook_session("system prompt", initial_message="hello")

    # (1) Env scrub
    env = mock_run.call_args.kwargs["env"]
    assert "CLAUDE_CODE_SSE_PORT" not in env
    assert "ENABLE_IDE_INTEGRATION" not in env
    assert "VSCODE_GIT_ASKPASS_MAIN" not in env
    assert "CLAUDE_CODE_IDE_HOST_OVERRIDE" not in env

    # (2) Auto-connect suppressor
    assert env["CLAUDE_CODE_AUTO_CONNECT_IDE"] == "0"

    # Argv must no longer carry a leading ['env', ...] prefix.
    cmd = mock_run.call_args.args[0]
    assert cmd[0] == "claude"
