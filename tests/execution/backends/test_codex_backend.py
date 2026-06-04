from __future__ import annotations

import subprocess
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path

import pytest
import structlog.testing

from autoskillit.core import (
    AGENT_BACKEND_CODEX,
    CAMPAIGN_ID_ENV_VAR,
    CODEX_MODEL_ALIASES,
    KITCHEN_SESSION_ID_ENV_VAR,
    MCP_CLIENT_BACKEND_ENV_VAR,
    SESSION_TYPE_ORCHESTRATOR,
    SESSION_TYPE_SKILL,
    BackendCapabilities,
    BackendConventions,
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
    pkg_root,
)
from autoskillit.execution.backends.codex import (
    CODEX_ENV_PREFIX_DENYLIST,
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

    def test_capabilities_session_record_types(self) -> None:
        assert CodexBackend().capabilities.session_record_types == frozenset({"item.completed"})

    def test_capabilities_food_truck_true(self) -> None:
        assert CodexBackend().capabilities.food_truck_capable is True

    def test_capabilities_triage_capable_false(self) -> None:
        assert CodexBackend().capabilities.triage_capable is False

    def test_capabilities_supports_context_exhaustion_detection_false(self) -> None:
        assert CodexBackend().capabilities.supports_context_exhaustion_detection is False

    def test_capabilities_project_local_skills_capable_false(self) -> None:
        assert CodexBackend().capabilities.project_local_skills_capable is False

    def test_capabilities_required_skill_fields(self) -> None:
        assert CodexBackend().capabilities.required_skill_fields == frozenset(
            {"name", "description"}
        )

    def test_capabilities_required_session_files(self) -> None:
        assert CodexBackend().capabilities.required_session_files == frozenset({"config.toml"})

    def test_capabilities_session_dir_symlinks(self) -> None:
        assert CodexBackend().capabilities.session_dir_symlinks == frozenset(
            {"auth.json", ".env", "sessions"}
        )

    def test_capabilities_applicable_guards(self) -> None:
        assert CodexBackend().capabilities.applicable_guards == frozenset()

    def test_capabilities_env_denylist_prefixes(self) -> None:
        assert CodexBackend().capabilities.env_denylist_prefixes == CODEX_ENV_PREFIX_DENYLIST

    def test_capabilities_min_version(self) -> None:
        assert CodexBackend().capabilities.min_version == "0.130.0"

    def test_capabilities_version_check_command(self) -> None:
        assert CodexBackend().capabilities.version_check_command == "codex --version"

    def test_capabilities_process_name(self) -> None:
        assert CodexBackend().capabilities.process_name == "codex"

    def test_capabilities_skills_subdir(self) -> None:
        assert CodexBackend().capabilities.skills_subdir == "skills"

    def test_capabilities_mcp_env_forward_vars(self) -> None:
        from autoskillit.core import CODEX_MCP_ENV_FORWARD_VARS

        assert CodexBackend().capabilities.mcp_env_forward_vars == CODEX_MCP_ENV_FORWARD_VARS

    def test_capabilities_replay_capable_false(self) -> None:
        assert CodexBackend().capabilities.replay_capable is False

    def test_capabilities_record_capable_false(self) -> None:
        assert CodexBackend().capabilities.record_capable is False

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

    def test_build_resume_cmd_env_uses_filtered_base(self, monkeypatch) -> None:
        monkeypatch.setenv("PATH", "/usr/bin")
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
            ("food_truck_capable", True),
            ("supports_tool_list_changed", False),
        ],
    )
    def test_capability_flag(self, attr: str, expected: bool) -> None:
        assert getattr(CodexBackend().capabilities, attr) is expected

    def test_completion_record_types_content(self) -> None:
        assert CodexBackend().capabilities.completion_record_types == frozenset(
            {"turn.completed", "turn.failed", "error"}
        )

    def test_session_record_types(self) -> None:
        assert CodexBackend().capabilities.session_record_types == frozenset({"item.completed"})


