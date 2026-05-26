from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import pytest

from autoskillit.core import (
    AGENT_BACKEND_CODEX,
    SESSION_TYPE_SKILL,
    BackendCapabilities,
    CmdSpec,
    CodingAgentBackend,
    DirectInstall,
    EnvPolicy,
    OutputFormat,
    ResultParser,
    SessionCheckpoint,
    SessionLocator,
    SkillSessionConfig,
    StreamParser,
    ValidatedAddDir,
)
from autoskillit.execution.backends.codex import (
    CodexBackend,
    CodexEnvPolicy,
    CodexFlags,
    CodexResultParser,
    CodexSessionLocator,
    CodexStreamParser,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestCodexFlags:
    def test_is_str_enum(self) -> None:
        assert issubclass(CodexFlags, StrEnum)

    def test_str_json_equals_double_dash_json(self) -> None:
        assert str(CodexFlags.JSON) == "--json"

    def test_all_members_present(self) -> None:
        expected = {
            "JSON",
            "SANDBOX",
            "ASK_FOR_APPROVAL",
            "ASK_FOR_APPROVAL_SHORT",
            "MODEL",
            "MODEL_SHORT",
            "ADD_DIR",
            "IGNORE_USER_CONFIG",
            "EPHEMERAL",
            "RESUME_SUBCOMMAND",
            "LAST",
            "CONFIG_OVERRIDE",
            "DANGEROUSLY_BYPASS",
        }
        actual = {m.name for m in CodexFlags}
        assert actual == expected
        assert len(set(CodexFlags)) == len(expected)


class TestCodexBackend:
    def test_isinstance_coding_agent_backend(self) -> None:
        assert isinstance(CodexBackend(), CodingAgentBackend)

    def test_name_property(self) -> None:
        assert CodexBackend().name == AGENT_BACKEND_CODEX

    def test_capabilities_channel_b_false(self) -> None:
        assert CodexBackend().capabilities.channel_b_capable is False

    def test_capabilities_skill_injection_true(self) -> None:
        assert CodexBackend().capabilities.skill_injection_capable is True

    def test_capabilities_pty_required_false(self) -> None:
        assert CodexBackend().capabilities.pty_required is False

    def test_capabilities_session_resume_true(self) -> None:
        assert CodexBackend().capabilities.session_resume_capable is True

    def test_capabilities_supports_thinking_blocks_false(self) -> None:
        assert CodexBackend().capabilities.supports_thinking_blocks is False

    def test_capabilities_supports_claude_format_stdout_false(self) -> None:
        assert CodexBackend().capabilities.supports_claude_format_stdout is False

    def test_capabilities_exit_code_is_terminal_true(self) -> None:
        assert CodexBackend().capabilities.exit_code_is_terminal is True

    def test_capabilities_mcp_config_capable_true(self) -> None:
        assert CodexBackend().capabilities.mcp_config_capable is True

    def test_capabilities_completion_record_types(self) -> None:
        expected = frozenset({"turn.completed", "turn.failed", "error"})
        assert CodexBackend().capabilities.completion_record_types == expected

    def test_capabilities_session_record_types_empty(self) -> None:
        assert CodexBackend().capabilities.session_record_types == frozenset()

    def test_binary_name(self) -> None:
        assert CodexBackend().binary_name() == "codex"

    def test_version_cmd(self) -> None:
        assert CodexBackend().version_cmd() == ("codex", "--version")


class TestCodexBackendCommands:
    def test_build_headless_cmd_codex_at_0(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert spec.cmd[0] == "codex"

    def test_build_headless_cmd_exec_at_1(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert spec.cmd[1] == "exec"

    def test_build_headless_cmd_has_json_flag(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert "--json" in spec.cmd

    def test_build_headless_cmd_has_sandbox_flag(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert "--sandbox" in spec.cmd
        idx = spec.cmd.index("--sandbox")
        assert spec.cmd[idx + 1] == "workspace-write"

    def test_no_approval_flag_in_headless_cmd(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert "-a" not in spec.cmd

    def test_build_headless_cmd_prompt_is_last(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert spec.cmd[-1] == "do stuff"

    def test_build_headless_cmd_with_model(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff", model="o3")
        assert "--model" in spec.cmd
        idx = spec.cmd.index("--model")
        assert spec.cmd[idx + 1] == "o3"

    def test_build_headless_cmd_returns_cmd_spec(self) -> None:
        spec = CodexBackend().build_headless_cmd("x")
        assert isinstance(spec, CmdSpec)
        assert isinstance(spec.cmd, tuple)

    def test_build_headless_cmd_with_env_extras(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff", env_extras={"FOO": "bar"})
        assert spec.env.get("FOO") == "bar"

    def test_build_cmd_delegates_to_headless(self) -> None:
        backend = CodexBackend()
        spec = backend.build_cmd("do stuff", "/work")
        assert spec.cmd[0] == "codex"
        assert spec.cwd == "/work"

    def test_build_resume_cmd_with_session_id(self) -> None:
        spec = CodexBackend().build_resume_cmd(resume_session_id="sess-123", prompt="continue")
        assert spec.cmd[0] == "codex"
        assert spec.cmd[1] == "exec"
        assert "resume" in spec.cmd
        assert "sess-123" in spec.cmd
        assert spec.cmd[-1] == "continue"

    def test_build_resume_cmd_empty_id_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            CodexBackend().build_resume_cmd(resume_session_id="", prompt="continue")

    def test_build_resume_cmd_with_env_extras(self) -> None:
        spec = CodexBackend().build_resume_cmd(
            resume_session_id="s1", prompt="go", env_extras={"FOO": "bar"}
        )
        assert spec.env.get("FOO") == "bar"

    def test_build_resume_cmd_env_includes_os_environ(self) -> None:
        spec = CodexBackend().build_resume_cmd(resume_session_id="s1", prompt="go")
        assert "PATH" in spec.env

    def test_build_resume_cmd_has_json_flag(self) -> None:
        spec = CodexBackend().build_resume_cmd(resume_session_id="s1", prompt="go")
        assert "--json" in spec.cmd

    def test_build_interactive_cmd_returns_cmd_spec(self) -> None:
        spec = CodexBackend().build_interactive_cmd()
        assert isinstance(spec, CmdSpec)


class TestCodexBackendFactories:
    def test_stream_parser_returns_stream_parser(self) -> None:
        assert isinstance(CodexBackend().stream_parser(), StreamParser)

    def test_result_parser_returns_result_parser(self) -> None:
        assert isinstance(CodexBackend().result_parser(), ResultParser)

    def test_result_parser_is_codex_result_parser(self) -> None:
        assert isinstance(CodexBackend().result_parser(), CodexResultParser)

    def test_env_policy_returns_env_policy(self) -> None:
        assert isinstance(CodexBackend().env_policy(), EnvPolicy)

    def test_session_locator_returns_session_locator(self) -> None:
        locator = CodexBackend().session_locator()
        assert isinstance(locator, SessionLocator)

    def test_session_locator_is_codex_session_locator(self) -> None:
        locator = CodexBackend().session_locator()
        assert isinstance(locator, CodexSessionLocator)

    def test_write_tool_names_returns_frozenset(self) -> None:
        assert isinstance(CodexBackend().write_tool_names(), frozenset)

    def test_stream_parser_factory_passes_completion_marker(self) -> None:
        parser = CodexBackend().stream_parser(completion_marker="%%DONE%%")
        assert isinstance(parser, CodexStreamParser)
        assert parser.completion_marker == "%%DONE%%"

    def test_stream_parser_factory_default_empty_marker(self) -> None:
        parser = CodexBackend().stream_parser()
        assert isinstance(parser, CodexStreamParser)
        assert parser.completion_marker == ""


class TestCodexEnvPolicy:
    def test_build_env_preserves_non_denied_vars(self) -> None:
        policy = CodexEnvPolicy()
        result = policy.build_env({"PATH": "/usr/bin", "HOME": "/root"})
        assert result["PATH"] == "/usr/bin"
        assert result["HOME"] == "/root"


class TestCodexImportContract:
    def test_import_codex_flags_from_module(self) -> None:
        from autoskillit.execution.backends.codex import CodexFlags

        assert issubclass(CodexFlags, StrEnum)

    def test_import_codex_backend_from_package(self) -> None:
        from autoskillit.execution.backends import CodexBackend

        assert isinstance(CodexBackend(), CodingAgentBackend)

    def test_import_codex_backend_from_execution(self) -> None:
        from autoskillit.execution import CodexBackend

        assert isinstance(CodexBackend(), CodingAgentBackend)

    def test_codex_backend_not_in_core_types(self) -> None:
        from autoskillit.core.types import __all__ as core_all

        assert "CodexBackend" not in core_all


class TestCodexBackendProtocol:
    def test_isinstance_coding_agent_backend(self) -> None:
        assert isinstance(CodexBackend(), CodingAgentBackend)

    def test_binary_name_is_codex(self) -> None:
        assert CodexBackend().binary_name() == "codex"

    def test_capabilities_is_backend_capabilities(self) -> None:
        assert isinstance(CodexBackend().capabilities, BackendCapabilities)

    @pytest.mark.parametrize(
        ("attr", "expected"),
        [
            ("channel_b_capable", False),
            ("pty_required", False),
            ("session_resume_capable", True),
            ("skill_injection_capable", True),
            ("mcp_config_capable", True),
            ("food_truck_capable", False),
        ],
    )
    def test_capability_flag(self, attr: str, expected: bool) -> None:
        assert getattr(CodexBackend().capabilities, attr) is expected

    def test_completion_record_types_content(self) -> None:
        assert CodexBackend().capabilities.completion_record_types == frozenset(
            {"turn.completed", "turn.failed", "error"}
        )

    def test_session_record_types_empty(self) -> None:
        assert CodexBackend().capabilities.session_record_types == frozenset()


class TestCodexHeadlessCmd:
    def test_default_cmd_structure(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert spec.cmd == (
            "codex",
            "exec",
            "--json",
            "--sandbox",
            "workspace-write",
            "do stuff",
        )

    def test_sandbox_with_workspace_write(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert "--sandbox" in spec.cmd
        idx = spec.cmd.index("--sandbox")
        assert spec.cmd[idx + 1] == "workspace-write"

    def test_no_approval_never_in_headless_cmd(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert "-a" not in spec.cmd
        assert "never" not in spec.cmd

    def test_model_flag(self) -> None:
        spec = CodexBackend().build_headless_cmd("x", model="o3")
        assert "--model" in spec.cmd
        idx = spec.cmd.index("--model")
        assert spec.cmd[idx + 1] == "o3"

    def test_add_dir_flag(self) -> None:
        spec = CodexBackend().build_headless_cmd("x", add_dirs=["/extra"])
        assert "--add-dir" in spec.cmd
        idx = spec.cmd.index("--add-dir")
        assert spec.cmd[idx + 1] == "/extra"

    def test_multiple_add_dir_flags(self) -> None:
        spec = CodexBackend().build_headless_cmd("x", add_dirs=["/a", "/b"])
        add_dir_indices = [i for i, v in enumerate(spec.cmd) if v == "--add-dir"]
        assert len(add_dir_indices) == 2
        assert spec.cmd[add_dir_indices[0] + 1] == "/a"
        assert spec.cmd[add_dir_indices[1] + 1] == "/b"

    def test_no_dangerously_skip_permissions(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert "--dangerously-skip-permissions" not in spec.cmd

    def test_no_print_flag(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert "-p" not in spec.cmd

    def test_no_plugin_dir(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert "--plugin-dir" not in spec.cmd

    def test_no_output_format(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert "--output-format" not in spec.cmd


class TestCodexResumeCmd:
    def test_positional_structure(self) -> None:
        spec = CodexBackend().build_resume_cmd(resume_session_id="abc123", prompt="continue")
        assert spec.cmd == ("codex", "exec", "--json", "resume", "abc123", "continue")

    def test_empty_session_id_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            CodexBackend().build_resume_cmd(resume_session_id="", prompt="continue")

    def test_whitespace_session_id_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            CodexBackend().build_resume_cmd(resume_session_id="   ", prompt="continue")

    def test_no_sandbox_flag_in_resume(self) -> None:
        spec = CodexBackend().build_resume_cmd(resume_session_id="abc123", prompt="continue")
        assert "--sandbox" not in spec.cmd
        assert "workspace-write" not in spec.cmd

    def test_no_approval_flag_in_resume(self) -> None:
        spec = CodexBackend().build_resume_cmd(resume_session_id="abc123", prompt="continue")
        assert "-a" not in spec.cmd
        assert "never" not in spec.cmd

    def test_json_flag_present_in_resume(self) -> None:
        spec = CodexBackend().build_resume_cmd(resume_session_id="abc123", prompt="continue")
        assert "--json" in spec.cmd

    def test_non_json_output_format_omits_json_flag(self) -> None:
        spec = CodexBackend().build_resume_cmd(
            resume_session_id="abc123",
            prompt="continue",
            output_format=OutputFormat.STREAM_JSON,
        )
        assert "--json" not in spec.cmd


class TestCodexBuildSkillSessionCmd:
    BASE: dict[str, object] = {
        "skill_command": "/test-skill",
        "cwd": "/work",
        "completion_marker": "%%DONE%%",
        "model": None,
        "plugin_source": None,
        "output_format": OutputFormat.JSON,
    }

    def test_completion_directive_injected(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert "ORCHESTRATION DIRECTIVE" in spec.cmd[-1]

    def test_cwd_anchor_injected(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert "/work" in spec.cmd[-1]

    def test_narration_suppression_injected(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert "EFFICIENCY DIRECTIVE" in spec.cmd[-1]

    def test_completion_reminder_injected(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert "Remember: end your final response with" in spec.cmd[-1]

    def test_headless_env_set(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert spec.env["AUTOSKILLIT_HEADLESS"] == "1"

    def test_session_type_env_set(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert spec.env["AUTOSKILLIT_SESSION_TYPE"] == SESSION_TYPE_SKILL

    def test_skill_name_extracted(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(
            **{**self.BASE, "skill_command": "/planner-analyze foo"},
        )
        assert spec.env["AUTOSKILLIT_SKILL_NAME"] == "planner-analyze"

    def test_scenario_step_name_presence_and_absence(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(
            **{**self.BASE, "scenario_step_name": "step-foo"},
        )
        assert spec.env["SCENARIO_STEP_NAME"] == "step-foo"
        spec2 = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert "SCENARIO_STEP_NAME" not in spec2.env

    def test_allowed_write_prefix_presence_and_absence(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(
            **{**self.BASE, "allowed_write_prefix": "/work/src"},
        )
        assert spec.env["AUTOSKILLIT_ALLOWED_WRITE_PREFIX"] == "/work/src"
        spec2 = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert "AUTOSKILLIT_ALLOWED_WRITE_PREFIX" not in spec2.env

    def test_codex_home_env_set(self) -> None:
        dirs = [ValidatedAddDir(path="/extra")]
        spec = CodexBackend().build_skill_session_cmd(
            **{**self.BASE, "add_dirs": dirs},
        )
        assert spec.env["CODEX_HOME"] == "/extra"

    def test_codex_home_not_set_by_default(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert "CODEX_HOME" not in spec.env

    def test_no_add_dir_flag_with_add_dirs(self) -> None:
        dirs = [ValidatedAddDir(path="/extra")]
        spec = CodexBackend().build_skill_session_cmd(
            **{**self.BASE, "add_dirs": dirs},
        )
        assert "--add-dir" not in spec.cmd

    def test_no_add_dir_flag_without_add_dirs(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert "--add-dir" not in spec.cmd

    def test_model_forwarded(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(
            **{**self.BASE, "model": "o3"},
        )
        assert "--model" in spec.cmd
        idx = spec.cmd.index("--model")
        assert spec.cmd[idx + 1] == "o3"
        spec2 = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert "--model" not in spec2.cmd

    def test_resume_path(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(
            **{**self.BASE, "resume_session_id": "sess-abc123"},
        )
        assert CodexFlags.RESUME_SUBCOMMAND in spec.cmd
        assert "sess-abc123" in spec.cmd
        assert "--sandbox" in spec.cmd
        assert "-a" in spec.cmd

    def test_completion_marker_with_profile(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(
            **{**self.BASE, "profile_name": "minimax"},
        )
        assert spec.env["AUTOSKILLIT_COMPLETION_MARKER"] == self.BASE["completion_marker"]
        spec2 = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert "AUTOSKILLIT_COMPLETION_MARKER" not in spec2.env

    def test_claude_only_params_accepted_but_ignored(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(
            **{
                **self.BASE,
                "exit_after_stop_delay_ms": 5000,
                "stream_idle_timeout_ms": 3000,
            }
        )
        cmd_str = " ".join(spec.cmd)
        assert "--output-format" not in cmd_str
        assert "--plugin-dir" not in cmd_str
        assert "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY" not in spec.env
        assert "CLAUDE_STREAM_IDLE_TIMEOUT_MS" not in spec.env

    def test_cwd_set(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(
            **{**self.BASE, "cwd": "/my/project"},
        )
        assert spec.cwd == "/my/project"

    def test_json_flag_always_present(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert "--json" in spec.cmd


class TestCodexBuildSkillSessionCmdConfigAdapter:
    def test_config_adapter_matches_flat_params(self) -> None:
        config = SkillSessionConfig(
            completion_marker="%%DONE%%",
            model=None,
            plugin_source=None,
            output_format=OutputFormat.JSON,
        )
        via_config = CodexBackend().build_skill_session_cmd(
            "/test-skill", cwd="/work", config=config
        )
        via_flat = CodexBackend().build_skill_session_cmd(
            skill_command="/test-skill",
            cwd="/work",
            completion_marker="%%DONE%%",
            model=None,
            plugin_source=None,
            output_format=OutputFormat.JSON,
        )
        assert via_config.cmd == via_flat.cmd
        assert via_config.env == via_flat.env
        assert via_config.cwd == via_flat.cwd

    def test_config_adapter_forwards_all_fields(self) -> None:
        chk = SessionCheckpoint(step_name="chk")
        config = SkillSessionConfig(
            completion_marker="%%MARKER%%",
            model="o3",
            plugin_source=None,
            output_format=OutputFormat.STREAM_JSON,
            exit_after_stop_delay_ms=120000,
            stream_idle_timeout_ms=30000,
            scenario_step_name="step1",
            temp_dir_relpath=".autoskillit/temp",
            allowed_write_prefix="/tmp/test",
            provider_extras={"KEY": "val"},
            profile_name="my-profile",
            resume_session_id="s1",
            resume_checkpoint=chk,
            resume_message="resume-msg",
        )
        via_config = CodexBackend().build_skill_session_cmd("/test", cwd="/tmp", config=config)
        via_flat = CodexBackend().build_skill_session_cmd(
            skill_command="/test",
            cwd="/tmp",
            completion_marker="%%MARKER%%",
            model="o3",
            plugin_source=None,
            output_format=OutputFormat.STREAM_JSON,
            exit_after_stop_delay_ms=120000,
            stream_idle_timeout_ms=30000,
            scenario_step_name="step1",
            temp_dir_relpath=".autoskillit/temp",
            allowed_write_prefix="/tmp/test",
            provider_extras={"KEY": "val"},
            profile_name="my-profile",
            resume_session_id="s1",
            resume_checkpoint=chk,
            resume_message="resume-msg",
        )
        assert via_config.cmd == via_flat.cmd
        assert via_config.env == via_flat.env

    def test_config_path_returns_cmdspec(self) -> None:
        config = SkillSessionConfig(completion_marker="%%DONE%%", output_format=OutputFormat.JSON)
        result = CodexBackend().build_skill_session_cmd("/test", cwd="/tmp", config=config)
        assert isinstance(result, CmdSpec)
        assert isinstance(result.cmd, tuple)

    def test_config_noop_fields(self) -> None:
        config = SkillSessionConfig(
            completion_marker="%%DONE%%",
            output_format=OutputFormat.STREAM_JSON,
            plugin_source=DirectInstall(plugin_dir=Path("/p")),
        )
        spec = CodexBackend().build_skill_session_cmd("/test", cwd="/work", config=config)
        cmd_str = " ".join(spec.cmd)
        assert "--output-format" not in cmd_str
        assert "--plugin-dir" not in cmd_str
        assert "--json" in spec.cmd

    def test_legacy_flat_params_still_work(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(
            skill_command="/test-skill",
            cwd="/work",
            completion_marker="%%DONE%%",
            model=None,
            plugin_source=None,
            output_format=OutputFormat.JSON,
        )
        assert isinstance(spec, CmdSpec)
        assert any("/test-skill" in s for s in spec.cmd)


class TestCodexBuildInteractiveCmd:
    def test_dangerously_bypass_value(self) -> None:
        assert str(CodexFlags.DANGEROUSLY_BYPASS) == "--dangerously-bypass-approvals-and-sandbox"

    def test_no_resume_produces_correct_base_command(self) -> None:
        spec = CodexBackend().build_interactive_cmd()
        assert spec.cmd[0] == "codex"
        assert CodexFlags.DANGEROUSLY_BYPASS in spec.cmd
        assert CodexFlags.RESUME_SUBCOMMAND not in spec.cmd

    def test_named_resume_produces_resume_subcommand_with_session_id(self) -> None:
        from autoskillit.core import NamedResume

        spec = CodexBackend().build_interactive_cmd(resume_spec=NamedResume(session_id="abc"))
        assert spec.cmd[0] == "codex"
        assert spec.cmd[1] == CodexFlags.RESUME_SUBCOMMAND
        assert spec.cmd[2] == "abc"
        assert CodexFlags.DANGEROUSLY_BYPASS in spec.cmd

    def test_bare_resume_produces_resume_subcommand_without_session_id(self) -> None:
        from autoskillit.core import BareResume

        spec = CodexBackend().build_interactive_cmd(resume_spec=BareResume())
        assert spec.cmd[0] == "codex"
        assert spec.cmd[1] == CodexFlags.RESUME_SUBCOMMAND
        assert CodexFlags.DANGEROUSLY_BYPASS in spec.cmd
        assert "abc" not in spec.cmd

    def test_system_prompt_with_no_resume_appends_config_override(self) -> None:
        spec = CodexBackend().build_interactive_cmd(system_prompt="foo")
        assert CodexFlags.CONFIG_OVERRIDE in spec.cmd
        idx = spec.cmd.index(CodexFlags.CONFIG_OVERRIDE)
        assert spec.cmd[idx + 1] == "developer_instructions=foo"

    def test_system_prompt_with_named_resume_does_not_append_config_override(self) -> None:
        from autoskillit.core import NamedResume

        spec = CodexBackend().build_interactive_cmd(
            resume_spec=NamedResume(session_id="s1"), system_prompt="foo"
        )
        assert CodexFlags.CONFIG_OVERRIDE not in spec.cmd

    def test_add_dirs_appends_add_dir_for_each_entry(self) -> None:
        spec = CodexBackend().build_interactive_cmd(add_dirs=["/a", "/b"])
        assert (CodexFlags.ADD_DIR, "/a") == (
            spec.cmd[spec.cmd.index(CodexFlags.ADD_DIR)],
            spec.cmd[spec.cmd.index(CodexFlags.ADD_DIR) + 1],
        )

    def test_initial_prompt_is_final_element(self) -> None:
        spec = CodexBackend().build_interactive_cmd(initial_prompt="hello")
        assert spec.cmd[-1] == "hello"

    def test_plugin_source_is_silently_ignored(self) -> None:
        from pathlib import Path

        from autoskillit.core import DirectInstall

        spec = CodexBackend().build_interactive_cmd(
            plugin_source=DirectInstall(plugin_dir=Path("/x"))
        )
        assert "--plugin-dir" not in spec.cmd
        assert "/x" not in spec.cmd

    def test_env_excludes_headless_vars(self, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
        monkeypatch.setenv("HOME", "/home/user")
        spec = CodexBackend().build_interactive_cmd()
        assert "ANTHROPIC_API_KEY" not in spec.env

    def test_model_flag_appended_when_provided(self) -> None:
        spec = CodexBackend().build_interactive_cmd(model="o3")
        assert CodexFlags.MODEL in spec.cmd
        idx = spec.cmd.index(CodexFlags.MODEL)
        assert spec.cmd[idx + 1] == "o3"
