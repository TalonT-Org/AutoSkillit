from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import RestoreSession, ValidatedAddDir
from autoskillit.execution.backends.codex import CodexBackend

pytestmark = [pytest.mark.small]

_GENERATED_HOME = Path("/workspace/codex-home")


class TestCodexInteractiveLaunch:
    def test_launch_command_correct_binary(self) -> None:
        spec = CodexBackend().build_interactive_cmd(generated_home=_GENERATED_HOME)
        assert spec.cmd[0] == "codex"

    def test_resume_command_is_subcommand(self) -> None:
        spec = CodexBackend().build_interactive_cmd(
            launch=RestoreSession(session_id="sess-1"),
            generated_home=_GENERATED_HOME,
        )
        assert "resume" in spec.cmd
        assert "--resume" not in spec.cmd

    def test_add_dirs_passed_through(self) -> None:
        dirs = [ValidatedAddDir(path="/workspace/a"), ValidatedAddDir(path="/workspace/b")]
        spec = CodexBackend().build_interactive_cmd(add_dirs=dirs, generated_home=_GENERATED_HOME)
        indices = [i for i, v in enumerate(spec.cmd) if v == "--add-dir"]
        assert len(indices) == 2
        assert spec.cmd[indices[0] + 1] == "/workspace/a"
        assert spec.cmd[indices[1] + 1] == "/workspace/b"
