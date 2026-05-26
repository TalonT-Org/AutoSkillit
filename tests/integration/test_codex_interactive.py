from __future__ import annotations

import pytest

from autoskillit.core import NamedResume, ValidatedAddDir
from autoskillit.execution.backends.codex import CodexBackend

pytestmark = [pytest.mark.small]


class TestCodexInteractiveLaunch:
    def test_launch_command_correct_binary(self) -> None:
        spec = CodexBackend().build_interactive_cmd()
        assert spec.cmd[0] == "codex"

    def test_resume_command_is_subcommand(self) -> None:
        spec = CodexBackend().build_interactive_cmd(
            resume_spec=NamedResume(session_id="sess-1"),
        )
        assert "resume" in spec.cmd
        assert "--resume" not in spec.cmd

    def test_add_dirs_passed_through(self) -> None:
        dirs = [ValidatedAddDir(path="/workspace/a"), ValidatedAddDir(path="/workspace/b")]
        spec = CodexBackend().build_interactive_cmd(add_dirs=dirs)
        indices = [i for i, v in enumerate(spec.cmd) if v == "--add-dir"]
        assert len(indices) == 2
        assert spec.cmd[indices[0] + 1] == "/workspace/a"
        assert spec.cmd[indices[1] + 1] == "/workspace/b"
