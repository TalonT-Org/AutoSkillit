from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

import pytest

from autoskillit.core import (
    OUTPUT_DISCIPLINE_DIGEST,
    BareResume,
    CmdSpec,
    NamedResume,
    NoResume,
)
from autoskillit.execution.backends.codex import CodexBackend, CodexFlags

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _developer_instructions(spec: CmdSpec) -> str | None:
    overrides = [
        spec.cmd[i + 1]
        for i, value in enumerate(spec.cmd[:-1])
        if value == CodexFlags.CONFIG_OVERRIDE
    ]
    rendered = next(
        (
            value.partition("=")[2]
            for value in overrides
            if value.startswith("developer_instructions=")
        ),
        None,
    )
    if rendered is None:
        return None
    return tomllib.loads(f"developer_instructions = {rendered}")["developer_instructions"]


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
        assert "abc123" in spec.cmd
        assert spec.origin is not None
        assert "abc123" in spec.origin.positional

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
        assert _developer_instructions(spec) == f"do stuff\n\n{OUTPUT_DISCIPLINE_DIGEST}"
        overrides = [
            spec.cmd[i + 1] for i, v in enumerate(spec.cmd[:-1]) if v == CodexFlags.CONFIG_OVERRIDE
        ]
        assert "features.image_generation=false" in overrides

    def test_system_prompt_with_named_resume_suppressed(self) -> None:
        spec = CodexBackend().build_interactive_cmd(
            system_prompt="do stuff",
            resume_spec=NamedResume(session_id="s1"),
        )
        overrides = [
            spec.cmd[i + 1] for i, v in enumerate(spec.cmd[:-1]) if v == CodexFlags.CONFIG_OVERRIDE
        ]
        assert not any(v.startswith("developer_instructions=") for v in overrides)
        assert "features.image_generation=false" in overrides

    def test_system_prompt_with_bare_resume_suppressed(self) -> None:
        spec = CodexBackend().build_interactive_cmd(
            system_prompt="do stuff",
            resume_spec=BareResume(),
        )
        overrides = [
            spec.cmd[i + 1] for i, v in enumerate(spec.cmd[:-1]) if v == CodexFlags.CONFIG_OVERRIDE
        ]
        assert not any(v.startswith("developer_instructions=") for v in overrides)
        assert "features.image_generation=false" in overrides

    def test_no_system_prompt_with_no_resume_excludes_config_override(self) -> None:
        spec = CodexBackend().build_interactive_cmd(resume_spec=NoResume())
        overrides = [
            spec.cmd[i + 1] for i, v in enumerate(spec.cmd[:-1]) if v == CodexFlags.CONFIG_OVERRIDE
        ]
        assert _developer_instructions(spec) == OUTPUT_DISCIPLINE_DIGEST
        assert "features.image_generation=false" in overrides

    def test_system_prompt_override_is_valid_toml_with_quotes_and_newlines(self) -> None:
        caller_prompt = 'line one "quoted"\nline two \\ path'
        spec = CodexBackend().build_interactive_cmd(
            system_prompt=caller_prompt,
            resume_spec=NoResume(),
        )
        assert _developer_instructions(spec) == (f"{caller_prompt}\n\n{OUTPUT_DISCIPLINE_DIGEST}")

    def test_installed_codex_parses_exact_fresh_config_overrides(self, tmp_path: Path) -> None:
        binary = shutil.which("codex")
        if binary is None:
            pytest.skip("installed Codex CLI is absent")

        caller_prompt = (
            'caller """ prompt = "quoted"\n[features]\npath = C:\\temp\\$HOME\n# literal text'
        )
        spec = CodexBackend().build_interactive_cmd(
            system_prompt=caller_prompt,
            resume_spec=NoResume(),
        )
        config_pairs: list[str] = []
        for index, value in enumerate(spec.cmd[:-1]):
            if value == CodexFlags.CONFIG_OVERRIDE:
                config_pairs.extend(spec.cmd[index : index + 2])

        overrides = config_pairs[1::2]
        assert len(config_pairs) == 4
        assert len(overrides) == 2
        assert {value.partition("=")[0] for value in overrides} == {
            "developer_instructions",
            "features.image_generation",
        }
        assert caller_prompt in _developer_instructions(spec)

        project_temp = Path(__file__).resolve().parents[3] / ".autoskillit" / "temp"
        project_temp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="codex-config-parse-", dir=project_temp
        ) as codex_home:
            env = dict(os.environ)
            env["CODEX_HOME"] = codex_home
            result = subprocess.run(  # noqa: S603
                [binary, *config_pairs, "doctor", "--json"],
                cwd=tmp_path,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )

        assert result.stdout, result.stderr
        config_check = json.loads(result.stdout)["checks"]["config.load"]
        assert config_check["status"] == "ok", config_check


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
        assert "CODEX_HOME" not in spec.env


class TestCodexInteractiveCmdCodexHome:
    def test_add_dirs_injects_codex_home_env(self) -> None:
        spec = CodexBackend().build_interactive_cmd(add_dirs=[Path("/session/dir")])
        assert spec.env["CODEX_HOME"] == "/session/dir"

    def test_codex_home_uses_first_add_dir(self) -> None:
        spec = CodexBackend().build_interactive_cmd(
            add_dirs=[Path("/first"), Path("/second")],
        )
        assert spec.env["CODEX_HOME"] == "/first"

    def test_empty_add_dirs_excludes_codex_home(self) -> None:
        spec = CodexBackend().build_interactive_cmd(add_dirs=[])
        assert "CODEX_HOME" not in spec.env

    def test_caller_env_extras_takes_precedence(self) -> None:
        spec = CodexBackend().build_interactive_cmd(
            add_dirs=[Path("/session/dir")],
            env_extras={"CODEX_HOME": "/override"},
        )
        assert spec.env["CODEX_HOME"] == "/override"


class TestCodexInteractiveCmdPositionalOrdering:
    def test_codex_initial_prompt_precedes_add_dir(self) -> None:
        result = CodexBackend().build_interactive_cmd(
            initial_prompt="hello",
            add_dirs=[Path("/tmp/a")],
        )
        prompt_idx = list(result.cmd).index("hello")
        add_dir_idx = list(result.cmd).index(CodexFlags.ADD_DIR)
        assert prompt_idx < add_dir_idx
