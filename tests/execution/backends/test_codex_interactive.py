from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import BareResume, CmdSpec, NamedResume, NoResume
from autoskillit.execution.backends.codex import CodexBackend, CodexFlags

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestCodexInteractiveCmdBaseStructure:
    def test_no_resume_base_command(self) -> None:
        spec = CodexBackend().build_interactive_cmd(resume_spec=NoResume())
        assert spec.cmd[0] == "codex"
        assert CodexFlags.DANGEROUSLY_BYPASS in spec.cmd
        assert "resume" not in spec.cmd

    @pytest.mark.parametrize(
        "resume_spec",
        [NoResume(), BareResume(), NamedResume(session_id="s1")],
        ids=["NoResume", "BareResume", "NamedResume"],
    )
    def test_returns_cmd_spec_with_tuple(self, resume_spec) -> None:
        spec = CodexBackend().build_interactive_cmd(resume_spec=resume_spec)
        assert isinstance(spec, CmdSpec)
        assert isinstance(spec.cmd, tuple)


class TestCodexInteractiveCmdResumeVariants:
    def test_no_resume_excludes_resume_subcommand(self) -> None:
        spec = CodexBackend().build_interactive_cmd(resume_spec=NoResume())
        assert CodexFlags.RESUME_SUBCOMMAND not in spec.cmd

    def test_named_resume_includes_resume_with_session_id(self) -> None:
        spec = CodexBackend().build_interactive_cmd(
            resume_spec=NamedResume(session_id="abc123"),
        )
        assert CodexFlags.RESUME_SUBCOMMAND in spec.cmd
        idx = list(spec.cmd).index(CodexFlags.RESUME_SUBCOMMAND)
        assert spec.cmd[idx + 1] == "abc123"

    def test_bare_resume_includes_resume_without_session_id(self) -> None:
        spec = CodexBackend().build_interactive_cmd(resume_spec=BareResume())
        assert CodexFlags.RESUME_SUBCOMMAND in spec.cmd
        idx = list(spec.cmd).index(CodexFlags.RESUME_SUBCOMMAND)
        assert spec.cmd[idx + 1] == CodexFlags.DANGEROUSLY_BYPASS


class TestCodexInteractiveCmdModelFlag:
    def test_model_kwarg_produces_model_flag_pair(self) -> None:
        spec = CodexBackend().build_interactive_cmd(model="o3-pro")
        idx = list(spec.cmd).index(CodexFlags.MODEL)
        assert spec.cmd[idx + 1] == "o3-pro"

    def test_no_model_kwarg_excludes_model_flag(self) -> None:
        spec = CodexBackend().build_interactive_cmd()
        assert "--model" not in spec.cmd


class TestCodexInteractiveCmdSystemPrompt:
    def test_system_prompt_with_no_resume_produces_config_override(self) -> None:
        spec = CodexBackend().build_interactive_cmd(
            system_prompt="do stuff",
            resume_spec=NoResume(),
        )
        assert CodexFlags.CONFIG_OVERRIDE in spec.cmd
        idx = list(spec.cmd).index(CodexFlags.CONFIG_OVERRIDE)
        assert spec.cmd[idx + 1] == "developer_instructions=do stuff"

    def test_system_prompt_with_named_resume_suppressed(self) -> None:
        spec = CodexBackend().build_interactive_cmd(
            system_prompt="do stuff",
            resume_spec=NamedResume(session_id="s1"),
        )
        assert CodexFlags.CONFIG_OVERRIDE not in spec.cmd

    def test_system_prompt_with_bare_resume_suppressed(self) -> None:
        spec = CodexBackend().build_interactive_cmd(
            system_prompt="do stuff",
            resume_spec=BareResume(),
        )
        assert CodexFlags.CONFIG_OVERRIDE not in spec.cmd

    def test_no_system_prompt_with_no_resume_excludes_config_override(self) -> None:
        spec = CodexBackend().build_interactive_cmd(resume_spec=NoResume())
        assert CodexFlags.CONFIG_OVERRIDE not in spec.cmd


class TestCodexInteractiveCmdAddDirs:
    def test_single_dir_produces_add_dir_pair(self) -> None:
        spec = CodexBackend().build_interactive_cmd(add_dirs=[Path("/a")])
        idx = list(spec.cmd).index(CodexFlags.ADD_DIR)
        assert spec.cmd[idx + 1] == "/a"

    def test_two_dirs_produce_two_add_dir_pairs_in_order(self) -> None:
        spec = CodexBackend().build_interactive_cmd(
            add_dirs=[Path("/a"), Path("/b")],
        )
        indices = [i for i, v in enumerate(spec.cmd) if v == CodexFlags.ADD_DIR]
        assert len(indices) == 2
        assert spec.cmd[indices[0] + 1] == "/a"
        assert spec.cmd[indices[1] + 1] == "/b"

    def test_empty_list_excludes_add_dir(self) -> None:
        spec = CodexBackend().build_interactive_cmd(add_dirs=[])
        assert CodexFlags.ADD_DIR not in spec.cmd