class TestCodexHeadlessCmd:
    def test_default_cmd_structure(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert spec.cmd == (
            "codex",
            "exec",
            "--json",
            "--sandbox",
            "workspace-write",
            "-c",
            "features.image_generation=false",
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
        assert spec.cmd == (
            "codex",
            "exec",
            "--json",
            "--sandbox",
            "read-only",
            "-c",
            "features.image_generation=false",
            "resume",
            "abc123",
            "continue",
        )

    def test_empty_session_id_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            CodexBackend().build_resume_cmd(resume_session_id="", prompt="continue")

    def test_whitespace_session_id_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            CodexBackend().build_resume_cmd(resume_session_id="   ", prompt="continue")

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

    def test_resume_cmd_includes_sandbox_flag(self) -> None:
        spec = CodexBackend().build_resume_cmd(resume_session_id="abc123", prompt="continue")
        assert "--sandbox" in spec.cmd
        assert "read-only" in spec.cmd

    def test_resume_cmd_uses_filtered_base_env(self, monkeypatch) -> None:
        from autoskillit.execution.commands import (
            _HEADLESS_EXCLUSIVE_VARS,
            _SESSION_BASELINE_ENV,
        )

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaked")
        spec = CodexBackend().build_resume_cmd(resume_session_id="abc123", prompt="continue")
        reinjected = frozenset(_SESSION_BASELINE_ENV.keys())
        leaking = (_HEADLESS_EXCLUSIVE_VARS - reinjected) & spec.env.keys()
        assert not leaking, f"_HEADLESS_EXCLUSIVE_VARS leaked into resume env: {leaking}"


class TestCodexHeadlessCmdEnv:
    def test_headless_cmd_uses_filtered_base_env(self, monkeypatch) -> None:
        from autoskillit.execution.commands import _HEADLESS_EXCLUSIVE_VARS

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaked")
        spec = CodexBackend().build_headless_cmd("do stuff")
        leaking = _HEADLESS_EXCLUSIVE_VARS & spec.env.keys()
        assert not leaking, f"_HEADLESS_EXCLUSIVE_VARS leaked into headless env: {leaking}"


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
        spec3 = CodexBackend().build_skill_session_cmd(
            **{**self.BASE, "allowed_write_prefixes": ("/work/src/",)},
        )
        assert spec3.env["AUTOSKILLIT_ALLOWED_WRITE_PREFIXES"] == "/work/src/"
        spec4 = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert "AUTOSKILLIT_ALLOWED_WRITE_PREFIXES" not in spec4.env

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

    def test_headless_auto_gate_env_set(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert spec.env.get("AUTOSKILLIT_HEADLESS_AUTO_GATE") == "1"

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
            allowed_write_prefixes=("/tmp/test/",),
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
            allowed_write_prefixes=("/tmp/test/",),
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
        assert "abc" in spec.cmd
        assert spec.origin is not None
        assert "abc" in spec.origin.positional
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
        overrides = [
            spec.cmd[i + 1] for i, v in enumerate(spec.cmd[:-1]) if v == CodexFlags.CONFIG_OVERRIDE
        ]
        assert "developer_instructions=foo" in overrides
        assert "features.image_generation=false" in overrides

    def test_system_prompt_with_named_resume_does_not_append_config_override(self) -> None:
        from autoskillit.core import NamedResume

        spec = CodexBackend().build_interactive_cmd(
            resume_spec=NamedResume(session_id="s1"), system_prompt="foo"
        )
        overrides = [
            spec.cmd[i + 1] for i, v in enumerate(spec.cmd[:-1]) if v == CodexFlags.CONFIG_OVERRIDE
        ]
        assert not any(v.startswith("developer_instructions=") for v in overrides)
        assert "features.image_generation=false" in overrides

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


class TestCodexBuildSkillSessionCmdAgentBackend:
    """Tests that AUTOSKILLIT_AGENT_BACKEND is injected into skill session env."""

    BASE: dict[str, object] = {
        "skill_command": "/test-skill",
        "cwd": "/work",
        "completion_marker": "%%DONE%%",
        "model": None,
        "plugin_source": None,
        "output_format": OutputFormat.JSON,
    }

    def test_agent_backend_env_set(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert spec.env["AUTOSKILLIT_AGENT_BACKEND"] == "codex"

    def test_agent_backend_overrides_parent_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "wrong-value")
        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert spec.env["AUTOSKILLIT_AGENT_BACKEND"] == "codex"

    def test_agent_backend_present_without_parent_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)
        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert spec.env["AUTOSKILLIT_AGENT_BACKEND"] == "codex"


class TestCodexDynaconfBackendEnv:
    """AUTOSKILLIT_AGENT_BACKEND__BACKEND (Dynaconf nested form) in Codex cmd builders."""

    SKILL_BASE: dict[str, object] = {
        "skill_command": "/test-skill",
        "cwd": "/work",
        "completion_marker": "%%DONE%%",
        "model": None,
        "plugin_source": None,
        "output_format": OutputFormat.JSON,
    }

    FOOD_TRUCK_BASE: dict[str, object] = {
        "orchestrator_prompt": "dispatch the work",
        "plugin_source": DirectInstall(plugin_dir=Path("/pkg")),
        "cwd": "/work",
        "completion_marker": "%%DONE%%",
    }

    def test_skill_session_has_dynaconf_backend(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(**self.SKILL_BASE)
        assert spec.env["AUTOSKILLIT_AGENT_BACKEND__BACKEND"] == "codex"

    def test_food_truck_has_dynaconf_backend(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.FOOD_TRUCK_BASE)
        assert spec.env["AUTOSKILLIT_AGENT_BACKEND__BACKEND"] == "codex"


class TestCodexBuildFoodTruckCmd:
    BASE: dict[str, object] = {
        "orchestrator_prompt": "dispatch the work",
        "plugin_source": DirectInstall(plugin_dir=Path("/pkg")),
        "cwd": "/work",
        "completion_marker": "%%DONE%%",
    }

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)

    # --- Structural / flag tests (non-resume) ---

    def test_cmd_0_is_codex(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert spec.cmd[0] == "codex"

    def test_cmd_1_is_exec(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert spec.cmd[1] == "exec"

    def test_json_flag_present(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert "--json" in spec.cmd

    def test_sandbox_read_only(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert "--sandbox" in spec.cmd
        idx = spec.cmd.index("--sandbox")
        assert spec.cmd[idx + 1] == "read-only"

    def test_config_override_web_search_disabled(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert "-c" in spec.cmd
        idx = spec.cmd.index("-c")
        assert spec.cmd[idx + 1] == "web_search=disabled"

    def test_approval_never(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert "-a" in spec.cmd
        idx = spec.cmd.index("-a")
        assert spec.cmd[idx + 1] == "never"

    def test_no_add_dir_flag(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert "--add-dir" not in spec.cmd

    def test_no_plugin_dir_flag(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert "--plugin-dir" not in spec.cmd

    def test_mcp_tools_only_prompt_reinforcement(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert "ORCHESTRATION DIRECTIVE" in spec.cmd[-1]

    def test_prompt_is_last_token(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert "dispatch the work" in spec.cmd[-1]

    def test_returns_cmdspec_with_tuple_cmd(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert isinstance(spec, CmdSpec)
        assert isinstance(spec.cmd, tuple)

    # --- Env var tests ---

    def test_headless_env_set(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert spec.env["AUTOSKILLIT_HEADLESS"] == "1"

    def test_session_type_orchestrator(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert spec.env["AUTOSKILLIT_SESSION_TYPE"] == SESSION_TYPE_ORCHESTRATOR

    def test_campaign_id_present_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_ID", "camp-123")
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert spec.env[CAMPAIGN_ID_ENV_VAR] == "camp-123"

    def test_campaign_id_absent_when_not_set(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert CAMPAIGN_ID_ENV_VAR not in spec.env

    def test_kitchen_session_id_present_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOSKILLIT_KITCHEN_SESSION_ID", "ks-456")
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert spec.env[KITCHEN_SESSION_ID_ENV_VAR] == "ks-456"

    def test_kitchen_session_id_absent_when_not_set(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert KITCHEN_SESSION_ID_ENV_VAR not in spec.env

    def test_completion_marker_env_set(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert spec.env["AUTOSKILLIT_COMPLETION_MARKER"] == "%%DONE%%"

    def test_provider_profile_absent(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert "AUTOSKILLIT_PROVIDER_PROFILE" not in spec.env

    # --- Resume path tests ---

    def test_resume_subcommand_present(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(
            **{**self.BASE, "resume_session_id": "sess-abc"},
        )
        assert "resume" in spec.cmd

    def test_resume_session_id_follows_resume(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(
            **{**self.BASE, "resume_session_id": "sess-abc"},
        )
        idx = spec.cmd.index("resume")
        assert spec.cmd[idx + 1] == "sess-abc"

    def test_resume_prompt_is_last(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(
            **{**self.BASE, "resume_session_id": "sess-abc"},
        )
        assert "dispatch the work" in spec.cmd[-1]

    def test_resume_json_flag_present(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(
            **{**self.BASE, "resume_session_id": "sess-abc"},
        )
        assert "--json" in spec.cmd

    def test_resume_cmd_structure(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(
            **{**self.BASE, "resume_session_id": "sess-abc"},
        )
        assert spec.cmd[0] == "codex"
        assert spec.cmd[1] == "exec"
        assert "--json" in spec.cmd
        resume_idx = spec.cmd.index("resume")
        assert spec.cmd[resume_idx + 1] == "sess-abc"
        assert "dispatch the work" in spec.cmd[-1]

    def test_no_resume_when_none(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert "resume" not in spec.cmd

    def test_non_resume_json_present(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert "--json" in spec.cmd


class TestCodexEnsurePreLaunchConfigValidation:
    @pytest.fixture(autouse=True)
    def _clean_backend_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(MCP_CLIENT_BACKEND_ENV_VAR, raising=False)

    def test_ensure_pre_launch_returns_error_on_config_load_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        doctor_output = (
            '{"checks": {"config.load": {"status": "error",'
            ' "summary": "invalid type: map, expected u32",'
            ' "remediation": "Delete [tui] section"}}}'
        )

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout=doctor_output)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(
            "autoskillit.execution.backends.codex.ensure_codex_mcp_registered",
            lambda **kw: False,
        )
        errors = CodexBackend().ensure_pre_launch()
        assert errors
        combined = " ".join(errors)
        assert "invalid type: map, expected u32" in combined
        assert "Delete [tui] section" in combined

    def test_ensure_pre_launch_returns_empty_on_config_load_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        doctor_output = '{"checks": {"config.load": {"status": "ok"}}}'

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout=doctor_output)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(
            "autoskillit.execution.backends.codex.ensure_codex_mcp_registered",
            lambda **kw: False,
        )
        assert CodexBackend().ensure_pre_launch() == []

    def test_ensure_pre_launch_returns_empty_on_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 20)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(
            "autoskillit.execution.backends.codex.ensure_codex_mcp_registered",
            lambda **kw: False,
        )
        assert CodexBackend().ensure_pre_launch() == []

    def test_ensure_pre_launch_returns_empty_on_oserror(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run(cmd, **kwargs):
            raise FileNotFoundError("codex not found")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(
            "autoskillit.execution.backends.codex.ensure_codex_mcp_registered",
            lambda **kw: False,
        )
        assert CodexBackend().ensure_pre_launch() == []

    def test_ensure_pre_launch_codex_doctor_runs_after_mcp_registration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        call_order: list[str] = []

        def fake_register(**kw):
            call_order.append("register")
            return False

        def fake_run(cmd, **kwargs):
            call_order.append("doctor")
            return subprocess.CompletedProcess(cmd, 0, stdout='{"checks": {}}')

        monkeypatch.setattr(
            "autoskillit.execution.backends.codex.ensure_codex_mcp_registered", fake_register
        )
        monkeypatch.setattr(subprocess, "run", fake_run)
        CodexBackend().ensure_pre_launch()
        assert call_order == ["register", "doctor"]

    def test_ensure_pre_launch_returns_empty_on_nonzero_returncode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="error output")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(
            "autoskillit.execution.backends.codex.ensure_codex_mcp_registered",
            lambda **kw: False,
        )
        assert CodexBackend().ensure_pre_launch() == []

    def test_ensure_pre_launch_returns_empty_on_malformed_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="not json")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(
            "autoskillit.execution.backends.codex.ensure_codex_mcp_registered",
            lambda **kw: False,
        )
        assert CodexBackend().ensure_pre_launch() == []

    def test_ensure_pre_launch_returns_empty_on_missing_config_check(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout='{"checks": {}}')

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(
            "autoskillit.execution.backends.codex.ensure_codex_mcp_registered",
            lambda **kw: False,
        )
        assert CodexBackend().ensure_pre_launch() == []

    def test_ensure_pre_launch_integration_registration_succeeds_then_validates(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout='{"checks": {"config.load": {"status": "error", "summary": "bad config"}}}',
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        errors = CodexBackend().ensure_pre_launch()
        assert errors
        assert "bad config" in " ".join(errors)


class TestCodexBackendVersion:
    def test_version_returns_stripped_stdout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(cmd, *, capture_output, text, timeout):
            result = subprocess.CompletedProcess(cmd, 0)
            result.stdout = "  1.2.3\n"
            result.stderr = ""
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert CodexBackend().version() == "1.2.3"

    def test_version_stderr_fallback_when_stdout_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run(cmd, *, capture_output, text, timeout):
            result = subprocess.CompletedProcess(cmd, 0)
            result.stdout = ""
            result.stderr = "1.2.3-stderr\n"
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert CodexBackend().version() == "1.2.3-stderr"

    def test_version_returns_empty_on_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(cmd, *, capture_output, text, timeout):
            raise subprocess.TimeoutExpired(cmd, timeout)

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert CodexBackend().version() == ""

    def test_version_returns_empty_on_oserror(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(cmd, *, capture_output, text, timeout):
            raise OSError("not found")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert CodexBackend().version() == ""

    def test_version_delegates_to_version_cmd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured_cmd = None

        def fake_run(cmd, *, capture_output, text, timeout):
            nonlocal captured_cmd
            captured_cmd = cmd
            result = subprocess.CompletedProcess(cmd, 0)
            result.stdout = "v1"
            result.stderr = ""
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = CodexBackend().version()
        assert captured_cmd == ["codex", "--version"]
        assert result == "v1"


class TestCodexStubMethods:
    def test_validate_skill_content_returns_list(self) -> None:
        result = CodexBackend().validate_skill_content("some skill content")
        assert result == []

    def test_list_plugins_returns_list(self) -> None:
        result = CodexBackend().list_plugins()
        assert result == []


class TestCodexForwardVarsInjection:
    """Every CODEX_MCP_ENV_FORWARD_VARS member must appear in cmd-builder output."""

    SKILL_BASE: dict[str, object] = {
        "skill_command": "/test-skill",
        "cwd": "/work",
        "completion_marker": "%%DONE%%",
        "model": None,
        "plugin_source": None,
        "output_format": OutputFormat.JSON,
    }
    FOOD_TRUCK_BASE: dict[str, object] = {
        "orchestrator_prompt": "dispatch the work",
        "plugin_source": DirectInstall(plugin_dir=Path("/pkg")),
        "cwd": "/work",
        "completion_marker": "%%DONE%%",
    }

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)

    @pytest.mark.parametrize(
        "var",
        sorted(
            __import__(
                "autoskillit.core.types._type_constants_env",
                fromlist=["CODEX_MCP_ENV_FORWARD_VARS"],
            ).CODEX_MCP_ENV_FORWARD_VARS
        ),
    )
    def test_skill_session_has_forward_var(self, var: str) -> None:
        spec = CodexBackend().build_skill_session_cmd(**self.SKILL_BASE)
        assert var in spec.env, f"{var} missing from build_skill_session_cmd env"

    @pytest.mark.parametrize(
        "var",
        sorted(
            __import__(
                "autoskillit.core.types._type_constants_env",
                fromlist=["CODEX_MCP_ENV_FORWARD_VARS"],
            ).CODEX_MCP_ENV_FORWARD_VARS
        ),
    )
    def test_food_truck_has_forward_var(self, var: str) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.FOOD_TRUCK_BASE)
        assert var in spec.env, f"{var} missing from build_food_truck_cmd env"

    def test_headless_has_mcp_client_backend(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert MCP_CLIENT_BACKEND_ENV_VAR in spec.env
        assert spec.env[MCP_CLIENT_BACKEND_ENV_VAR] == "codex"

    def test_interactive_has_mcp_client_backend(self) -> None:
        spec = CodexBackend().build_interactive_cmd()
        assert MCP_CLIENT_BACKEND_ENV_VAR in spec.env
        assert spec.env[MCP_CLIENT_BACKEND_ENV_VAR] == "codex"

    def test_resume_has_mcp_client_backend(self) -> None:
        spec = CodexBackend().build_resume_cmd(resume_session_id="abc123", prompt="go")
        assert MCP_CLIENT_BACKEND_ENV_VAR in spec.env
        assert spec.env[MCP_CLIENT_BACKEND_ENV_VAR] == "codex"


class TestCodexMcpClientBackendRequired:
    """build_env required= gate catches missing MCP_CLIENT_BACKEND_ENV_VAR."""

    SKILL_BASE: dict[str, object] = {
        "skill_command": "/test-skill",
        "cwd": "/work",
        "completion_marker": "%%DONE%%",
        "model": None,
        "plugin_source": None,
        "output_format": OutputFormat.JSON,
    }
    FOOD_TRUCK_BASE: dict[str, object] = {
        "orchestrator_prompt": "dispatch the work",
        "plugin_source": DirectInstall(plugin_dir=Path("/pkg")),
        "cwd": "/work",
        "completion_marker": "%%DONE%%",
    }

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)

    def test_skill_session_mcp_backend_value(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(**self.SKILL_BASE)
        assert spec.env[MCP_CLIENT_BACKEND_ENV_VAR] == AGENT_BACKEND_CODEX

    def test_food_truck_mcp_backend_value(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.FOOD_TRUCK_BASE)
        assert spec.env[MCP_CLIENT_BACKEND_ENV_VAR] == AGENT_BACKEND_CODEX

    def test_skill_session_mcp_backend_overrides_parent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(MCP_CLIENT_BACKEND_ENV_VAR, "wrong")
        spec = CodexBackend().build_skill_session_cmd(**self.SKILL_BASE)
        assert spec.env[MCP_CLIENT_BACKEND_ENV_VAR] == AGENT_BACKEND_CODEX

    def test_food_truck_mcp_backend_overrides_parent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(MCP_CLIENT_BACKEND_ENV_VAR, "wrong")
        spec = CodexBackend().build_food_truck_cmd(**self.FOOD_TRUCK_BASE)
        assert spec.env[MCP_CLIENT_BACKEND_ENV_VAR] == AGENT_BACKEND_CODEX

    @pytest.fixture()
    def _strip_mcp_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original = CodexEnvPolicy.build_env

        def _strip_mcp_key(
            self_policy: CodexEnvPolicy,
            base: Mapping[str, str],
            *,
            extras: Mapping[str, str] | None = None,
            required: frozenset[str] | None = None,
        ) -> dict[str, str]:
            if extras is not None:
                extras = {k: v for k, v in extras.items() if k != MCP_CLIENT_BACKEND_ENV_VAR}
            return original(self_policy, base, extras=extras, required=required)

        monkeypatch.setattr(CodexEnvPolicy, "build_env", _strip_mcp_key)

    def test_skill_session_raises_without_mcp_client_backend(self, _strip_mcp_env: None) -> None:
        with pytest.raises(ValueError, match="MCP_CLIENT_BACKEND"):
            CodexBackend().build_skill_session_cmd(**self.SKILL_BASE)

    def test_food_truck_raises_without_mcp_client_backend(self, _strip_mcp_env: None) -> None:
        with pytest.raises(ValueError, match="MCP_CLIENT_BACKEND"):
            CodexBackend().build_food_truck_cmd(**self.FOOD_TRUCK_BASE)


class TestCodexBackendConventions:
    def test_conventions_returns_backend_conventions_instance(self) -> None:
        assert isinstance(CodexBackend().conventions, BackendConventions)

    def test_codex_skills_in_project_local_dirs(self) -> None:
        assert ".codex/skills" in CodexBackend().conventions.project_local_skill_search_dirs

    def test_agents_skills_in_project_local_dirs(self) -> None:
        assert ".agents/skills" in CodexBackend().conventions.project_local_skill_search_dirs

    def test_conventions_skills_subdir(self) -> None:
        assert CodexBackend().conventions.skills_subdir == Path("skills")


class TestCodexBackendSetupSessionDir:
    @pytest.fixture(autouse=True)
    def _setup_dirs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.fake_home = tmp_path / "fakehome"
        self.codex_home = self.fake_home / ".codex"
        self.codex_home.mkdir(parents=True)
        self.session_dir = tmp_path / "session"
        self.session_dir.mkdir()
        self.fake_log_dir = tmp_path / "logs"
        monkeypatch.setattr(Path, "home", staticmethod(lambda: self.fake_home))
        monkeypatch.setattr(
            "autoskillit.execution.backends.codex.default_log_dir",
            lambda: self.fake_log_dir,
        )

    def _write_all_source_files(self) -> None:
        (self.codex_home / "config.toml").write_text("[mcp_servers.autoskillit]\n")
        (self.codex_home / "auth.json").write_text("{}")
        (self.codex_home / ".env").write_text("KEY=val\n")

    def test_happy_path_all_files_provisioned(self) -> None:
        self._write_all_source_files()
        CodexBackend().setup_session_dir(self.session_dir)
        assert (self.session_dir / "config.toml").is_file()
        assert (self.session_dir / "auth.json").is_symlink()
        assert (self.session_dir / ".env").is_file()
        assert (self.session_dir / "sessions").is_symlink()
        assert (self.session_dir / "sessions").resolve() == (
            self.fake_log_dir / "codex-sessions"
        ).resolve()

    def test_missing_config_raises_and_logs_error(self) -> None:
        with pytest.raises(FileNotFoundError):
            CodexBackend().setup_session_dir(self.session_dir)

    def test_absent_auth_logs_warning_no_raise(self) -> None:
        (self.codex_home / "config.toml").write_text("[mcp]\n")
        with structlog.testing.capture_logs() as cap_logs:
            CodexBackend().setup_session_dir(self.session_dir)
        assert not (self.session_dir / "auth.json").exists()
        assert any(
            e.get("event") == "codex_auth_copy_missing" and e.get("log_level") == "warning"
            for e in cap_logs
        )

    def test_auth_symlink_oserror_logs_warning_no_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (self.codex_home / "config.toml").write_text("[mcp]\n")
        (self.codex_home / "auth.json").write_text("{}")
        # Pre-create auth.json as a regular file to block symlink creation
        (self.session_dir / "auth.json").write_text("blocker")
        with structlog.testing.capture_logs() as cap_logs:
            CodexBackend().setup_session_dir(self.session_dir)
        # Verify auth.json is still a regular file (symlink failed silently)
        assert not (self.session_dir / "auth.json").is_symlink()
        assert any(
            e.get("event") == "codex_auth_symlink_failed" and e.get("log_level") == "warning"
            for e in cap_logs
        )

    def test_absent_env_silently_skipped(self) -> None:
        (self.codex_home / "config.toml").write_text("[mcp]\n")
        CodexBackend().setup_session_dir(self.session_dir)
        assert not (self.session_dir / ".env").exists()

    def test_sessions_symlink_oserror_swallowed(self) -> None:
        (self.codex_home / "config.toml").write_text("[mcp]\n")
        (self.codex_home / "auth.json").write_text("{}")
        # Pre-create sessions/ as a directory to block symlink creation
        (self.session_dir / "sessions").mkdir()
        CodexBackend().setup_session_dir(self.session_dir)
        # Verify sessions is still a directory (symlink failed silently)
        assert not (self.session_dir / "sessions").is_symlink()

    def test_setup_session_dir_creates_agents_directory(self) -> None:
        self._write_all_source_files()
        CodexBackend().setup_session_dir(self.session_dir)
        assert (self.session_dir / "agents").is_dir()

    def test_agent_toml_count_matches_md_sources(self) -> None:
        self._write_all_source_files()
        CodexBackend().setup_session_dir(self.session_dir)
        toml_files = list((self.session_dir / "agents").glob("*.toml"))
        expected = 0
        for md_path in (pkg_root() / "agents").glob("*.md"):
            if md_path.name == "CLAUDE.md":
                continue
            text = md_path.read_text(encoding="utf-8")
            if not text.startswith("---"):
                continue
            parts = text.split("---", 2)
            if len(parts) < 3 or not parts[2].strip() or "'''" in parts[2]:
                continue
            expected += 1
        assert len(toml_files) == expected, (
            f"TOML count {len(toml_files)} != valid source count {expected}"
        )

    def test_agent_toml_required_fields_present_and_nonempty(self) -> None:
        import tomllib

        self._write_all_source_files()
        CodexBackend().setup_session_dir(self.session_dir)
        for toml_path in sorted((self.session_dir / "agents").glob("*.toml")):
            data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
            assert data["name"], f"{toml_path.name}: name empty"
            assert data["description"], f"{toml_path.name}: description empty"
            assert data["developer_instructions"], (
                f"{toml_path.name}: developer_instructions empty"
            )
            assert data["sandbox_mode"] == "workspace-write", (
                f"{toml_path.name}: wrong sandbox_mode"
            )

    def test_agent_toml_model_alias_mapped(self) -> None:
        import tomllib

        self._write_all_source_files()
        CodexBackend().setup_session_dir(self.session_dir)
        wp_toml = self.session_dir / "agents" / "wp-elaborator.toml"
        data = tomllib.loads(wp_toml.read_text(encoding="utf-8"))
        assert data["model"] == CODEX_MODEL_ALIASES["sonnet"]

    def test_agent_toml_contains_effort(self) -> None:
        import tomllib

        from autoskillit.core import CODEX_EFFORT_MAPPING

        self._write_all_source_files()
        CodexBackend().setup_session_dir(self.session_dir)
        wp_toml = self.session_dir / "agents" / "wp-elaborator.toml"
        data = tomllib.loads(wp_toml.read_text(encoding="utf-8"))
        assert "model_reasoning_effort" in data, (
            "agent TOML must include model_reasoning_effort field"
        )
        assert data["model_reasoning_effort"] == CODEX_EFFORT_MAPPING["sonnet"]

    def test_no_claude_md_toml_generated(self) -> None:
        self._write_all_source_files()
        CodexBackend().setup_session_dir(self.session_dir)
        assert not (self.session_dir / "agents" / "CLAUDE.toml").exists()

    def test_developer_instructions_preserves_markdown_structure(self) -> None:
        import tomllib

        self._write_all_source_files()
        CodexBackend().setup_session_dir(self.session_dir)
        wp_toml = self.session_dir / "agents" / "wp-elaborator.toml"
        data = tomllib.loads(wp_toml.read_text(encoding="utf-8"))
        body = data["developer_instructions"]
        assert "# wp-elaborator" in body
        assert "## Tool Constraints" in body
        assert "```json" in body
