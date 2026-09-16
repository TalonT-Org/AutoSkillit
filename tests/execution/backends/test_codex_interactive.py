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
    CmdOrigin,
    CmdSpec,
    FreshLaunch,
    PositionalRole,
    RestoreSession,
    ResumeWithBriefing,
)
from autoskillit.execution.backends._claude_prompt import codex_discipline_suffix
from autoskillit.execution.backends.codex import CodexBackend as _CodexBackend
from autoskillit.execution.backends.codex import CodexFlags
from tests.execution.backends._generated_home_backend import (
    GeneratedHomeCodexBackend,
    bind_generated_home_backend,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

CodexBackend = GeneratedHomeCodexBackend


@pytest.fixture(autouse=True)
def _bind_generated_home_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_generated_home_backend(tmp_path, monkeypatch)


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
    def test_requires_and_pins_a_canonical_generated_home(self, tmp_path: Path) -> None:
        backend = _CodexBackend()
        with pytest.raises(ValueError, match="generated_home is required"):
            backend.build_interactive_cmd()

        generated_home = tmp_path / "generated-home"
        spec = backend.build_interactive_cmd(generated_home=generated_home)

        assert spec.env["CODEX_HOME"] == str(generated_home)
        assert spec.env["CODEX_SQLITE_HOME"] == str(generated_home)
        assert spec.origin is not None
        assert (
            CodexFlags.CONFIG_OVERRIDE,
            f'sqlite_home="{generated_home}"',
        ) in spec.origin.variadic_pairs

    def test_fresh_base_command(self) -> None:
        spec = CodexBackend().build_interactive_cmd(launch=FreshLaunch())
        assert spec.cmd[0] == "codex"
        assert CodexFlags.DANGEROUSLY_BYPASS in spec.cmd
        assert "resume" not in spec.cmd

    @pytest.mark.parametrize(
        "launch",
        [
            FreshLaunch(),
            RestoreSession(session_id="s1"),
            ResumeWithBriefing(session_id="s1", briefing="continue"),
        ],
        ids=["fresh", "restore", "resume_with_briefing"],
    )
    def test_returns_cmd_spec_with_tuple(self, launch) -> None:
        spec = CodexBackend().build_interactive_cmd(launch=launch)
        assert isinstance(spec, CmdSpec)
        assert isinstance(spec.cmd, tuple)


class TestCodexInteractiveCmdResumeVariants:
    def test_fresh_launch_excludes_resume_subcommand(self) -> None:
        spec = CodexBackend().build_interactive_cmd(launch=FreshLaunch())
        assert CodexFlags.RESUME_SUBCOMMAND not in spec.cmd

    def test_resume_with_briefing_includes_resume_session_and_briefing(self) -> None:
        spec = CodexBackend().build_interactive_cmd(
            launch=ResumeWithBriefing(session_id="abc123", briefing="continue"),
            model="gpt-5.6-sol",
            generated_home=Path("/session/home"),
            add_dirs=[Path("/first"), Path("/second")],
            env_extras={"AUTOSKILLIT_PROVIDER_PROFILE": "test-profile"},
        )
        assert spec.cmd == (
            "codex",
            CodexFlags.RESUME_SUBCOMMAND,
            CodexFlags.DANGEROUSLY_BYPASS,
            CodexFlags.PROFILE,
            "test-profile",
            CodexFlags.MODEL,
            "gpt-5.6-sol",
            "abc123",
            "continue",
            CodexFlags.CONFIG_OVERRIDE,
            "features.image_generation=false",
            CodexFlags.CONFIG_OVERRIDE,
            'sqlite_home="/session/home"',
            CodexFlags.ADD_DIR,
            "/first",
            CodexFlags.ADD_DIR,
            "/second",
        )
        assert spec.origin == CmdOrigin(
            binary="codex",
            mode_flags=(
                CodexFlags.RESUME_SUBCOMMAND,
                CodexFlags.DANGEROUSLY_BYPASS,
            ),
            kv_flags=(
                (CodexFlags.PROFILE, "test-profile"),
                (CodexFlags.MODEL, "gpt-5.6-sol"),
            ),
            positional=(
                (PositionalRole.RESUME_TARGET, "abc123"),
                (PositionalRole.PROMPT, "continue"),
            ),
            variadic_pairs=(
                (CodexFlags.CONFIG_OVERRIDE, "features.image_generation=false"),
                (CodexFlags.CONFIG_OVERRIDE, 'sqlite_home="/session/home"'),
                (CodexFlags.ADD_DIR, "/first"),
                (CodexFlags.ADD_DIR, "/second"),
            ),
        )

    def test_restore_session_includes_resume_with_no_prompt(self) -> None:
        spec = CodexBackend().build_interactive_cmd(launch=RestoreSession(session_id="abc123"))
        assert CodexFlags.RESUME_SUBCOMMAND in spec.cmd
        idx = list(spec.cmd).index(CodexFlags.RESUME_SUBCOMMAND)
        assert spec.cmd[idx + 1] == CodexFlags.DANGEROUSLY_BYPASS
        resume_target_idx = list(spec.cmd).index("abc123")
        config_override_idx = list(spec.cmd).index(CodexFlags.CONFIG_OVERRIDE)
        assert resume_target_idx < config_override_idx
        assert spec.cmd[-1] != "abc123"
        assert spec.origin is not None
        assert spec.origin.positional == ((PositionalRole.RESUME_TARGET, "abc123"),)


class TestCodexInteractiveCmdModelFlag:
    def test_model_kwarg_produces_model_flag_pair(self) -> None:
        spec = CodexBackend().build_interactive_cmd(model="o3-pro")
        idx = list(spec.cmd).index(CodexFlags.MODEL)
        assert spec.cmd[idx + 1] == "o3-pro"

    def test_no_model_kwarg_excludes_model_flag(self) -> None:
        spec = CodexBackend().build_interactive_cmd()
        assert "--model" not in spec.cmd


class TestCodexInteractiveCmdSystemPrompt:
    def test_fresh_system_prompt_produces_config_override(self) -> None:
        spec = CodexBackend().build_interactive_cmd(
            launch=FreshLaunch(system_prompt="do stuff"),
        )
        assert _developer_instructions(spec) == (
            f"do stuff\n\n{codex_discipline_suffix(include_scope=True)}"
        )
        overrides = [
            spec.cmd[i + 1] for i, v in enumerate(spec.cmd[:-1]) if v == CodexFlags.CONFIG_OVERRIDE
        ]
        assert "features.image_generation=false" in overrides

    @pytest.mark.parametrize(
        "launch",
        [
            RestoreSession(session_id="s1"),
            ResumeWithBriefing(session_id="s1", briefing="continue"),
        ],
        ids=["restore", "resume_with_briefing"],
    )
    def test_resume_variants_do_not_emit_fresh_developer_instructions(self, launch) -> None:
        spec = CodexBackend().build_interactive_cmd(launch=launch)
        overrides = [
            spec.cmd[i + 1] for i, v in enumerate(spec.cmd[:-1]) if v == CodexFlags.CONFIG_OVERRIDE
        ]
        assert not any(v.startswith("developer_instructions=") for v in overrides)
        assert "features.image_generation=false" in overrides

    def test_fresh_launch_without_system_prompt_uses_scope_discipline(self) -> None:
        spec = CodexBackend().build_interactive_cmd(launch=FreshLaunch())
        overrides = [
            spec.cmd[i + 1] for i, v in enumerate(spec.cmd[:-1]) if v == CodexFlags.CONFIG_OVERRIDE
        ]
        assert _developer_instructions(spec) == codex_discipline_suffix(include_scope=True)
        assert "features.image_generation=false" in overrides

    def test_system_prompt_override_is_valid_toml_with_quotes_and_newlines(self) -> None:
        caller_prompt = 'line one "quoted"\nline two \\ path'
        spec = CodexBackend().build_interactive_cmd(
            launch=FreshLaunch(system_prompt=caller_prompt),
        )
        assert _developer_instructions(spec) == (
            f"{caller_prompt}\n\n{codex_discipline_suffix(include_scope=True)}"
        )

    def test_installed_codex_parses_exact_fresh_config_overrides(self, tmp_path: Path) -> None:
        binary = shutil.which("codex")
        if binary is None:
            pytest.skip("installed Codex CLI is absent")

        caller_prompt = (
            'caller """ prompt = "quoted"\n[features]\npath = C:\\temp\\$HOME\n# literal text'
        )
        spec = CodexBackend().build_interactive_cmd(
            launch=FreshLaunch(system_prompt=caller_prompt),
        )
        config_pairs: list[str] = []
        for index, value in enumerate(spec.cmd[:-1]):
            if value == CodexFlags.CONFIG_OVERRIDE:
                config_pairs.extend(spec.cmd[index : index + 2])

        overrides = config_pairs[1::2]
        assert len(config_pairs) == 6
        assert len(overrides) == 3
        assert {value.partition("=")[0] for value in overrides} == {
            "developer_instructions",
            "features.image_generation",
            "sqlite_home",
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
        assert spec.env["CODEX_HOME"] == str(CodexBackend._fixture_home())


class TestCodexInteractiveCmdCodexHome:
    def test_generated_home_injects_reserved_home_env(self) -> None:
        spec = CodexBackend().build_interactive_cmd(
            add_dirs=[Path("/session/add-dir")],
            generated_home=Path("/session/home"),
        )
        assert spec.env["CODEX_HOME"] == "/session/home"
        assert spec.env["CODEX_SQLITE_HOME"] == "/session/home"

    def test_add_dirs_do_not_replace_generated_home(self) -> None:
        spec = CodexBackend().build_interactive_cmd(
            add_dirs=[Path("/first"), Path("/second")],
        )
        expected = str(CodexBackend._fixture_home())
        assert spec.env["CODEX_HOME"] == expected
        assert spec.env["CODEX_SQLITE_HOME"] == expected

    def test_empty_add_dirs_keeps_generated_home(self) -> None:
        spec = CodexBackend().build_interactive_cmd(add_dirs=[])
        assert spec.env["CODEX_HOME"] == str(CodexBackend._fixture_home())

    def test_generated_home_takes_precedence_over_caller_env_extras(self) -> None:
        spec = CodexBackend().build_interactive_cmd(
            add_dirs=[Path("/session/add-dir")],
            generated_home=Path("/session/home"),
            env_extras={
                "CODEX_HOME": "/override",
                "CODEX_SQLITE_HOME": "/other-override",
            },
        )
        assert spec.env["CODEX_HOME"] == "/session/home"
        assert spec.env["CODEX_SQLITE_HOME"] == "/session/home"


class TestCodexInteractiveCmdPositionalOrdering:
    def test_codex_initial_prompt_precedes_add_dir(self) -> None:
        result = CodexBackend().build_interactive_cmd(
            launch=FreshLaunch(initial_prompt="hello"),
            add_dirs=[Path("/tmp/a")],
        )
        prompt_idx = list(result.cmd).index("hello")
        add_dir_idx = list(result.cmd).index(CodexFlags.ADD_DIR)
        assert prompt_idx < add_dir_idx
