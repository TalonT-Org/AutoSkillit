from __future__ import annotations

import os
import subprocess
import tomllib
from collections.abc import Mapping
from dataclasses import replace
from enum import StrEnum
from pathlib import Path
from typing import cast

import pytest
import structlog.testing

import autoskillit.execution.backends._codex_prelaunch as _patch_backends__codex_prelaunch
import autoskillit.execution.backends.codex as _patch_backends_codex
from autoskillit.core import (
    AGENT_BACKEND_CODEX,
    BUNDLED_EXPLORER_ROLES,
    CAMPAIGN_ID_ENV_VAR,
    CODEX_EFFORT_MAPPING,
    CODEX_MODEL_ALIASES,
    DIRECT_PREFIX,
    KITCHEN_SESSION_ID_ENV_VAR,
    MCP_CLIENT_BACKEND_ENV_VAR,
    SESSION_TYPE_ORCHESTRATOR,
    SESSION_TYPE_SKILL,
    WEB_EVIDENCE_RESEARCHER_ROLE,
    AgentDef,
    BackendCapabilities,
    BackendConventions,
    ChildExecutionIdentity,
    CmdSpec,
    CodexAgentProjectionDef,
    CodingAgentBackend,
    EnvPolicy,
    ExecutionIdentity,
    OutputFormat,
    ResultParser,
    SessionCheckpoint,
    SessionLocator,
    SkillExecutionRole,
    SkillSessionConfig,
    StreamParser,
    ValidatedAddDir,
    agent_definition_digest,
    load_agent_definitions,
    pkg_root,
)
from autoskillit.execution.backends._codex_config import effective_codex_agent_names
from autoskillit.execution.backends.codex import (
    CODEX_ENV_PREFIX_DENYLIST,
    CodexBackend,
    CodexEnvPolicy,
    CodexFlags,
    CodexResultParser,
    CodexSessionLocator,
    CodexStreamParser,
    clear_explorer_binding_env,
    refresh_explorer_binding_env,
)
from tests._codex_feature_policy import RETIRED_CODEX_FEATURES
from tests.execution.backends._otlp_test_data import OTLP_EXTRAS
from tests.execution.backends._plugin_binding import plugin_binding

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_OTLP_OVERRIDES = (
    'otel.exporter={otlp-http={endpoint="http://127.0.0.1:4318/v1/logs",protocol="json"}}',
    'otel.metrics_exporter={otlp-http={endpoint="http://127.0.0.1:4318/v1/metrics",protocol="json"}}',
)


def _config_overrides(spec: CmdSpec) -> list[str]:
    return [value for flag, value in zip(spec.cmd, spec.cmd[1:]) if flag == "-c"]


class TestCodexFlags:
    def test_is_str_enum(self) -> None:
        assert issubclass(CodexFlags, StrEnum)

    def test_str_json_equals_double_dash_json(self) -> None:
        assert str(CodexFlags.JSON) == "--json"

    def test_all_members_present(self) -> None:
        expected = {
            "JSON",
            "SANDBOX",
            "MODEL",
            "MODEL_SHORT",
            "PROFILE",
            "ADD_DIR",
            "RESUME_SUBCOMMAND",
            "CONFIG_OVERRIDE",
            "DANGEROUSLY_BYPASS",
            "DANGEROUSLY_BYPASS_HOOK_TRUST",
        }
        actual = {m.name for m in CodexFlags}
        assert actual == expected
        assert len(set(CodexFlags)) == len(expected)


class TestCodexBackend:
    def test_isinstance_coding_agent_backend(self) -> None:
        assert isinstance(CodexBackend(), CodingAgentBackend)

    def test_name_property(self) -> None:
        assert CodexBackend().name == AGENT_BACKEND_CODEX

    def test_effective_execution_identity_uses_codex_rollout_authority(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import Mock

        requested = ExecutionIdentity(
            requested_parent_backend=AGENT_BACKEND_CODEX,
            children=(
                ChildExecutionIdentity(
                    task_id="task-a",
                    role="semantic-code-navigator",
                    plan_digest="plan-a",
                    definition_digest="definition-a",
                ),
            ),
        )
        effective = ExecutionIdentity(effective_parent_backend=AGENT_BACKEND_CODEX)
        rollout_path = Path("rollout-parent.jsonl")
        locate_session = Mock(return_value=rollout_path)
        locator = SimpleNamespace(locate_session=locate_session)
        extract_identity = Mock(return_value=effective)
        monkeypatch.setattr(CodexBackend, "session_locator", lambda _self: locator)
        monkeypatch.setattr(
            _patch_backends_codex,
            "extract_codex_execution_identity",
            extract_identity,
        )

        observed = CodexBackend().resolve_effective_execution_identity(
            requested=requested,
            session_id="parent-session",
        )

        locate_session.assert_called_once_with("parent-session")
        extract_identity.assert_called_once_with(
            rollout_path,
            requested=requested,
            child_rollout_resolver=locate_session,
        )
        assert observed is effective

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

    def test_capabilities_supports_model_capacity_error_detection_true(self) -> None:
        assert CodexBackend().capabilities.supports_model_capacity_error_detection is True

    def test_capabilities_required_skill_fields(self) -> None:
        assert CodexBackend().capabilities.required_skill_fields == frozenset(
            {"name", "description"}
        )

    def test_capabilities_required_session_files(self) -> None:
        assert CodexBackend().capabilities.required_session_files == frozenset({"config.toml"})

    def test_capabilities_session_dir_symlinks(self) -> None:
        assert CodexBackend().capabilities.session_dir_symlinks == frozenset(
            {"sessions", "archived_sessions"}
        )

    def test_capabilities_applicable_guards(self) -> None:
        assert CodexBackend().capabilities.applicable_guards == frozenset({"write_guard"})

    def test_capabilities_env_denylist_prefixes(self) -> None:
        assert CodexBackend().capabilities.env_denylist_prefixes == CODEX_ENV_PREFIX_DENYLIST

    def test_capabilities_min_version(self) -> None:
        from autoskillit.execution.backends._codex_discovery import (
            CODEX_SKILL_DISCOVERY_CONTRACT,
        )

        assert CodexBackend().capabilities.min_version == "0.136.0"
        assert (
            CodexBackend().capabilities.min_version
            == CODEX_SKILL_DISCOVERY_CONTRACT.extra_roots_min_version
        )

    def test_capabilities_version_check_command(self) -> None:
        assert CodexBackend().capabilities.version_check_command == "codex --version"

    def test_capabilities_process_name(self) -> None:
        assert CodexBackend().capabilities.process_name == "codex"

    def test_capabilities_skills_subdir(self) -> None:
        assert CodexBackend().capabilities.skills_subdir == "skills"

    def test_capabilities_mcp_env_forward_vars(self) -> None:
        from autoskillit.core import CODEX_MCP_ENV_FORWARD_VARS

        assert CodexBackend().capabilities.mcp_env_forward_vars == CODEX_MCP_ENV_FORWARD_VARS

    def test_capabilities_replay_capable_true(self) -> None:
        assert CodexBackend().capabilities.replay_capable is True

    def test_capabilities_record_capable_false(self) -> None:
        assert CodexBackend().capabilities.record_capable is False

    def test_capabilities_skill_sigil_dollar(self) -> None:
        assert CodexBackend().capabilities.skill_sigil == "$"

    def test_binary_name(self) -> None:
        assert CodexBackend().binary_name() == "codex"

    def test_version_cmd(self) -> None:
        assert CodexBackend().version_cmd() == ("codex", "--version")


class TestCodexBackendCommands:
    def test_build_headless_cmd_codex_at_0(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert spec.cmd[0] == "codex"

    def test_build_headless_cmd_app_server_at_1(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert spec.cmd[1] == "app-server"

    def test_build_headless_cmd_no_json_flag(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert "--json" not in spec.cmd

    def test_build_headless_cmd_sandbox_in_plan(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert "--sandbox" not in spec.cmd
        assert spec.app_server_plan.sandbox == "workspace-write"

    def test_no_approval_flag_in_headless_cmd(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert "-a" not in spec.cmd

    def test_build_headless_cmd_prompt_on_plan_not_argv(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert spec.app_server_plan.prompt == "do stuff"
        assert "do stuff" not in spec.cmd

    def test_build_headless_cmd_with_model(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff", model="o3")
        assert "--model" not in spec.cmd
        assert spec.app_server_plan.model == CodexBackend().translate_model("o3")

    def test_build_headless_cmd_returns_cmd_spec(self) -> None:
        spec = CodexBackend().build_headless_cmd("x")
        assert isinstance(spec, CmdSpec)
        assert isinstance(spec.cmd, tuple)

    def test_build_headless_cmd_with_env_extras(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff", env_extras={"FOO": "bar"})
        assert spec.env.get("FOO") == "bar"

    def test_headless_otlp_overrides_use_exact_inline_tables(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff", env_extras=OTLP_EXTRAS)
        overrides = _config_overrides(spec)
        assert _OTLP_OVERRIDES[0] in overrides
        assert _OTLP_OVERRIDES[1] in overrides

    def test_otlp_overrides_escape_toml_control_characters(self) -> None:
        extras = {
            **OTLP_EXTRAS,
            "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": "http://127.0.0.1:4318/\b/v1/logs",
        }
        spec = CodexBackend().build_headless_cmd("do stuff", env_extras=extras)
        exporter_override = next(
            value for value in _config_overrides(spec) if value.startswith("otel.exporter=")
        )

        parsed = tomllib.loads(exporter_override)

        assert (
            parsed["otel"]["exporter"]["otlp-http"]["endpoint"]
            == extras["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"]
        )

    def test_otlp_overrides_require_both_endpoints(self) -> None:
        spec = CodexBackend().build_headless_cmd(
            "do stuff",
            env_extras={
                "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": OTLP_EXTRAS["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"]
            },
        )
        assert not any(value.startswith("otel.exporter=") for value in spec.cmd)
        assert not any(value.startswith("otel.metrics_exporter=") for value in spec.cmd)

    def test_otlp_overrides_compose_with_existing_headless_overrides(self) -> None:
        skill = CodexBackend().build_skill_session_cmd(
            "/autoskillit:investigate",
            "/repo",
            completion_marker="DONE",
            provider_extras=OTLP_EXTRAS,
            network_access=True,
            add_dirs=(
                ValidatedAddDir(
                    path="/repo/add-dir",
                    session_home="/repo",
                    skill_entries=(("investigate", "investigate/SKILL.md"),),
                ),
            ),
        )
        food_truck_catalog = ValidatedAddDir(
            path="/repo/add-dir",
            session_home="/repo",
            skill_entries=(("investigate", "investigate/SKILL.md"),),
        )
        food_truck = CodexBackend().build_food_truck_cmd(
            orchestrator_prompt="dispatch",
            plugin_binding=None,
            cwd="/repo",
            completion_marker="DONE",
            env_extras=OTLP_EXTRAS,
            managed_skill_catalog=food_truck_catalog,
        )
        skill_overrides = _config_overrides(skill)
        food_truck_overrides = _config_overrides(food_truck)
        assert skill.app_server_plan is not None
        assert (
            skill.app_server_plan.config_overrides["sandbox_workspace_write.network_access"]
            is True
        )
        assert food_truck.app_server_plan is not None
        assert food_truck.app_server_plan.config_overrides["web_search"] == "disabled"
        assert "web_search=disabled" not in food_truck_overrides
        assert tuple(value for value in skill_overrides if value.startswith("otel.")) == (
            _OTLP_OVERRIDES
        )
        assert tuple(value for value in food_truck_overrides if value.startswith("otel.")) == (
            _OTLP_OVERRIDES
        )

    def test_interactive_cmd_does_not_gain_run_scoped_otlp_overrides(self) -> None:
        spec = CodexBackend().build_interactive_cmd(env_extras=OTLP_EXTRAS)
        assert not any(value.startswith("otel.exporter=") for value in spec.cmd)
        assert not any(value.startswith("otel.metrics_exporter=") for value in spec.cmd)

    def test_build_cmd_delegates_to_headless(self) -> None:
        backend = CodexBackend()
        spec = backend.build_cmd("do stuff", "/work")
        assert spec.cmd[0] == "codex"
        assert spec.cwd == "/work"

    def test_build_cmd_rewrites_both_spec_and_plan_cwd(self) -> None:
        """build_cmd's shallow replace() must update both CmdSpec.cwd and the
        nested app_server_plan.cwd, not just the outer spec (Part D)."""
        spec = CodexBackend().build_cmd("do stuff", "/work")
        assert spec.app_server_plan is not None
        assert spec.app_server_plan.cwd == "/work"

    def test_build_resume_cmd_with_session_id(self) -> None:
        from autoskillit.core import OUTPUT_DISCIPLINE_DIGEST

        spec = CodexBackend().build_resume_cmd(resume_session_id="sess-123", prompt="continue")
        assert spec.cmd[0] == "codex"
        assert spec.cmd[1] == "app-server"
        assert "resume" not in spec.cmd
        assert "sess-123" not in spec.cmd
        assert spec.app_server_plan.resume_thread_id == "sess-123"
        assert spec.app_server_plan.prompt.endswith("continue")
        assert OUTPUT_DISCIPLINE_DIGEST in spec.app_server_plan.prompt

    def test_resume_cmd_prepends_discipline_digests(self) -> None:
        from autoskillit.core import CODEX_INTAKE_DISCIPLINE_DIGEST, OUTPUT_DISCIPLINE_DIGEST

        spec = CodexBackend().build_resume_cmd(
            resume_session_id="sess-123", prompt="continue working"
        )
        prompt = spec.app_server_plan.prompt
        assert prompt.startswith(OUTPUT_DISCIPLINE_DIGEST)
        assert CODEX_INTAKE_DISCIPLINE_DIGEST in prompt
        assert prompt.endswith("continue working")
        assert spec.app_server_plan.developer_instructions is None

    def test_build_resume_cmd_empty_id_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            CodexBackend().build_resume_cmd(resume_session_id="", prompt="continue")

    def test_build_resume_cmd_with_env_extras(self) -> None:
        spec = CodexBackend().build_resume_cmd(
            resume_session_id="s1", prompt="go", env_extras={"FOO": "bar"}
        )
        assert spec.env.get("FOO") == "bar"

    def test_build_resume_cmd_pins_explicit_session_home(self) -> None:
        spec = CodexBackend().build_resume_cmd(
            resume_session_id="sess-123",
            prompt="continue",
            plugin_binding=plugin_binding(Path("/plugin")),
            env_extras={"CODEX_HOME": "/caller-home", "CODEX_SQLITE_HOME": "/caller-home"},
            session_home="/session-home",
        )
        assert spec.env["CODEX_HOME"] == "/session-home"
        assert spec.env["CODEX_SQLITE_HOME"] == "/session-home"

    def test_build_resume_cmd_does_not_select_plugin_home_without_session_home(self) -> None:
        """Part D: 'Do not select a plugin projection as CODEX_HOME' for resume —
        without an explicit/managed home, resume proceeds against the selected
        native home and registers no managed root at all."""
        spec = CodexBackend().build_resume_cmd(
            resume_session_id="sess-123",
            prompt="continue",
            plugin_binding=plugin_binding(Path("/plugin")),
        )
        assert "CODEX_HOME" not in spec.env
        assert "CODEX_SQLITE_HOME" not in spec.env
        assert spec.app_server_plan.session_home == ""

    def test_build_resume_cmd_env_uses_filtered_base(self, monkeypatch) -> None:
        monkeypatch.setenv("PATH", "/usr/bin")
        spec = CodexBackend().build_resume_cmd(resume_session_id="s1", prompt="go")
        assert "PATH" in spec.env

    def test_build_resume_cmd_no_json_flag(self) -> None:
        spec = CodexBackend().build_resume_cmd(resume_session_id="s1", prompt="go")
        assert "--json" not in spec.cmd

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
        assert CodexBackend().write_tool_names() == frozenset({"file_change"})

    def test_write_tool_names_includes_file_change(self) -> None:
        names = CodexBackend().write_tool_names()
        assert "file_change" in names

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
            ("inspector_capable", False),
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
            "app-server",
            "--listen",
            "stdio://",
            "-c",
            "features.image_generation=false",
        )

    def test_sandbox_with_workspace_write_in_plan(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert "--sandbox" not in spec.cmd
        assert spec.app_server_plan is not None
        assert spec.app_server_plan.sandbox == "workspace-write"

    def test_no_approval_never_in_headless_cmd(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert "-a" not in spec.cmd
        assert "never" not in spec.cmd
        assert spec.app_server_plan.approval_policy == "never"

    def test_model_carried_on_plan(self) -> None:
        spec = CodexBackend().build_headless_cmd("x", model="o3")
        assert "--model" not in spec.cmd
        assert spec.app_server_plan.model == CodexBackend().translate_model("o3")

    def test_add_dir_becomes_runtime_workspace_root(self) -> None:
        spec = CodexBackend().build_headless_cmd("x", add_dirs=["/extra"])
        assert "--add-dir" not in spec.cmd
        assert spec.app_server_plan.runtime_workspace_roots == ("/extra",)

    def test_multiple_add_dirs_become_runtime_workspace_roots(self) -> None:
        spec = CodexBackend().build_headless_cmd("x", add_dirs=["/a", "/b"])
        assert spec.app_server_plan.runtime_workspace_roots == ("/a", "/b")

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

    def test_bypass_hook_trust_absent_from_argv_true_in_plan(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert "--dangerously-bypass-hook-trust" not in spec.cmd
        assert spec.app_server_plan.bypass_hook_trust is True

    def test_no_catalog_registered_no_managed_skill_catalog(self) -> None:
        """Ordinary headless launches have no managed catalog and register no
        extra skill root — the server's native home is used as-is."""
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert spec.managed_skill_catalog is None
        assert spec.app_server_plan.catalog_root == ""
        assert spec.app_server_plan.expected_skill_names == frozenset()
        assert spec.app_server_plan.expected_skill_entries == ()

    def test_empty_home_sentinel_when_no_finalized_codex_home(self) -> None:
        # The central `_scrub_ambient_env` autouse fixture already scrubs
        # CODEX_HOME from every test's environment; no explicit delenv needed.
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert spec.app_server_plan.session_home == ""

    def test_explicit_home_resolved_from_finalized_env(self, monkeypatch) -> None:
        """CODEX_HOME is one of _HEADLESS_EXCLUSIVE_VARS (stripped from the base env
        this builder assembles for the child, to block host leakage) and one of the
        reserved keys _merge_caller_env_extras always blocks from caller extras — so
        an explicit ambient home is read directly off this process's own environment
        and re-injected as the child's reserved home keys."""
        monkeypatch.setenv("CODEX_HOME", "/tmp/explicit-codex-home")
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert spec.app_server_plan.session_home == "/tmp/explicit-codex-home"
        assert spec.env["CODEX_HOME"] == "/tmp/explicit-codex-home"
        assert spec.env["CODEX_SQLITE_HOME"] == "/tmp/explicit-codex-home"


class TestCodexResumeCmd:
    def test_app_server_cmd_structure(self) -> None:
        spec = CodexBackend().build_resume_cmd(resume_session_id="abc123", prompt="continue")
        assert spec.cmd == (
            "codex",
            "app-server",
            "--listen",
            "stdio://",
            "-c",
            "features.image_generation=false",
        )

    def test_resume_thread_id_carried_on_plan(self) -> None:
        spec = CodexBackend().build_resume_cmd(resume_session_id="abc123", prompt="continue")
        assert spec.app_server_plan is not None
        assert spec.app_server_plan.resume_thread_id == "abc123"

    def test_discipline_suffix_prepended_to_plan_prompt(self) -> None:
        """Mirrors build_skill_session_cmd's own composition: the discipline suffix
        is prepended into the plan's prompt field, not a separate channel."""
        from autoskillit.core import OUTPUT_DISCIPLINE_DIGEST

        spec = CodexBackend().build_resume_cmd(resume_session_id="abc123", prompt="continue")
        assert spec.app_server_plan.developer_instructions is None
        assert spec.app_server_plan.prompt.startswith(OUTPUT_DISCIPLINE_DIGEST)
        assert spec.app_server_plan.prompt.endswith("continue")

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
        assert spec.app_server_plan.approval_policy == "never"

    def test_resume_cmd_sandbox_is_read_only_in_plan(self) -> None:
        spec = CodexBackend().build_resume_cmd(resume_session_id="abc123", prompt="continue")
        assert "--sandbox" not in spec.cmd
        assert spec.app_server_plan.sandbox == "read-only"

    def test_resume_cmd_uses_filtered_base_env(self, monkeypatch) -> None:
        from autoskillit.core import CODEX_MCP_ENV_FORWARD_VARS
        from autoskillit.execution.backends._backend_cmd_builder_base import SHARED_BASELINE_ENV
        from autoskillit.execution.runtime.commands import _HEADLESS_EXCLUSIVE_VARS

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaked")
        spec = CodexBackend().build_resume_cmd(resume_session_id="abc123", prompt="continue")
        reinjected = frozenset(SHARED_BASELINE_ENV.keys()) | CODEX_MCP_ENV_FORWARD_VARS
        leaking = (_HEADLESS_EXCLUSIVE_VARS - reinjected) & spec.env.keys()
        assert not leaking, f"_HEADLESS_EXCLUSIVE_VARS leaked into resume env: {leaking}"

    def test_bypass_hook_trust_absent_from_argv_true_in_plan(self) -> None:
        spec = CodexBackend().build_resume_cmd(resume_session_id="s1", prompt="go")
        assert "--dangerously-bypass-hook-trust" not in spec.cmd
        assert spec.app_server_plan.bypass_hook_trust is True

    def test_no_managed_catalog_no_registration(self) -> None:
        spec = CodexBackend().build_resume_cmd(resume_session_id="abc123", prompt="continue")
        assert spec.managed_skill_catalog is None
        assert spec.app_server_plan.catalog_root == ""
        assert spec.app_server_plan.expected_skill_names == frozenset()

    def test_no_plugin_projection_selected_as_codex_home(self) -> None:
        """Part D removed CODEX_HOME-from-plugin-binding selection for resume; a
        non-managed resume preserves the selected native/explicit home only."""
        binding = plugin_binding(Path("/some-plugin-dir"))
        spec = CodexBackend().build_resume_cmd(
            resume_session_id="abc123", prompt="continue", plugin_binding=binding
        )
        assert "CODEX_HOME" not in spec.env
        assert spec.app_server_plan.session_home == ""

    def test_managed_catalog_requires_nonempty_session_home(self) -> None:
        catalog = ValidatedAddDir(
            path="/tmp/session/add-dir", session_home="", skill_entries=(("foo", "foo/SKILL.md"),)
        )
        with pytest.raises(ValueError, match="managed resume"):
            CodexBackend().build_resume_cmd(
                resume_session_id="abc123", prompt="continue", managed_skill_catalog=catalog
            )

    def test_managed_catalog_requires_nonempty_entries(self) -> None:
        catalog = ValidatedAddDir(
            path="/tmp/session/add-dir", session_home="/tmp/session", skill_entries=()
        )
        with pytest.raises(ValueError, match="managed resume"):
            CodexBackend().build_resume_cmd(
                resume_session_id="abc123", prompt="continue", managed_skill_catalog=catalog
            )

    def test_managed_catalog_registers_frozen_catalog(self) -> None:
        catalog = ValidatedAddDir(
            path="/tmp/session/add-dir",
            session_home="/tmp/session",
            skill_entries=(("foo", "foo/SKILL.md"),),
        )
        spec = CodexBackend().build_resume_cmd(
            resume_session_id="abc123", prompt="continue", managed_skill_catalog=catalog
        )
        assert spec.managed_skill_catalog == catalog
        assert spec.app_server_plan.session_home == "/tmp/session"
        assert spec.app_server_plan.catalog_root == "/tmp/session/add-dir/skills"
        assert spec.app_server_plan.expected_skill_names == frozenset({"foo"})
        assert spec.app_server_plan.expected_skill_entries == (("foo", "foo/SKILL.md"),)
        assert spec.env["CODEX_HOME"] == "/tmp/session"
        assert spec.env["CODEX_SQLITE_HOME"] == "/tmp/session"

    def test_managed_catalog_and_explicit_session_home_must_agree(self) -> None:
        catalog = ValidatedAddDir(
            path="/tmp/session/add-dir",
            session_home="/tmp/session",
            skill_entries=(("foo", "foo/SKILL.md"),),
        )
        with pytest.raises(ValueError, match="does not agree"):
            CodexBackend().build_resume_cmd(
                resume_session_id="abc123",
                prompt="continue",
                managed_skill_catalog=catalog,
                session_home="/tmp/other-session",
            )


class TestCodexHeadlessCmdEnv:
    def test_headless_cmd_uses_filtered_base_env(self, monkeypatch) -> None:
        from autoskillit.core import CODEX_MCP_ENV_FORWARD_VARS
        from autoskillit.execution.runtime.commands import _HEADLESS_EXCLUSIVE_VARS

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaked")
        spec = CodexBackend().build_headless_cmd("do stuff")
        leaking = (_HEADLESS_EXCLUSIVE_VARS - CODEX_MCP_ENV_FORWARD_VARS) & spec.env.keys()
        assert not leaking, f"_HEADLESS_EXCLUSIVE_VARS leaked into headless env: {leaking}"


class TestCodexBuildSkillSessionCmd:
    BASE: dict[str, object] = {
        "skill_command": "/test-skill",
        "cwd": "/work",
        "completion_marker": "%%DONE%%",
        "model": None,
        "plugin_binding": None,
        "output_format": OutputFormat.JSON,
        "add_dirs": (
            ValidatedAddDir(
                path="/work/add-dir",
                session_home="/work",
                skill_entries=(("test-skill", "test-skill/SKILL.md"),),
            ),
        ),
    }

    def test_completion_directive_injected(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert spec.app_server_plan is not None
        assert "ORCHESTRATION DIRECTIVE" in spec.app_server_plan.prompt

    def test_cwd_anchor_injected(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert spec.app_server_plan is not None
        assert "/work" in spec.app_server_plan.prompt

    def test_narration_suppression_injected(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert spec.app_server_plan is not None
        assert "EFFICIENCY DIRECTIVE" in spec.app_server_plan.prompt

    def test_fresh_headless_includes_output_discipline_digest(self) -> None:
        from autoskillit.core import OUTPUT_DISCIPLINE_DIGEST

        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert spec.app_server_plan is not None
        assert OUTPUT_DISCIPLINE_DIGEST in spec.app_server_plan.prompt

    def test_fresh_headless_includes_intake_discipline_digest(self) -> None:
        from autoskillit.core import CODEX_INTAKE_DISCIPLINE_DIGEST

        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert spec.app_server_plan is not None
        assert CODEX_INTAKE_DISCIPLINE_DIGEST in spec.app_server_plan.prompt

    def test_fresh_headless_excludes_scope_discipline_digest_by_default(self) -> None:
        """Scope discipline is a change-authoring policy; default sessions don't get it (#4478)."""
        from autoskillit.core import CODEX_SCOPE_DISCIPLINE_DIGEST

        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert spec.app_server_plan is not None
        assert CODEX_SCOPE_DISCIPLINE_DIGEST not in spec.app_server_plan.prompt

    def test_fresh_headless_includes_scope_discipline_digest_when_opted_in(self) -> None:
        from autoskillit.core import CODEX_SCOPE_DISCIPLINE_DIGEST

        spec = CodexBackend().build_skill_session_cmd(**self.BASE, include_scope_discipline=True)
        assert spec.app_server_plan is not None
        assert CODEX_SCOPE_DISCIPLINE_DIGEST in spec.app_server_plan.prompt

    def test_completion_reminder_injected(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert spec.app_server_plan is not None
        assert "Remember: end your final response with" in spec.app_server_plan.prompt

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
        dirs = [
            ValidatedAddDir(
                path="/extra/add-dir",
                session_home="/extra",
                skill_entries=(("test-skill", "test-skill/SKILL.md"),),
            )
        ]
        spec = CodexBackend().build_skill_session_cmd(
            **{**self.BASE, "add_dirs": dirs},
        )
        assert spec.env["CODEX_HOME"] == "/extra"
        assert spec.env["CODEX_SQLITE_HOME"] == "/extra"
        assert not spec.env["CODEX_HOME"].startswith("/dev/shm"), (
            "CODEX_HOME must not point to volatile tmpfs"
        )

    def test_skill_session_rejects_add_dir_without_session_home(self) -> None:
        with pytest.raises(ValueError, match="session_home"):
            CodexBackend().build_skill_session_cmd(
                **{**self.BASE, "add_dirs": [ValidatedAddDir(path="/extra/add-dir")]},
            )

    def test_skill_session_home_vars_are_required_env(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(
            **{
                **self.BASE,
                "add_dirs": [
                    ValidatedAddDir(
                        path="/extra/add-dir",
                        session_home="/extra",
                        skill_entries=(("test-skill", "test-skill/SKILL.md"),),
                    )
                ],
            },
        )
        assert {"CODEX_HOME", "CODEX_SQLITE_HOME"} <= spec.env.keys()

    def test_codex_capabilities_session_dir_persistent(self) -> None:
        """CodexBackend declares session_dir_persistent=True for persistent roots."""
        caps = CodexBackend().capabilities
        assert caps.session_dir_persistent is True
        assert caps.cook_startup_observer_capable is True

    def test_no_add_dir_flag_with_add_dirs(self) -> None:
        dirs = [
            ValidatedAddDir(
                path="/extra/add-dir",
                session_home="/extra",
                skill_entries=(("test-skill", "test-skill/SKILL.md"),),
            )
        ]
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
        assert spec.app_server_plan is not None
        assert spec.app_server_plan.model == "o3"
        spec2 = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert spec2.app_server_plan is not None
        assert spec2.app_server_plan.model is None

    def test_resume_path(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(
            **{**self.BASE, "resume_session_id": "sess-abc123"},
        )
        assert spec.is_resume is True
        assert spec.app_server_plan is not None
        assert spec.app_server_plan.resume_thread_id == "sess-abc123"

    def test_completion_marker_with_profile(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(
            **{**self.BASE, "profile_name": "minimax"},
        )
        assert spec.env["AUTOSKILLIT_COMPLETION_MARKER"] == self.BASE["completion_marker"]
        spec2 = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert "AUTOSKILLIT_COMPLETION_MARKER" not in spec2.env

    def test_default_params_emit_no_warnings(self) -> None:
        with structlog.testing.capture_logs() as cap_logs:
            spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        warning_events = [e for e in cap_logs if e.get("event") == "codex_output_format_coerced"]
        assert warning_events == []
        assert "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY" not in spec.env
        assert "CLAUDE_STREAM_IDLE_TIMEOUT_MS" not in spec.env

    def test_stream_idle_timeout_routed_to_cmdspec(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(
            **{**self.BASE, "stream_idle_timeout_ms": 30000}
        )
        assert spec.process_idle_timeout_ms == 30000

    def test_cwd_set(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(
            **{**self.BASE, "cwd": "/my/project"},
        )
        assert spec.cwd == "/my/project"

    def test_headless_auto_gate_env_set(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert spec.env.get("AUTOSKILLIT_HEADLESS_AUTO_GATE") == "1"

    def test_skill_session_cmd_uses_filtered_base_env(self, monkeypatch) -> None:
        from autoskillit.core import CODEX_MCP_ENV_FORWARD_VARS
        from autoskillit.execution.runtime.commands import _HEADLESS_EXCLUSIVE_VARS

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaked")
        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        reinjected = {
            "AUTOSKILLIT_SESSION_TYPE",
            "MAX_MCP_OUTPUT_TOKENS",
            "AUTOSKILLIT_SKILL_NAME",
            "AUTOSKILLIT_CWD",
            # Unconditionally re-injected from the mandatory add-dir's session_home,
            # never read from ambient environment.
            "CODEX_HOME",
        }
        leaking = (
            _HEADLESS_EXCLUSIVE_VARS - reinjected - CODEX_MCP_ENV_FORWARD_VARS
        ) & spec.env.keys()
        assert not leaking, f"_HEADLESS_EXCLUSIVE_VARS leaked into skill session env: {leaking}"

    def test_bypass_hook_trust_present_in_skill_session_cmd(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert spec.app_server_plan is not None
        assert spec.app_server_plan.bypass_hook_trust is True

    def test_app_server_plan_catalog_root_expected_names_and_cwd(self) -> None:
        """app_server_plan carries the catalog root under the frozen add-dir's
        session_home, the expected skill names/entries straight from that
        catalog, and the invocation's cwd — none of which live on argv."""
        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        assert spec.app_server_plan is not None
        plan = spec.app_server_plan
        assert plan.session_home == "/work"
        assert plan.catalog_root == "/work/add-dir/skills"
        assert plan.expected_skill_names == frozenset({"test-skill"})
        assert plan.expected_skill_entries == (("test-skill", "test-skill/SKILL.md"),)
        assert plan.cwd == "/work"

    def test_model_and_effort_forwarded_via_config_overrides(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(**{**self.BASE, "model": "sonnet"})
        assert spec.app_server_plan is not None
        plan = spec.app_server_plan
        assert plan.model == CodexBackend().translate_model("sonnet")
        assert plan.config_overrides["model_reasoning_effort"] == CODEX_EFFORT_MAPPING["sonnet"]

    def test_line_driver_returns_driver_bound_to_the_exact_plan(self) -> None:
        """CodexBackend.line_driver hands back a driver bound to the exact plan object
        the builder produced — never a copy or a re-derived plan."""
        from autoskillit.execution.backends._codex.app_server import CodexAppServerDriver

        spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        driver = CodexBackend().line_driver(spec)
        assert isinstance(driver, CodexAppServerDriver)
        assert driver._plan is spec.app_server_plan  # type: ignore[attr-defined]

    def test_adapter_digest_moves_with_app_server_plan_despite_shared_argv(self) -> None:
        """#4659 digest-divergence class: once prompt, catalog root, and resume
        thread id leave argv for the app-server transport, adapter_digest must
        still distinguish two launches that differ only in those app_server_plan
        fields (see _app_server_plan_digest_payload)."""
        import hashlib
        import json

        from autoskillit.execution.headless._managed._launch_adapter import (
            _app_server_plan_digest_payload,
        )

        def digest_of(spec: CmdSpec) -> str:
            payload = {
                "argv": spec.cmd,
                "app_server_plan": _app_server_plan_digest_payload(spec.app_server_plan),
            }
            return hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()

        base_spec = CodexBackend().build_skill_session_cmd(**self.BASE)
        base_digest = digest_of(base_spec)
        other_catalog = ValidatedAddDir(
            path="/other/add-dir",
            session_home="/other",
            skill_entries=(("test-skill", "test-skill/SKILL.md"),),
        )
        variants: dict[str, dict[str, object]] = {
            "prompt": {**self.BASE, "skill_command": "/test-skill extra arg"},
            "catalog_root": {**self.BASE, "add_dirs": (other_catalog,)},
            "resume_thread_id": {**self.BASE, "resume_session_id": "original-thread-id"},
        }
        for field, kwargs in variants.items():
            varied_spec = CodexBackend().build_skill_session_cmd(**kwargs)
            assert varied_spec.cmd == base_spec.cmd, f"{field} variant must share argv"
            assert digest_of(varied_spec) != base_digest, (
                f"adapter_digest failed to move when only {field} changed"
            )

    def test_resumed_session_uses_current_invocation_home_and_overrides(self) -> None:
        """A retained skill-catalog closure restored for a resumed attempt is bound
        to the CURRENT invocation's session_home (never a prior attempt's),
        attested by its exact skill names/paths, while resuming the original
        Codex thread id and carrying only this call's own overrides."""
        original_thread_id = "original-thread-abc123"
        retained_entries = (
            ("investigate", "investigate/SKILL.md"),
            ("make-plan", "make-plan/SKILL.md"),
        )
        current_invocation_add_dirs = (
            ValidatedAddDir(
                path="/current/home/add-dir",
                session_home="/current/home",
                skill_entries=retained_entries,
            ),
        )
        spec = CodexBackend().build_skill_session_cmd(
            **{
                **self.BASE,
                "add_dirs": current_invocation_add_dirs,
                "resume_session_id": original_thread_id,
                "network_access": True,
            }
        )
        assert spec.is_resume is True
        assert spec.app_server_plan is not None
        plan = spec.app_server_plan
        assert plan.session_home == "/current/home"
        assert plan.catalog_root == "/current/home/add-dir/skills"
        assert plan.expected_skill_names == frozenset({"investigate", "make-plan"})
        assert plan.expected_skill_entries == retained_entries
        assert plan.resume_thread_id == original_thread_id
        assert plan.config_overrides["sandbox_workspace_write.network_access"] is True


class TestCodexBuildSkillSessionCmdConfigAdapter:
    def test_config_adapter_matches_flat_params(self) -> None:
        add_dirs = (
            ValidatedAddDir(
                path="/work/add-dir",
                session_home="/work",
                skill_entries=(("test-skill", "test-skill/SKILL.md"),),
            ),
        )
        config = SkillSessionConfig(
            completion_marker="%%DONE%%",
            model=None,
            plugin_binding=None,
            output_format=OutputFormat.JSON,
            add_dirs=add_dirs,
        )
        via_config = CodexBackend().build_skill_session_cmd(
            "/test-skill", cwd="/work", config=config
        )
        via_flat = CodexBackend().build_skill_session_cmd(
            skill_command="/test-skill",
            cwd="/work",
            completion_marker="%%DONE%%",
            model=None,
            plugin_binding=None,
            output_format=OutputFormat.JSON,
            add_dirs=add_dirs,
        )
        assert via_config.cmd == via_flat.cmd
        assert via_config.env == via_flat.env
        assert via_config.cwd == via_flat.cwd

    def test_config_adapter_forwards_all_fields(self) -> None:
        chk = SessionCheckpoint(step_name="chk")
        add_dirs = (
            ValidatedAddDir(
                path="/tmp/add-dir",
                session_home="/tmp",
                skill_entries=(("test", "test/SKILL.md"),),
            ),
        )
        config = SkillSessionConfig(
            completion_marker="%%MARKER%%",
            model="o3",
            plugin_binding=None,
            output_format=OutputFormat.STREAM_JSON,
            add_dirs=add_dirs,
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
            sandbox_mode="read-only",
        )
        via_config = CodexBackend().build_skill_session_cmd("/test", cwd="/tmp", config=config)
        via_flat = CodexBackend().build_skill_session_cmd(
            skill_command="/test",
            cwd="/tmp",
            completion_marker="%%MARKER%%",
            model="o3",
            plugin_binding=None,
            output_format=OutputFormat.STREAM_JSON,
            add_dirs=add_dirs,
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
            sandbox_mode="read-only",
        )
        assert via_config.cmd == via_flat.cmd
        assert via_config.env == via_flat.env

    def test_config_sandbox_mode_propagates(self) -> None:
        config = SkillSessionConfig(
            sandbox_mode="read-only",
            add_dirs=(
                ValidatedAddDir(
                    path="/tmp/add-dir",
                    session_home="/tmp",
                    skill_entries=(("test", "test/SKILL.md"),),
                ),
            ),
        )
        spec = CodexBackend().build_skill_session_cmd("/test", cwd="/tmp", config=config)
        assert spec.app_server_plan is not None
        assert spec.app_server_plan.sandbox == "read-only"

    def test_workspace_write_parent_omits_cli_sandbox_override(self) -> None:
        config = SkillSessionConfig(
            sandbox_mode="workspace-write",
            add_dirs=(
                ValidatedAddDir(
                    path="/tmp/add-dir",
                    session_home="/tmp",
                    skill_entries=(("test", "test/SKILL.md"),),
                ),
            ),
        )
        spec = CodexBackend().build_skill_session_cmd("/test", cwd="/tmp", config=config)
        assert "--sandbox" not in spec.cmd
        assert spec.app_server_plan is not None
        assert spec.app_server_plan.sandbox == "workspace-write"

    def test_config_path_returns_cmdspec(self) -> None:
        config = SkillSessionConfig(
            completion_marker="%%DONE%%",
            output_format=OutputFormat.JSON,
            add_dirs=(
                ValidatedAddDir(
                    path="/tmp/add-dir",
                    session_home="/tmp",
                    skill_entries=(("test", "test/SKILL.md"),),
                ),
            ),
        )
        result = CodexBackend().build_skill_session_cmd("/test", cwd="/tmp", config=config)
        assert isinstance(result, CmdSpec)
        assert isinstance(result.cmd, tuple)

    def test_config_add_dir_session_home_governs_codex_home_over_plugin_binding(self) -> None:
        """Skill sessions now source CODEX_HOME from the add-dir's session_home;
        plugin_binding no longer participates for this builder (it's superseded by
        the mandatory add-dir catalog binding, unlike build_food_truck_cmd)."""
        config = SkillSessionConfig(
            completion_marker="%%DONE%%",
            output_format=OutputFormat.STREAM_JSON,
            plugin_binding=plugin_binding(Path("/p")),
            add_dirs=(
                ValidatedAddDir(
                    path="/work/add-dir",
                    session_home="/work",
                    skill_entries=(("test", "test/SKILL.md"),),
                ),
            ),
        )
        spec = CodexBackend().build_skill_session_cmd("/test", cwd="/work", config=config)
        cmd_str = " ".join(spec.cmd)
        assert "--output-format" not in cmd_str
        assert "--plugin-dir" not in cmd_str
        assert spec.env["CODEX_HOME"] == "/work"

    def test_legacy_flat_params_still_work(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(
            skill_command="/test-skill",
            cwd="/work",
            completion_marker="%%DONE%%",
            model=None,
            plugin_binding=None,
            output_format=OutputFormat.JSON,
            add_dirs=(
                ValidatedAddDir(
                    path="/work/add-dir",
                    session_home="/work",
                    skill_entries=(("test-skill", "test-skill/SKILL.md"),),
                ),
            ),
        )
        assert isinstance(spec, CmdSpec)
        assert spec.app_server_plan is not None
        assert "$test-skill" in spec.app_server_plan.prompt


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
        from autoskillit.execution.backends._claude_prompt import codex_discipline_suffix

        spec = CodexBackend().build_interactive_cmd(system_prompt="foo")
        overrides = [
            spec.cmd[i + 1] for i, v in enumerate(spec.cmd[:-1]) if v == CodexFlags.CONFIG_OVERRIDE
        ]
        rendered = next(
            value.partition("=")[2]
            for value in overrides
            if value.startswith("developer_instructions=")
        )
        parsed = tomllib.loads(f"developer_instructions = {rendered}")
        assert parsed["developer_instructions"] == (
            f"foo\n\n{codex_discipline_suffix(include_scope=True)}"
        )
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

    def test_generated_home_is_distinct_from_add_dirs(self, tmp_path: Path) -> None:
        generated_home = (tmp_path / "generated-home").resolve()
        skills_dir = (tmp_path / "skills").resolve()

        spec = CodexBackend().build_interactive_cmd(
            add_dirs=[str(skills_dir)],
            generated_home=generated_home,
        )

        assert spec.env["CODEX_HOME"] == str(generated_home)
        assert spec.env["CODEX_SQLITE_HOME"] == str(generated_home)
        assert str(skills_dir) in spec.cmd
        assert str(generated_home) not in [
            spec.cmd[index + 1]
            for index, value in enumerate(spec.cmd[:-1])
            if value == CodexFlags.ADD_DIR
        ]
        assert f'sqlite_home="{generated_home}"' in spec.cmd

    def test_initial_prompt_is_final_element(self) -> None:
        spec = CodexBackend().build_interactive_cmd(initial_prompt="hello")
        assert spec.cmd[-1] == "hello"

    def test_plugin_binding_is_delivered_through_codex_home(self) -> None:
        from pathlib import Path

        spec = CodexBackend().build_interactive_cmd(plugin_binding=plugin_binding(Path("/x")))
        assert "--plugin-dir" not in spec.cmd
        assert "/x" not in spec.cmd
        assert spec.env["CODEX_HOME"] == "/x"

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
        "plugin_binding": None,
        "output_format": OutputFormat.JSON,
        "add_dirs": (
            ValidatedAddDir(
                path="/work/add-dir",
                session_home="/work",
                skill_entries=(("test-skill", "test-skill/SKILL.md"),),
            ),
        ),
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
        "plugin_binding": None,
        "output_format": OutputFormat.JSON,
        "add_dirs": (
            ValidatedAddDir(
                path="/work/add-dir",
                session_home="/work",
                skill_entries=(("test-skill", "test-skill/SKILL.md"),),
            ),
        ),
    }

    FOOD_TRUCK_BASE: dict[str, object] = {
        "orchestrator_prompt": "dispatch the work",
        "plugin_binding": plugin_binding(Path("/pkg")),
        "cwd": "/work",
        "completion_marker": "%%DONE%%",
        "managed_skill_catalog": ValidatedAddDir(
            path="/work/add-dir",
            session_home="/work",
            skill_entries=(("test-skill", "test-skill/SKILL.md"),),
        ),
    }

    def test_skill_session_has_dynaconf_backend(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(**self.SKILL_BASE)
        assert spec.env["AUTOSKILLIT_AGENT_BACKEND__BACKEND"] == "codex"

    def test_food_truck_has_dynaconf_backend(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.FOOD_TRUCK_BASE)
        assert spec.env["AUTOSKILLIT_AGENT_BACKEND__BACKEND"] == "codex"


class TestCodexBuildFoodTruckCmd:
    _CATALOG = ValidatedAddDir(
        path="/work/add-dir",
        session_home="/work",
        skill_entries=(("test", "test/SKILL.md"),),
    )
    BASE: dict[str, object] = {
        "orchestrator_prompt": "dispatch the work",
        "plugin_binding": plugin_binding(Path("/pkg")),
        "cwd": "/work",
        "completion_marker": "%%DONE%%",
        "managed_skill_catalog": _CATALOG,
    }

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)

    # --- Structural / flag tests (app-server transport, non-resume) ---

    def test_cmd_0_is_codex(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert spec.cmd[0] == "codex"

    def test_app_server_cmd_prefix(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert spec.cmd[:4] == ("codex", "app-server", "--listen", "stdio://")

    def test_no_json_flag(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert "--json" not in spec.cmd

    def test_no_sandbox_flag_sandbox_is_read_only_in_plan(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert "--sandbox" not in spec.cmd
        assert spec.app_server_plan is not None
        assert spec.app_server_plan.sandbox == "read-only"

    def test_config_override_web_search_disabled_in_plan(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert spec.app_server_plan.config_overrides["web_search"] == "disabled"
        # not on argv: sandbox/hook-trust/prompt/config no longer live in -c overrides
        assert "web_search=disabled" not in _config_overrides(spec)

    def test_approval_never(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert "-a" not in spec.cmd
        assert "never" not in spec.cmd
        assert spec.app_server_plan.approval_policy == "never"

    def test_no_add_dir_flag(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert "--add-dir" not in spec.cmd

    def test_no_plugin_dir_flag(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert "--plugin-dir" not in spec.cmd

    def test_no_resume_thread_id_when_none(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert spec.app_server_plan.resume_thread_id == ""

    def test_food_truck_without_managed_catalog_raises(self) -> None:
        base_without_catalog = {k: v for k, v in self.BASE.items() if k != "managed_skill_catalog"}
        with pytest.raises(ValueError, match="managed catalog"):
            CodexBackend().build_food_truck_cmd(**base_without_catalog)

    def test_food_truck_with_catalog_missing_session_home_raises(self) -> None:
        bad_catalog = ValidatedAddDir(
            path="/work/add-dir", session_home="", skill_entries=(("test", "test/SKILL.md"),)
        )
        with pytest.raises(ValueError, match="managed catalog"):
            CodexBackend().build_food_truck_cmd(
                **{**self.BASE, "managed_skill_catalog": bad_catalog}
            )

    def test_food_truck_with_catalog_missing_entries_raises(self) -> None:
        bad_catalog = ValidatedAddDir(path="/work/add-dir", session_home="/work", skill_entries=())
        with pytest.raises(ValueError, match="managed catalog"):
            CodexBackend().build_food_truck_cmd(
                **{**self.BASE, "managed_skill_catalog": bad_catalog}
            )

    def test_managed_catalog_registered_on_plan_and_spec(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert spec.managed_skill_catalog == self._CATALOG
        assert spec.app_server_plan.catalog_root == "/work/add-dir/skills"
        assert spec.app_server_plan.expected_skill_names == frozenset({"test"})
        assert spec.app_server_plan.expected_skill_entries == (("test", "test/SKILL.md"),)

    def test_fresh_food_truck_receives_bound_catalog_home_not_plugin_binding(self) -> None:
        """Part D removed the CODEX_HOME-from-plugin-binding selection for food trucks —
        CODEX_HOME now comes exclusively from the bound managed catalog's session_home."""
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert spec.env["CODEX_HOME"] == "/work"
        assert spec.env["CODEX_SQLITE_HOME"] == "/work"
        assert spec.app_server_plan.session_home == "/work"

    def test_food_truck_forwards_explicit_inspector_model(self) -> None:
        from autoskillit.core import FLEET_INSPECTOR_MODEL_ENV_VAR

        spec = CodexBackend().build_food_truck_cmd(
            **self.BASE,
            env_extras={FLEET_INSPECTOR_MODEL_ENV_VAR: "configured-inspector"},
        )
        assert spec.env[FLEET_INSPECTOR_MODEL_ENV_VAR] == "configured-inspector"

    def test_mcp_tools_only_prompt_reinforcement(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert "ORCHESTRATION DIRECTIVE" in spec.app_server_plan.prompt

    def test_fresh_orchestrator_includes_output_discipline_digest(self) -> None:
        from autoskillit.core import OUTPUT_DISCIPLINE_DIGEST

        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert OUTPUT_DISCIPLINE_DIGEST in spec.app_server_plan.prompt

    def test_food_truck_includes_intake_discipline_digest(self) -> None:
        from autoskillit.core import CODEX_INTAKE_DISCIPLINE_DIGEST

        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert CODEX_INTAKE_DISCIPLINE_DIGEST in spec.app_server_plan.prompt

    def test_food_truck_excludes_scope_discipline_digest(self) -> None:
        """Orchestrators dispatch run_skill calls; they never author code changes (#4478)."""
        from autoskillit.core import CODEX_SCOPE_DISCIPLINE_DIGEST

        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert CODEX_SCOPE_DISCIPLINE_DIGEST not in spec.app_server_plan.prompt

    def test_prompt_carried_on_plan(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert "dispatch the work" in spec.app_server_plan.prompt

    def test_no_production_builder_emits_prompt_as_argv_positional(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert "dispatch the work" not in spec.cmd

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

    def test_no_resume_argv_positional(self) -> None:
        """Resume no longer flows through the codex-exec 'resume <id>' argv shape —
        thread continuity is a JSON-RPC thread/resume field on the plan."""
        spec = CodexBackend().build_food_truck_cmd(
            **{**self.BASE, "resume_session_id": "sess-abc"},
        )
        assert "resume" not in spec.cmd
        assert "sess-abc" not in spec.cmd

    def test_resumed_food_truck_receives_same_bound_catalog_home(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(
            **{**self.BASE, "resume_session_id": "sess-abc"},
        )
        assert spec.env["CODEX_HOME"] == "/work"

    def test_resume_session_id_carried_on_plan(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(
            **{**self.BASE, "resume_session_id": "sess-abc"},
        )
        assert spec.app_server_plan.resume_thread_id == "sess-abc"

    def test_resume_prompt_carried_on_plan(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(
            **{**self.BASE, "resume_session_id": "sess-abc"},
        )
        assert "dispatch the work" in spec.app_server_plan.prompt

    def test_resume_cmd_structure(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(
            **{**self.BASE, "resume_session_id": "sess-abc"},
        )
        assert spec.cmd[:4] == ("codex", "app-server", "--listen", "stdio://")
        assert spec.app_server_plan.resume_thread_id == "sess-abc"
        assert "dispatch the work" in spec.app_server_plan.prompt
        assert spec.managed_skill_catalog == self._CATALOG

    def test_food_truck_cmd_uses_filtered_base_env(self, monkeypatch) -> None:
        from autoskillit.core import CODEX_MCP_ENV_FORWARD_VARS
        from autoskillit.execution.runtime.commands import _HEADLESS_EXCLUSIVE_VARS

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaked")
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        reinjected = {
            "AUTOSKILLIT_SESSION_TYPE",
            "MAX_MCP_OUTPUT_TOKENS",
            "AUTOSKILLIT_CWD",
            "AUTOSKILLIT_COMPLETION_MARKER",
            "CODEX_HOME",
        }
        leaking = (
            _HEADLESS_EXCLUSIVE_VARS - reinjected - CODEX_MCP_ENV_FORWARD_VARS
        ) & spec.env.keys()
        assert not leaking, f"_HEADLESS_EXCLUSIVE_VARS leaked into food truck env: {leaking}"

    def test_bypass_hook_trust_absent_from_argv_present_in_plan(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(**self.BASE)
        assert "--dangerously-bypass-hook-trust" not in spec.cmd
        assert spec.app_server_plan.bypass_hook_trust is True
        assert spec.app_server_plan.config_overrides["bypass_hook_trust"] is True

    def test_stream_idle_timeout_routed_to_cmdspec(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(
            **{**self.BASE, "stream_idle_timeout_ms": 60000}
        )
        assert spec.process_idle_timeout_ms == 60000


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
        "plugin_binding": None,
        "output_format": OutputFormat.JSON,
        "add_dirs": (
            ValidatedAddDir(
                path="/work/add-dir",
                session_home="/work",
                skill_entries=(("test-skill", "test-skill/SKILL.md"),),
            ),
        ),
    }
    FOOD_TRUCK_BASE: dict[str, object] = {
        "orchestrator_prompt": "dispatch the work",
        "plugin_binding": plugin_binding(Path("/pkg")),
        "cwd": "/work",
        "completion_marker": "%%DONE%%",
        "managed_skill_catalog": ValidatedAddDir(
            path="/work/add-dir",
            session_home="/work",
            skill_entries=(("test-skill", "test-skill/SKILL.md"),),
        ),
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
        "plugin_binding": None,
        "output_format": OutputFormat.JSON,
        "add_dirs": (
            ValidatedAddDir(
                path="/work/add-dir",
                session_home="/work",
                skill_entries=(("test-skill", "test-skill/SKILL.md"),),
            ),
        ),
    }
    FOOD_TRUCK_BASE: dict[str, object] = {
        "orchestrator_prompt": "dispatch the work",
        "plugin_binding": plugin_binding(Path("/pkg")),
        "cwd": "/work",
        "completion_marker": "%%DONE%%",
        "managed_skill_catalog": ValidatedAddDir(
            path="/work/add-dir",
            session_home="/work",
            skill_entries=(("test-skill", "test-skill/SKILL.md"),),
        ),
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

    @pytest.mark.parametrize(
        "build",
        [
            lambda backend: backend.build_interactive_cmd(initial_prompt="work"),
            lambda backend: backend.build_headless_cmd("work"),
            lambda backend: backend.build_resume_cmd(resume_session_id="session-1", prompt="work"),
            lambda backend: backend.build_skill_session_cmd(
                **TestCodexMcpClientBackendRequired.SKILL_BASE
            ),
            lambda backend: backend.build_food_truck_cmd(
                **TestCodexMcpClientBackendRequired.FOOD_TRUCK_BASE
            ),
        ],
        ids=("interactive", "headless", "resume", "skill-session", "food-truck"),
    )
    def test_every_builder_overrides_inherited_mcp_backend(
        self,
        monkeypatch: pytest.MonkeyPatch,
        build,
    ) -> None:
        monkeypatch.setenv(MCP_CLIENT_BACKEND_ENV_VAR, "claude-code")

        spec = build(CodexBackend())

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

    def test_conventions_persistent_session_root(self) -> None:
        assert CodexBackend().conventions.persistent_session_root_subdir == Path("codex-sessions")


class TestClaudeCodeBackendProcessIdleDefault:
    def test_claude_code_backend_process_idle_default_zero(self) -> None:
        from autoskillit.execution.backends.claude import ClaudeCodeBackend

        spec = ClaudeCodeBackend().build_skill_session_cmd(
            "/test-skill",
            cwd="/work",
            completion_marker="%%DONE%%",
        )
        assert spec.process_idle_timeout_ms == 0


class TestCodexDiscardDispositions:
    """Codex builder parameter disposition contracts.

    plugin_binding -> preserved only for its content/authority/FD uses (Part D);
        neither build_food_truck_cmd nor build_skill_session_cmd select CODEX_HOME
        from it any more — both source CODEX_HOME exclusively from a bound managed
        catalog's session_home (the mandatory add-dir for skill sessions, the
        required managed_skill_catalog for food trucks).
    output_format -> logged warning when != JSON.
    exit_after_stop_delay_ms -> AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT env injection via setdefault.
    stream_idle_timeout_ms -> routed to CmdSpec.process_idle_timeout_ms + env injection.
    """

    SKILL_BASE: dict[str, object] = {
        "skill_command": "/test",
        "cwd": "/work",
        "completion_marker": "%%DONE%%",
        "add_dirs": (
            ValidatedAddDir(
                path="/work/add-dir",
                session_home="/work",
                skill_entries=(("test", "test/SKILL.md"),),
            ),
        ),
    }
    FOOD_TRUCK_BASE: dict[str, object] = {
        "orchestrator_prompt": "go",
        "plugin_binding": plugin_binding(Path("/pkg")),
        "cwd": "/work",
        "completion_marker": "%%DONE%%",
        "managed_skill_catalog": ValidatedAddDir(
            path="/work/add-dir",
            session_home="/work",
            skill_entries=(("test", "test/SKILL.md"),),
        ),
    }

    def test_plugin_binding_not_delivered_by_skill_builder(self) -> None:
        """Unlike build_food_truck_cmd, the skill builder ignores plugin_binding for
        CODEX_HOME — the mandatory add-dir's session_home is the sole authority."""
        spec = CodexBackend().build_skill_session_cmd(
            **self.SKILL_BASE,
            plugin_binding=plugin_binding(Path("/pkg")),
        )
        assert spec.env["CODEX_HOME"] == "/work"

    def test_plugin_binding_not_delivered_by_food_truck_builder(self) -> None:
        """Part D removed the CODEX_HOME-from-plugin-binding selection for food
        trucks — CODEX_HOME now comes exclusively from the bound managed catalog's
        session_home, while the plugin binding is preserved only for its FDs."""
        binding = plugin_binding(Path("/pkg"), inherited_fds=(7,))
        spec = CodexBackend().build_food_truck_cmd(
            **{**self.FOOD_TRUCK_BASE, "plugin_binding": binding}
        )
        assert spec.env["CODEX_HOME"] == "/work"
        assert spec.inherited_fds == (7,)

    def test_output_format_warning_skill_builder(self) -> None:
        with structlog.testing.capture_logs() as cap_logs:
            CodexBackend().build_skill_session_cmd(
                **self.SKILL_BASE,
                output_format=OutputFormat.STREAM_JSON,
            )
        events = [e for e in cap_logs if e.get("event") == "codex_output_format_coerced"]
        assert len(events) == 1
        assert events[0]["log_level"] == "warning"

    def test_output_format_warning_food_truck_builder(self) -> None:
        with structlog.testing.capture_logs() as cap_logs:
            CodexBackend().build_food_truck_cmd(
                **self.FOOD_TRUCK_BASE,
                output_format=OutputFormat.JSON,
            )
        events = [e for e in cap_logs if e.get("event") == "codex_output_format_coerced"]
        assert len(events) == 1
        assert events[0]["log_level"] == "warning"

    def test_exit_delay_injects_idle_timeout_skill_builder(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(
            **self.SKILL_BASE,
            exit_after_stop_delay_ms=5000,
        )
        assert spec.env["AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT"] == "5.0"

    def test_exit_delay_injects_idle_timeout_food_truck_builder(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(
            **self.FOOD_TRUCK_BASE,
            exit_after_stop_delay_ms=5000,
        )
        assert spec.env["AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT"] == "5.0"

    def test_stream_idle_injects_idle_timeout_skill_builder(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(
            **self.SKILL_BASE,
            stream_idle_timeout_ms=3000,
        )
        assert spec.env["AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT"] == "3.0"
        assert spec.process_idle_timeout_ms == 3000

    def test_stream_idle_injects_idle_timeout_food_truck_builder(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(
            **self.FOOD_TRUCK_BASE,
            stream_idle_timeout_ms=3000,
        )
        assert spec.env["AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT"] == "3.0"
        assert spec.process_idle_timeout_ms == 3000

    def test_stream_idle_routed_to_process_idle_skill_builder(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(
            **self.SKILL_BASE,
            stream_idle_timeout_ms=10000,
        )
        assert spec.process_idle_timeout_ms == 10000

    def test_stream_idle_routed_to_process_idle_food_truck_builder(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(
            **self.FOOD_TRUCK_BASE,
            stream_idle_timeout_ms=10000,
        )
        assert spec.process_idle_timeout_ms == 10000

    def test_zero_ms_no_idle_timeout_skill_builder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT", raising=False)
        spec = CodexBackend().build_skill_session_cmd(**self.SKILL_BASE)
        assert "AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT" not in spec.env

    def test_zero_ms_no_idle_timeout_food_truck_builder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT", raising=False)
        spec = CodexBackend().build_food_truck_cmd(**self.FOOD_TRUCK_BASE)
        assert "AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT" not in spec.env

    def test_existing_idle_timeout_not_overwritten_skill_builder(self) -> None:
        spec = CodexBackend().build_skill_session_cmd(
            **self.SKILL_BASE,
            exit_after_stop_delay_ms=5000,
            provider_extras={"AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT": "99.0"},
        )
        assert spec.env["AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT"] == "99.0"

    def test_existing_idle_timeout_not_overwritten_food_truck_builder(self) -> None:
        spec = CodexBackend().build_food_truck_cmd(
            **self.FOOD_TRUCK_BASE,
            exit_after_stop_delay_ms=5000,
            env_extras={"AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT": "99.0"},
        )
        assert spec.env["AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT"] == "99.0"

    def test_no_warnings_on_defaults_skill_builder(self) -> None:
        with structlog.testing.capture_logs() as cap_logs:
            CodexBackend().build_skill_session_cmd(**self.SKILL_BASE)
        warning_events = [e for e in cap_logs if e.get("event") == "codex_output_format_coerced"]
        assert warning_events == []


class TestCodexBackendEnsurePreLaunchStageTagging:
    """No existing test drives a failure through ensure_pre_launch's composed
    transaction — every existing caller only exercises the success path. The
    4 sub-steps (source-config sync, hook update; then, mutually exclusive per
    call, snapshot write or native home validation) each get their own stage
    prefix inside the single ``errors`` tuple element, since ``PreLaunchReadiness``
    has no dedicated ``stage`` field."""

    _CANONICAL_AUTOSKILLIT_MCP_CONFIG = (
        "[mcp_servers.autoskillit]\n"
        'command = "autoskillit"\n'
        'args = ["mcp"]\n'
        'env_vars = ["AUTOSKILLIT_HEADLESS_AUTO_GATE"]\n'
        "startup_timeout_sec = 20\n"
        "tool_timeout_sec = 30\n"
    )

    @pytest.fixture(autouse=True)
    def _setup_dirs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.fake_home = tmp_path / "fakehome"
        self.codex_home = self.fake_home / ".codex"
        self.codex_home.mkdir(parents=True)
        (self.codex_home / "config.toml").write_text(self._CANONICAL_AUTOSKILLIT_MCP_CONFIG)
        (self.codex_home / "auth.json").write_text("{}")
        self.session_dir = tmp_path / "session"
        self.session_dir.mkdir()
        (self.session_dir / "config.toml").write_text(self._CANONICAL_AUTOSKILLIT_MCP_CONFIG)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: self.fake_home))

    def test_source_config_sync_failure_is_tagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            _patch_backends__codex_prelaunch,
            "_ensure_codex_mcp_registered_unlocked",
            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        readiness = CodexBackend().ensure_pre_launch(session_dir=self.session_dir)
        assert len(readiness.errors) == 1
        assert "source-config sync" in readiness.errors[0]
        assert "boom" in readiness.errors[0]

    def test_hook_update_failure_is_tagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            _patch_backends__codex_prelaunch,
            "_sync_hooks_to_codex_config_unlocked",
            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        readiness = CodexBackend().ensure_pre_launch(session_dir=self.session_dir)
        assert len(readiness.errors) == 1
        assert "hook update" in readiness.errors[0]
        assert "boom" in readiness.errors[0]

    def test_snapshot_write_failure_is_tagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            _patch_backends_codex,
            "atomic_write",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        readiness = CodexBackend().ensure_pre_launch(session_dir=self.session_dir)
        assert len(readiness.errors) == 1
        assert "snapshot write" in readiness.errors[0]
        assert "boom" in readiness.errors[0]

    def test_native_home_validation_failure_is_tagged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            _patch_backends_codex,
            "_validate_global_codex_home",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        readiness = CodexBackend().ensure_pre_launch(session_dir=None)
        assert len(readiness.errors) == 1
        assert "native home validation" in readiness.errors[0]
        assert "boom" in readiness.errors[0]


class TestCodexBackendSetupSessionDir:
    _CANONICAL_AUTOSKILLIT_MCP_CONFIG = (
        "[mcp_servers.autoskillit]\n"
        'command = "autoskillit"\n'
        'args = ["mcp"]\n'
        'env_vars = ["AUTOSKILLIT_HEADLESS_AUTO_GATE"]\n'
        "startup_timeout_sec = 20\n"
        "tool_timeout_sec = 30\n"
    )

    @pytest.fixture(autouse=True)
    def _setup_dirs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.fake_home = tmp_path / "fakehome"
        self.codex_home = self.fake_home / ".codex"
        self.codex_home.mkdir(parents=True)
        self.session_dir = tmp_path / "session"
        self.session_dir.mkdir()
        (self.session_dir / "config.toml").write_text(self._CANONICAL_AUTOSKILLIT_MCP_CONFIG)
        self.fake_log_dir = tmp_path / "logs"
        monkeypatch.setattr(Path, "home", staticmethod(lambda: self.fake_home))
        monkeypatch.setattr(
            _patch_backends_codex,
            "default_log_dir",
            lambda: self.fake_log_dir,
        )

    def _write_all_source_files(self) -> None:
        (self.codex_home / "config.toml").write_text(self._CANONICAL_AUTOSKILLIT_MCP_CONFIG)
        (self.codex_home / "auth.json").write_text("{}")
        (self.codex_home / ".env").write_text("KEY=val\n")

    @staticmethod
    def _luna_definition(
        *,
        disabled_features: tuple[str, ...] = (),
        agents_enabled: bool = True,
    ) -> AgentDef:
        return AgentDef(
            name="semantic-code-navigator",
            description="Bounded semantic navigation",
            tools=("Read", "Grep", "Glob"),
            model="sonnet",
            max_turns=8,
            body="Return bounded evidence only.",
            codex=CodexAgentProjectionDef(
                "gpt-5.6-luna",
                "max",
                "read-only",
                disabled_features,
                agents_enabled,
            ),
        )

    @staticmethod
    def _explorer_definitions() -> tuple[AgentDef, ...]:
        names = {"semantic-code-navigator", "repository-impact-profiler"}
        definitions = tuple(
            definition
            for definition in load_agent_definitions(pkg_root() / "agents")
            if definition.name in names
        )
        assert {definition.name for definition in definitions} == names
        return definitions

    @staticmethod
    def _explorer_binding_envs(
        definitions: tuple[AgentDef, ...],
        *,
        generation: str,
    ) -> dict[str, dict[str, str]]:
        shared_binding = {
            "AUTOSKILLIT_EXPLORATION_CAPABILITY": f"capability-{generation}",
            "AUTOSKILLIT_EXPLORATION_ROLE": "shared-explorer-session",
            "AUTOSKILLIT_EXPLORATION_SESSION_ID": f"session-{generation}",
            "AUTOSKILLIT_EXPLORATION_AUTHORITY_PATH": (
                f"/authority/{generation}/shared-session.json"
            ),
        }
        return {definition.name: dict(shared_binding) for definition in definitions}

    @staticmethod
    def _materialize_pre_change_explorer_roles(
        session_dir: Path, definitions: tuple[AgentDef, ...]
    ) -> None:
        for definition in definitions:
            path = session_dir / "agents" / f"{definition.name}.toml"
            text = path.read_text(encoding="utf-8")
            assert 'web_search = "disabled"\n' in text
            assert "[features]\n" in text
            text = text.replace('web_search = "disabled"\n', "", 1)
            text = text.replace(
                "[features]\n",
                "[features]\n"
                + "\n".join(f"{feature} = false" for feature in RETIRED_CODEX_FEATURES)
                + "\n",
                1,
            )
            text = text.replace(agent_definition_digest(definition), "sha256:" + ("0" * 64))
            path.write_text(text, encoding="utf-8")

    def test_happy_path_all_files_provisioned(self) -> None:
        self._write_all_source_files()
        CodexBackend().setup_session_dir(self.session_dir)
        assert (self.session_dir / "config.toml").is_file()

    def test_direct_mcp_agent_rejects_malformed_transport_before_mutation(self) -> None:
        invalid_transport = "[mcp_servers.autoskillit]\n"
        (self.session_dir / "config.toml").write_text(invalid_transport)
        definition = next(
            definition
            for definition in load_agent_definitions(pkg_root() / "agents")
            if definition.name == "session-log-reader"
        )

        with pytest.raises(ValueError, match="requires exactly one canonical.*transport"):
            CodexBackend().setup_session_dir(
                self.session_dir,
                parent_sandbox_mode="read-only",
                agent_defs=(definition,),
            )

        assert (self.session_dir / "config.toml").read_text() == invalid_transport
        assert not (self.session_dir / "agents").exists()

    def test_bundled_direct_mcp_agent_rejects_malformed_transport_before_mutation(
        self,
    ) -> None:
        invalid_transport = "[mcp_servers.autoskillit]\n"
        (self.session_dir / "config.toml").write_text(invalid_transport)

        with pytest.raises(ValueError, match="requires exactly one canonical.*transport"):
            CodexBackend().setup_session_dir(self.session_dir)

        assert not (self.session_dir / "agents").exists()

    def test_session_log_reader_projects_direct_mcp_only_policy(self) -> None:
        self._write_all_source_files()
        CodexBackend().setup_session_dir(self.session_dir)

        reader = tomllib.loads(
            (self.session_dir / "agents" / "session-log-reader.toml").read_text()
        )
        assert reader["model"] == "gpt-5.6-luna"
        assert reader["model_reasoning_effort"] == "xhigh"
        assert reader["sandbox_mode"] == "read-only"
        assert reader["web_search"] == "disabled"
        assert reader["agents"] == {"enabled": False}
        assert reader["mcp_servers"]["autoskillit"]["enabled_tools"] == ["inspect_session_logs"]
        assert reader["features"]["shell_tool"] is False
        assert reader["features"]["multi_agent"] is False
        assert (self.session_dir / "auth.json").is_symlink()
        assert (self.session_dir / ".env").is_file()
        assert not (self.session_dir / "sessions").exists()
        assert not (self.session_dir / "archived_sessions").exists()

    @pytest.mark.parametrize("exact", [False, True], ids=["bundled", "exact"])
    def test_reader_eligible_agents_are_not_native_codex_agents(self, exact: bool) -> None:
        self._write_all_source_files()
        reader = next(
            definition
            for definition in load_agent_definitions(pkg_root() / "agents")
            if definition.name == "pr-source-reader"
        )

        kwargs = {"agent_defs": (reader,)} if exact else {}
        CodexBackend().setup_session_dir(self.session_dir, **kwargs)

        config = tomllib.loads((self.session_dir / "config.toml").read_text())
        assert "pr-source-reader" not in config.get("agents", {})
        assert not (self.session_dir / "agents" / "pr-source-reader.toml").exists()

    def test_missing_config_raises_and_logs_error(self) -> None:
        (self.session_dir / "config.toml").unlink()
        with pytest.raises(FileNotFoundError):
            CodexBackend().setup_session_dir(self.session_dir)

    def test_absent_auth_creates_durable_absolute_link(self) -> None:
        CodexBackend().setup_session_dir(self.session_dir)
        auth_link = self.session_dir / "auth.json"
        assert auth_link.is_symlink()
        assert auth_link.readlink().is_absolute()
        assert auth_link.readlink() == (self.codex_home / "auth.json").resolve(strict=False)
        assert not auth_link.exists()

    def test_orchestrator_auth_is_file_backed_across_generated_homes(self) -> None:
        (self.session_dir / "config.toml").write_text(
            'cli_auth_credentials_store = "keyring"\n' + self._CANONICAL_AUTOSKILLIT_MCP_CONFIG
        )
        backend = CodexBackend()
        backend.setup_session_dir(
            self.session_dir,
            execution_role=SkillExecutionRole.ORCHESTRATOR,
        )

        first_config = (self.session_dir / "config.toml").read_text()
        assert first_config.count('cli_auth_credentials_store = "file"') == 1
        assert tomllib.loads(first_config)["cli_auth_credentials_store"] == "file"
        first_link = self.session_dir / "auth.json"
        target = (self.codex_home / "auth.json").resolve(strict=False)
        assert first_link.is_symlink()
        assert first_link.readlink() == target

        fd = os.open(first_link, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)
        assert target.is_file()
        assert target.stat().st_mode & 0o777 == 0o600
        assert first_link.is_symlink()

        second_home = self.session_dir.parent / "second-session"
        second_home.mkdir()
        (second_home / "config.toml").write_text(self._CANONICAL_AUTOSKILLIT_MCP_CONFIG)
        backend.setup_session_dir(
            second_home,
            execution_role=SkillExecutionRole.ORCHESTRATOR,
        )
        assert (
            tomllib.loads((second_home / "config.toml").read_text())["cli_auth_credentials_store"]
            == "file"
        )
        assert (second_home / "auth.json").is_symlink()
        assert (second_home / "auth.json").readlink() == target
        assert target.is_file()

    def test_auth_destination_collision_fails_closed(self) -> None:
        (self.codex_home / "auth.json").write_text("{}")
        (self.session_dir / "auth.json").write_text("blocker")
        with pytest.raises(FileExistsError):
            CodexBackend().setup_session_dir(self.session_dir)
        assert not (self.session_dir / "auth.json").is_symlink()

    def test_absent_env_silently_skipped(self) -> None:
        CodexBackend().setup_session_dir(self.session_dir)
        assert not (self.session_dir / ".env").exists()

    def test_setup_does_not_manage_rollout_links(self) -> None:
        (self.session_dir / "sessions").mkdir()
        CodexBackend().setup_session_dir(self.session_dir)
        assert (self.session_dir / "sessions").is_dir()
        assert not (self.session_dir / "sessions").is_symlink()
        assert not (self.session_dir / "archived_sessions").exists()

    def test_setup_session_dir_creates_agents_directory(self) -> None:
        self._write_all_source_files()
        CodexBackend().setup_session_dir(self.session_dir)
        assert (self.session_dir / "agents").is_dir()

    def test_agent_toml_set_and_count_match_md_sources(self) -> None:
        self._write_all_source_files()
        CodexBackend().setup_session_dir(self.session_dir)
        toml_files = list((self.session_dir / "agents").glob("*.toml"))
        # Unbound setup excludes explorer roles (no bindings = not advertised)
        expected_names = {
            f"{definition.name}.toml"
            for definition in load_agent_definitions(pkg_root() / "agents")
            if definition.name not in BUNDLED_EXPLORER_ROLES and not definition.reader_tools
        }
        actual_names = {path.name for path in toml_files}
        assert actual_names == expected_names, (
            f"generated TOMLs {actual_names} != valid source set {expected_names}"
        )
        assert len(toml_files) == len(expected_names)

    def test_agent_toml_required_fields_present_and_nonempty(self) -> None:
        self._write_all_source_files()
        CodexBackend().setup_session_dir(self.session_dir)
        required_new_roles = {
            "friction-batch-scanner",
            "friction-category-analyzer",
            "pr-synthesizer",
            "research-source-reader",
            "research-synthesizer",
        }
        definitions = {
            definition.name: definition
            for definition in load_agent_definitions(pkg_root() / "agents")
        }
        generated_names = {path.stem for path in (self.session_dir / "agents").glob("*.toml")}
        assert required_new_roles <= generated_names
        for toml_path in sorted((self.session_dir / "agents").glob("*.toml")):
            data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
            assert data["name"], f"{toml_path.name}: name empty"
            assert data["description"], f"{toml_path.name}: description empty"
            assert data["description"] == definitions[toml_path.stem].description
            assert data["description"] != definitions[toml_path.stem].body
            if toml_path.stem in required_new_roles:
                assert data["description"].startswith("Use when ")
            assert data["developer_instructions"], (
                f"{toml_path.name}: developer_instructions empty"
            )
            assert data["instructions"] == data["developer_instructions"]
            from autoskillit.execution.backends._claude_prompt import codex_discipline_suffix

            assert (
                data["developer_instructions"]
                .rstrip()
                .endswith(codex_discipline_suffix().rstrip())
            )
            assert "AutoSkillit agent definition digest: sha256:" in data["developer_instructions"]
            expected_sandbox = definitions[toml_path.stem].codex.sandbox_mode
            assert data["sandbox_mode"] == expected_sandbox, (
                f"{toml_path.name}: wrong sandbox_mode"
            )

    def test_read_only_agent_tools_project_to_read_only_sandbox(self) -> None:
        self._write_all_source_files()
        CodexBackend().setup_session_dir(self.session_dir)
        definitions = load_agent_definitions(pkg_root() / "agents")
        read_only_names = {
            definition.name
            for definition in definitions
            if definition.name not in BUNDLED_EXPLORER_ROLES
            and not definition.reader_tools
            and definition.codex.sandbox_mode == "read-only"
        }
        assert WEB_EVIDENCE_RESEARCHER_ROLE in read_only_names
        for name in read_only_names:
            data = tomllib.loads(
                (self.session_dir / "agents" / f"{name}.toml").read_text(encoding="utf-8")
            )
            assert data["sandbox_mode"] == "read-only"

    def test_generated_agents_are_registered_in_session_config(self) -> None:
        self._write_all_source_files()
        CodexBackend().setup_session_dir(self.session_dir)
        config = tomllib.loads((self.session_dir / "config.toml").read_text(encoding="utf-8"))
        generated_names = {path.stem for path in (self.session_dir / "agents").glob("*.toml")}
        assert generated_names <= config["agents"].keys()
        wp_role = config["agents"]["wp-elaborator"]
        assert wp_role["config_file"] == "agents/wp-elaborator.toml"
        assert wp_role["description"]

    @pytest.mark.parametrize("parent_sandbox", ["read-only", "workspace-write"])
    def test_parent_sandbox_is_bound_without_preventing_child_narrowing(
        self, parent_sandbox: str
    ) -> None:
        self._write_all_source_files()
        CodexBackend().setup_session_dir(
            self.session_dir,
            parent_sandbox_mode=parent_sandbox,
        )
        config = tomllib.loads((self.session_dir / "config.toml").read_text(encoding="utf-8"))
        if parent_sandbox == "read-only":
            assert config["sandbox_mode"] == "read-only"
        else:
            assert "sandbox_mode" not in config

    def test_injected_luna_definition_is_the_only_generated_role(self) -> None:
        definition = self._luna_definition()
        CodexBackend().setup_session_dir(
            self.session_dir,
            parent_sandbox_mode="read-only",
            agent_defs=(definition,),
        )
        generated = tuple((self.session_dir / "agents").glob("*.toml"))
        assert [path.name for path in generated] == ["semantic-code-navigator.toml"]
        parsed = tomllib.loads(generated[0].read_text(encoding="utf-8"))
        assert parsed["model"] == "gpt-5.6-luna"
        assert parsed["model_reasoning_effort"] == "max"
        assert parsed["sandbox_mode"] == "read-only"
        assert "features" not in parsed
        assert "agents" not in parsed
        assert agent_definition_digest(definition) in parsed["instructions"]
        assert agent_definition_digest(definition) in parsed["developer_instructions"]

    def test_explorer_parent_and_roles_project_one_exact_shared_principal(self) -> None:
        definitions = self._explorer_definitions()
        binding_envs = self._explorer_binding_envs(definitions, generation="first")
        self._write_all_source_files()

        CodexBackend().setup_session_dir(
            self.session_dir,
            parent_sandbox_mode="read-only",
            agent_defs=definitions,
            explorer_binding_env=binding_envs,
        )

        shared_binding = next(iter(binding_envs.values()))
        expected_projection = {
            "command": "autoskillit",
            "args": ["mcp"],
            "env_vars": ["AUTOSKILLIT_HEADLESS_AUTO_GATE"],
            "startup_timeout_sec": 20,
            "tool_timeout_sec": 30,
            "enabled": True,
            "enabled_tools": [
                "submit_exploration_query",
                "get_exploration_page",
                "resume_exploration_context",
            ],
            "env": shared_binding,
        }
        role_projections = []
        for definition in definitions:
            parsed = tomllib.loads(
                (self.session_dir / "agents" / f"{definition.name}.toml").read_text(
                    encoding="utf-8"
                )
            )
            assert set(parsed["mcp_servers"]) == {"autoskillit"}
            projection = parsed["mcp_servers"]["autoskillit"]
            assert projection == expected_projection
            role_projections.append(projection)
            assert agent_definition_digest(definition) in parsed["developer_instructions"]
            assert parsed["model"] == "gpt-5.6-luna"
            assert parsed["model_reasoning_effort"] == "max"
            assert parsed["sandbox_mode"] == "read-only"

        parent_config = tomllib.loads(
            (self.session_dir / "config.toml").read_text(encoding="utf-8")
        )
        parent_projection = parent_config["mcp_servers"]["autoskillit"]
        assert parent_config["sandbox_mode"] == "read-only"
        assert parent_projection == expected_projection
        assert role_projections == [parent_projection, parent_projection]

    def test_explorer_setup_removes_ambient_parent_mcp_servers(self) -> None:
        definitions = self._explorer_definitions()
        binding_envs = self._explorer_binding_envs(definitions, generation="first")
        (self.session_dir / "config.toml").write_text(
            self._CANONICAL_AUTOSKILLIT_MCP_CONFIG
            + '\n[mcp_servers."ambient"]\ncommand = "ambient-mcp"\n',
            encoding="utf-8",
        )

        CodexBackend().setup_session_dir(
            self.session_dir,
            parent_sandbox_mode="read-only",
            agent_defs=definitions,
            explorer_binding_env=binding_envs,
        )

        parent = tomllib.loads((self.session_dir / "config.toml").read_text(encoding="utf-8"))
        assert set(parent["mcp_servers"]) == {"autoskillit"}
        for definition in definitions:
            role = tomllib.loads(
                (self.session_dir / "agents" / f"{definition.name}.toml").read_text(
                    encoding="utf-8"
                )
            )
            assert set(role["mcp_servers"]) == {"autoskillit"}

    def test_explorer_projection_rejects_missing_transport_before_mutation(self) -> None:
        definitions = self._explorer_definitions()
        binding_envs = self._explorer_binding_envs(definitions, generation="first")
        (self.session_dir / "config.toml").write_text("[mcp_servers.autoskillit]\n")

        with pytest.raises(ValueError, match="requires exactly one canonical.*transport"):
            CodexBackend().setup_session_dir(
                self.session_dir,
                parent_sandbox_mode="read-only",
                agent_defs=definitions,
                explorer_binding_env=binding_envs,
            )

        assert (self.session_dir / "config.toml").read_text(encoding="utf-8") == (
            "[mcp_servers.autoskillit]\n"
        )
        assert not (self.session_dir / "agents").exists()

    def test_explorer_projection_rejects_missing_role_binding_before_mutation(self) -> None:
        definitions = self._explorer_definitions()
        binding_envs = self._explorer_binding_envs(definitions, generation="first")
        binding_envs.pop("repository-impact-profiler")
        original_config = (self.session_dir / "config.toml").read_text(encoding="utf-8")

        with pytest.raises(ValueError, match="cover exactly the generated explorer roles"):
            CodexBackend().setup_session_dir(
                self.session_dir,
                parent_sandbox_mode="read-only",
                agent_defs=definitions,
                explorer_binding_env=binding_envs,
            )

        assert (self.session_dir / "config.toml").read_text(encoding="utf-8") == original_config
        assert not (self.session_dir / "agents").exists()

    @pytest.mark.parametrize("role", sorted(BUNDLED_EXPLORER_ROLES))
    def test_explorer_projection_rejects_missing_web_search_policy_before_mutation(
        self, role: str
    ) -> None:
        canonical_definitions = self._explorer_definitions()
        definitions = tuple(
            replace(definition, codex=replace(definition.codex, web_search=None))
            if definition.name == role
            else definition
            for definition in canonical_definitions
        )
        binding_envs = self._explorer_binding_envs(definitions, generation="first")
        original_config = (self.session_dir / "config.toml").read_text(encoding="utf-8")

        with pytest.raises(ValueError, match="must disable native web search"):
            CodexBackend().setup_session_dir(
                self.session_dir,
                parent_sandbox_mode="read-only",
                agent_defs=definitions,
                explorer_binding_env=binding_envs,
            )

        assert (self.session_dir / "config.toml").read_text(encoding="utf-8") == original_config
        assert not (self.session_dir / "agents").exists()

    def test_explorer_projection_rejects_divergent_role_bindings_before_mutation(self) -> None:
        definitions = self._explorer_definitions()
        binding_envs = self._explorer_binding_envs(definitions, generation="first")
        binding_envs["semantic-code-navigator"]["AUTOSKILLIT_EXPLORATION_CAPABILITY"] = (
            "role-local-capability"
        )
        original_config = (self.session_dir / "config.toml").read_text(encoding="utf-8")

        with pytest.raises(ValueError, match="must be identical.*shared session principal"):
            CodexBackend().setup_session_dir(
                self.session_dir,
                parent_sandbox_mode="read-only",
                agent_defs=definitions,
                explorer_binding_env=binding_envs,
            )

        assert (self.session_dir / "config.toml").read_text(encoding="utf-8") == original_config
        assert not (self.session_dir / "agents").exists()

    def test_refresh_explorer_binding_env_replaces_all_role_bindings(self) -> None:
        definitions = self._explorer_definitions()
        first = self._explorer_binding_envs(definitions, generation="first")
        second = self._explorer_binding_envs(definitions, generation="second")
        self._write_all_source_files()
        backend = CodexBackend()
        backend.setup_session_dir(
            self.session_dir,
            parent_sandbox_mode="read-only",
            agent_defs=definitions,
            explorer_binding_env=first,
        )
        before_config = (self.session_dir / "config.toml").read_text(encoding="utf-8")

        backend.refresh_explorer_binding_env(self.session_dir, second)

        projected_bindings = []
        parent = tomllib.loads((self.session_dir / "config.toml").read_text(encoding="utf-8"))
        projected_bindings.append(parent["mcp_servers"]["autoskillit"]["env"])
        for definition in definitions:
            parsed = tomllib.loads(
                (self.session_dir / "agents" / f"{definition.name}.toml").read_text(
                    encoding="utf-8"
                )
            )
            projected_bindings.append(parsed["mcp_servers"]["autoskillit"]["env"])
        assert projected_bindings == [next(iter(second.values()))] * 3
        assert (self.session_dir / "config.toml").read_text(encoding="utf-8") != before_config

    def test_refresh_replaces_pre_change_policy_and_is_idempotent(self) -> None:
        definitions = self._explorer_definitions()
        first = self._explorer_binding_envs(definitions, generation="first")
        second = self._explorer_binding_envs(definitions, generation="second")
        CodexBackend().setup_session_dir(
            self.session_dir,
            parent_sandbox_mode="read-only",
            agent_defs=definitions,
            explorer_binding_env=first,
        )
        self._materialize_pre_change_explorer_roles(self.session_dir, definitions)

        refresh_explorer_binding_env(self.session_dir, second)

        paths = [
            self.session_dir / "config.toml",
            *(
                self.session_dir / "agents" / f"{definition.name}.toml"
                for definition in definitions
            ),
        ]
        first_refresh = {path.relative_to(self.session_dir): path.read_bytes() for path in paths}
        for definition in definitions:
            role_text = (self.session_dir / "agents" / f"{definition.name}.toml").read_text(
                encoding="utf-8"
            )
            parsed = tomllib.loads(role_text)
            assert parsed["web_search"] == "disabled"
            assert agent_definition_digest(definition) in role_text
            assert not set(RETIRED_CODEX_FEATURES) & set(parsed["features"])
            assert parsed["mcp_servers"]["autoskillit"]["env"] == next(iter(second.values()))

        refresh_explorer_binding_env(self.session_dir, second)

        assert {
            path.relative_to(self.session_dir): path.read_bytes() for path in paths
        } == first_refresh

    def test_refresh_rejects_wrong_role_name_with_valid_mcp_projection(self) -> None:
        definitions = self._explorer_definitions()
        first = self._explorer_binding_envs(definitions, generation="first")
        second = self._explorer_binding_envs(definitions, generation="second")
        CodexBackend().setup_session_dir(
            self.session_dir,
            parent_sandbox_mode="read-only",
            agent_defs=definitions,
            explorer_binding_env=first,
        )
        role_path = self.session_dir / "agents" / "repository-impact-profiler.toml"
        role_path.write_text(
            role_path.read_text(encoding="utf-8").replace(
                'name = "repository-impact-profiler"', 'name = "tampered-role"', 1
            ),
            encoding="utf-8",
        )
        before = {
            path.relative_to(self.session_dir): path.read_bytes()
            for path in (
                self.session_dir / "config.toml",
                *(
                    self.session_dir / "agents" / f"{definition.name}.toml"
                    for definition in definitions
                ),
            )
        }

        with pytest.raises(ValueError, match="identity mismatch"):
            refresh_explorer_binding_env(self.session_dir, second)

        assert {
            relative: (self.session_dir / relative).read_bytes() for relative in before
        } == before

    def test_refresh_explorer_binding_env_prevalidation_preserves_old_bindings(self) -> None:
        definitions = self._explorer_definitions()
        first = self._explorer_binding_envs(definitions, generation="first")
        second = self._explorer_binding_envs(definitions, generation="second")
        self._write_all_source_files()
        CodexBackend().setup_session_dir(
            self.session_dir,
            parent_sandbox_mode="read-only",
            agent_defs=definitions,
            explorer_binding_env=first,
        )
        intact_role = self.session_dir / "agents" / "semantic-code-navigator.toml"
        intact_content = intact_role.read_text(encoding="utf-8")
        invalid_role = self.session_dir / "agents" / "repository-impact-profiler.toml"
        invalid_role.write_text("name = 'tampered'\n", encoding="utf-8")

        with pytest.raises(ValueError, match="missing MCP projection"):
            refresh_explorer_binding_env(self.session_dir, second)

        assert intact_role.read_text(encoding="utf-8") == intact_content

    def test_refresh_explorer_binding_env_rolls_back_all_three_configs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        definitions = self._explorer_definitions()
        first = self._explorer_binding_envs(definitions, generation="first")
        second = self._explorer_binding_envs(definitions, generation="second")
        self._write_all_source_files()
        CodexBackend().setup_session_dir(
            self.session_dir,
            parent_sandbox_mode="read-only",
            agent_defs=definitions,
            explorer_binding_env=first,
        )
        paths = [
            self.session_dir / "config.toml",
            *(
                self.session_dir / "agents" / f"{definition.name}.toml"
                for definition in definitions
            ),
        ]
        before = {path.relative_to(self.session_dir): path.read_text() for path in paths}
        real_replace = os.replace
        rejected_install = False

        def fail_staged_session_install(src: Path | str, dst: Path | str) -> None:
            nonlocal rejected_install
            source = Path(src)
            destination = Path(dst)
            if (
                not rejected_install
                and source.name == "session"
                and destination == self.session_dir
            ):
                rejected_install = True
                raise OSError("simulated projection swap failure")
            real_replace(src, dst)

        monkeypatch.setattr(os, "replace", fail_staged_session_install)

        with pytest.raises(OSError, match="simulated projection swap failure"):
            refresh_explorer_binding_env(self.session_dir, second)

        assert rejected_install is True
        assert {
            relative: (self.session_dir / relative).read_text() for relative in before
        } == before

    def test_refresh_explorer_binding_env_chains_rollback_failure_from_install_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        definitions = self._explorer_definitions()
        first = self._explorer_binding_envs(definitions, generation="first")
        second = self._explorer_binding_envs(definitions, generation="second")
        self._write_all_source_files()
        CodexBackend().setup_session_dir(
            self.session_dir,
            parent_sandbox_mode="read-only",
            agent_defs=definitions,
            explorer_binding_env=first,
        )
        real_replace = os.replace

        def fail_install_and_restore(src: Path | str, dst: Path | str) -> None:
            source = Path(src)
            destination = Path(dst)
            if source.name == "session" and destination == self.session_dir:
                raise OSError("simulated staged install failure")
            if source.name == "previous-session" and destination == self.session_dir:
                raise OSError("simulated rollback failure")
            real_replace(src, dst)

        monkeypatch.setattr(os, "replace", fail_install_and_restore)

        with pytest.raises(OSError, match="simulated rollback failure") as exc_info:
            refresh_explorer_binding_env(self.session_dir, second)

        assert isinstance(exc_info.value.__cause__, OSError)
        assert "simulated staged install failure" in str(exc_info.value.__cause__)
        recovery_roots = tuple(self.session_dir.parent.glob(".autoskillit-explorer-refresh-*"))
        assert len(recovery_roots) == 1
        assert (recovery_roots[0] / "previous-session").is_dir()

    @pytest.mark.parametrize("operation", ["refresh", "scrub"])
    def test_persisted_ambient_mcp_server_fails_closed_before_projection_rewrite(
        self,
        operation: str,
    ) -> None:
        definitions = self._explorer_definitions()
        first = self._explorer_binding_envs(definitions, generation="first")
        second = self._explorer_binding_envs(definitions, generation="second")
        CodexBackend().setup_session_dir(
            self.session_dir,
            parent_sandbox_mode="read-only",
            agent_defs=definitions,
            explorer_binding_env=first,
        )
        config_path = self.session_dir / "config.toml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8")
            + '\n[mcp_servers."ambient"]\ncommand = "ambient-mcp"\n',
            encoding="utf-8",
        )
        paths = [
            config_path,
            *(
                self.session_dir / "agents" / f"{definition.name}.toml"
                for definition in definitions
            ),
        ]
        before = {path.relative_to(self.session_dir): path.read_text() for path in paths}

        with pytest.raises(ValueError, match="must configure exactly one MCP server"):
            if operation == "refresh":
                refresh_explorer_binding_env(self.session_dir, second)
            else:
                clear_explorer_binding_env(self.session_dir, frozenset(first))

        assert {
            relative: (self.session_dir / relative).read_text() for relative in before
        } == before

    def test_explorer_projection_rejects_relative_authority_path_before_mutation(self) -> None:
        definitions = self._explorer_definitions()
        binding_envs = self._explorer_binding_envs(definitions, generation="first")
        for binding in binding_envs.values():
            binding["AUTOSKILLIT_EXPLORATION_AUTHORITY_PATH"] = "relative-authority.json"
        original_config = (self.session_dir / "config.toml").read_text(encoding="utf-8")

        with pytest.raises(ValueError, match="authority path.*absolute"):
            CodexBackend().setup_session_dir(
                self.session_dir,
                parent_sandbox_mode="read-only",
                agent_defs=definitions,
                explorer_binding_env=binding_envs,
            )

        assert (self.session_dir / "config.toml").read_text(encoding="utf-8") == original_config
        assert not (self.session_dir / "agents").exists()

    def test_clear_explorer_binding_env_scrubs_all_persisted_secrets(self) -> None:
        definitions = self._explorer_definitions()
        binding_envs = self._explorer_binding_envs(definitions, generation="first")
        self._write_all_source_files()
        backend = CodexBackend()
        backend.setup_session_dir(
            self.session_dir,
            parent_sandbox_mode="read-only",
            agent_defs=definitions,
            explorer_binding_env=binding_envs,
        )
        before_config = (self.session_dir / "config.toml").read_text(encoding="utf-8")

        backend.clear_explorer_binding_env(
            self.session_dir,
            frozenset(binding_envs),
        )

        parent_text = (self.session_dir / "config.toml").read_text(encoding="utf-8")
        parent = tomllib.loads(parent_text)
        parent_projection = parent["mcp_servers"]["autoskillit"]
        assert parent_projection["enabled_tools"] == [
            "submit_exploration_query",
            "get_exploration_page",
            "resume_exploration_context",
        ]
        assert "env" not in parent_projection
        for definition in definitions:
            role_text = (self.session_dir / "agents" / f"{definition.name}.toml").read_text(
                encoding="utf-8"
            )
            parsed = tomllib.loads(role_text)
            projection = parsed["mcp_servers"]["autoskillit"]
            assert projection["enabled_tools"] == [
                tool.removeprefix(DIRECT_PREFIX) for tool in definition.tools
            ]
            assert "env" not in projection
            for key in binding_envs[definition.name]:
                assert key not in role_text
            for value in binding_envs[definition.name].values():
                assert value not in role_text
        assert parent_text != before_config
        for key, value in next(iter(binding_envs.values())).items():
            assert key not in parent_text
            assert value not in parent_text

    def test_clear_replaces_pre_change_policy_while_scrubbing_bindings(self) -> None:
        definitions = self._explorer_definitions()
        binding_envs = self._explorer_binding_envs(definitions, generation="first")
        CodexBackend().setup_session_dir(
            self.session_dir,
            parent_sandbox_mode="read-only",
            agent_defs=definitions,
            explorer_binding_env=binding_envs,
        )
        self._materialize_pre_change_explorer_roles(self.session_dir, definitions)

        clear_explorer_binding_env(self.session_dir, frozenset(binding_envs))

        for definition in definitions:
            role_text = (self.session_dir / "agents" / f"{definition.name}.toml").read_text(
                encoding="utf-8"
            )
            parsed = tomllib.loads(role_text)
            assert parsed["web_search"] == "disabled"
            assert agent_definition_digest(definition) in role_text
            assert "env" not in parsed["mcp_servers"]["autoskillit"]
            assert not set(RETIRED_CODEX_FEATURES) & set(parsed["features"])

    def test_clear_explorer_binding_env_is_idempotent_after_scrubbing(self) -> None:
        definitions = self._explorer_definitions()
        binding_envs = self._explorer_binding_envs(definitions, generation="first")
        self._write_all_source_files()
        CodexBackend().setup_session_dir(
            self.session_dir,
            parent_sandbox_mode="read-only",
            agent_defs=definitions,
            explorer_binding_env=binding_envs,
        )
        roles = frozenset(binding_envs)

        clear_explorer_binding_env(self.session_dir, roles)
        first_scrub = {"parent": (self.session_dir / "config.toml").read_text(encoding="utf-8")}
        first_scrub.update(
            {
                role: (self.session_dir / "agents" / f"{role}.toml").read_text(encoding="utf-8")
                for role in roles
            }
        )
        clear_explorer_binding_env(self.session_dir, roles)

        second_scrub = {"parent": (self.session_dir / "config.toml").read_text(encoding="utf-8")}
        second_scrub.update(
            {
                role: (self.session_dir / "agents" / f"{role}.toml").read_text(encoding="utf-8")
                for role in roles
            }
        )
        assert second_scrub == first_scrub

    def test_clear_explorer_binding_env_validates_role_set_before_filesystem_access(self) -> None:
        missing_session_dir = self.session_dir / "not-created"

        with pytest.raises(ValueError, match="must be a frozenset"):
            clear_explorer_binding_env(
                missing_session_dir,
                cast(frozenset[str], {"semantic-code-navigator"}),
            )
        clear_explorer_binding_env(missing_session_dir, frozenset())

        assert not missing_session_dir.exists()

    def test_injected_luna_disabled_features_generate_toml_feature_table(self) -> None:
        definition = self._luna_definition(disabled_features=("apps", "shell_tool"))
        CodexBackend().setup_session_dir(
            self.session_dir,
            parent_sandbox_mode="read-only",
            agent_defs=(definition,),
        )
        generated = self.session_dir / "agents" / "semantic-code-navigator.toml"
        generated_text = generated.read_text(encoding="utf-8")
        parsed = tomllib.loads(generated_text)
        assert parsed["features"] == {"apps": False, "shell_tool": False}
        assert generated_text.index("developer_instructions") < generated_text.index("[features]")

    def test_terminal_luna_definition_disables_nested_agents(self) -> None:
        definition = self._luna_definition(agents_enabled=False)
        CodexBackend().setup_session_dir(
            self.session_dir,
            parent_sandbox_mode="read-only",
            agent_defs=(definition,),
        )
        generated = self.session_dir / "agents" / "semantic-code-navigator.toml"
        generated_text = generated.read_text(encoding="utf-8")
        parsed = tomllib.loads(generated_text)
        assert parsed["agents"] == {"enabled": False}
        assert generated_text.index("developer_instructions") < generated_text.index("[agents]")

    def test_web_evidence_role_renders_live_web_leaf_policy(self) -> None:
        definition = next(
            definition
            for definition in load_agent_definitions(pkg_root() / "agents")
            if definition.name == WEB_EVIDENCE_RESEARCHER_ROLE
        )
        CodexBackend().setup_session_dir(
            self.session_dir,
            parent_sandbox_mode="read-only",
            agent_defs=(definition,),
        )

        generated = self.session_dir / "agents" / f"{WEB_EVIDENCE_RESEARCHER_ROLE}.toml"
        generated_text = generated.read_text(encoding="utf-8")
        parsed = tomllib.loads(generated_text)
        assert parsed["model"] == "gpt-5.6-luna"
        assert parsed["model_reasoning_effort"] == "xhigh"
        assert parsed["sandbox_mode"] == "read-only"
        assert parsed["web_search"] == "live"
        assert parsed["agents"] == {"enabled": False}
        assert parsed["features"] == {
            feature: False for feature in definition.codex.disabled_features
        }
        assert agent_definition_digest(definition) in generated_text
        assert generated_text.index('web_search = "live"') < generated_text.index("[features]")
        assert generated_text.index('web_search = "live"') < generated_text.index("[agents]")

    def test_injected_luna_definition_rejects_writable_parent_before_mutation(self) -> None:
        self._write_all_source_files()
        config_path = self.session_dir / "config.toml"
        original_config = 'model = "user-pinned"\n[mcp_servers.autoskillit]\n'
        config_path.write_text(original_config, encoding="utf-8")

        with pytest.raises(
            ValueError,
            match="gpt-5.6-luna/max/read-only agent projection requires",
        ):
            CodexBackend().setup_session_dir(
                self.session_dir,
                parent_sandbox_mode="workspace-write",
                agent_defs=(self._luna_definition(),),
            )

        assert config_path.read_text(encoding="utf-8") == original_config
        assert {path.name for path in self.session_dir.iterdir()} == {"config.toml"}

    def test_ordinary_bundled_ambient_agent_takes_precedence(self) -> None:
        (self.session_dir / "config.toml").write_text(
            '[agents."wp-elaborator"]\n'
            'description = "profile role"\n'
            'config_file = "/profile/wp-elaborator.toml"\n'
        )
        CodexBackend().setup_session_dir(self.session_dir)

        config = tomllib.loads((self.session_dir / "config.toml").read_text(encoding="utf-8"))
        assert config["agents"]["wp-elaborator"] == {
            "description": "profile role",
            "config_file": "/profile/wp-elaborator.toml",
        }
        assert not (self.session_dir / "agents" / "wp-elaborator.toml").exists()
        assert not (self.session_dir / "agents" / "semantic-code-navigator.toml").exists()

    def test_unrelated_ambient_agent_is_preserved_with_bundled_projection(self) -> None:
        (self.session_dir / "config.toml").write_text(
            '[agents."profile-specialist"]\n'
            'description = "unrelated profile role"\n'
            'config_file = "/profile/profile-specialist.toml"\n'
        )

        CodexBackend().setup_session_dir(self.session_dir)

        config = tomllib.loads((self.session_dir / "config.toml").read_text(encoding="utf-8"))
        assert config["agents"]["profile-specialist"] == {
            "description": "unrelated profile role",
            "config_file": "/profile/profile-specialist.toml",
        }
        assert config["agents"]["wp-elaborator"]["config_file"] == ("agents/wp-elaborator.toml")
        assert not (self.session_dir / "agents" / "profile-specialist.toml").exists()

    def test_setup_returns_only_finalized_spawnable_agent_names(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        valid_target = self.fake_home / "valid-ambient.toml"
        valid_target.write_text('name = "valid-ambient"\n', encoding="utf-8")
        source_relative_target = self.codex_home / "agents" / "source-only.toml"
        source_relative_target.parent.mkdir()
        source_relative_target.write_text('name = "source-only"\n', encoding="utf-8")
        (self.codex_home / "config.toml").write_text(
            self._CANONICAL_AUTOSKILLIT_MCP_CONFIG
            + '\n[agents."valid-ambient"]\n'
            + 'description = "absolute ambient role"\n'
            + f'config_file = "{valid_target}"\n'
            + '\n[agents."source-relative"]\n'
            + 'description = "source-relative ambient role"\n'
            + 'config_file = "agents/source-only.toml"\n',
            encoding="utf-8",
        )
        (self.codex_home / "auth.json").write_text("{}", encoding="utf-8")
        monkeypatch.setenv(MCP_CLIENT_BACKEND_ENV_VAR, "pre-test-backend")
        backend = CodexBackend()

        assert not backend.ensure_pre_launch(session_dir=self.session_dir).errors
        role_names = backend.setup_session_dir(self.session_dir)

        eligible_bundled = {
            definition.name
            for definition in load_agent_definitions(pkg_root() / "agents")
            if definition.name not in BUNDLED_EXPLORER_ROLES and not definition.reader_tools
        }
        assert role_names == frozenset(
            {"default", "explorer", "worker", "valid-ambient"} | eligible_bundled
        )
        finalized = tomllib.loads((self.session_dir / "config.toml").read_text(encoding="utf-8"))
        assert set(finalized["agents"]) == (
            {"valid-ambient", "source-relative"} | eligible_bundled
        )

    def test_setup_warns_when_ambient_agent_config_is_unreadable(self) -> None:
        invalid_target = self.codex_home / "invalid-ambient.toml"
        invalid_target.write_text("[", encoding="utf-8")
        (self.codex_home / "config.toml").write_text(
            self._CANONICAL_AUTOSKILLIT_MCP_CONFIG
            + '\n[agents."invalid-ambient"]\n'
            + 'description = "invalid ambient role"\n'
            + f'config_file = "{invalid_target}"\n',
            encoding="utf-8",
        )
        (self.codex_home / "auth.json").write_text("{}", encoding="utf-8")
        backend = CodexBackend()

        assert not backend.ensure_pre_launch(session_dir=self.session_dir).errors
        with structlog.testing.capture_logs() as cap_logs:
            role_names = backend.setup_session_dir(self.session_dir)

        assert "invalid-ambient" not in role_names
        warning = next(
            entry for entry in cap_logs if entry.get("event") == "codex_agent_config_unreadable"
        )
        assert warning == {
            "agent_name": "invalid-ambient",
            "path": str(invalid_target),
            "error_type": "TOMLDecodeError",
            "event": "codex_agent_config_unreadable",
            "logger": "autoskillit.execution.backends._codex_config",
            "log_level": "warning",
        }

    def test_effective_agent_names_warns_for_ignored_registrations(self) -> None:
        missing_target = self.session_dir / "agents" / "missing.toml"
        (self.session_dir / "config.toml").write_text(
            "[agents]\n"
            'invalid = "not a table"\n'
            "[agents.missing_config]\n"
            'description = "missing config file"\n'
            "[agents.missing_target]\n"
            'config_file = "agents/missing.toml"\n',
            encoding="utf-8",
        )

        with structlog.testing.capture_logs() as cap_logs:
            role_names = effective_codex_agent_names(self.session_dir)

        assert "invalid" not in role_names
        assert "missing_config" not in role_names
        assert "missing_target" not in role_names
        assert [
            {key: entry[key] for key in ("agent_name", "path", "reason") if key in entry}
            for entry in cap_logs
            if entry.get("event") == "codex_agent_registration_ignored"
        ] == [
            {"agent_name": "invalid", "reason": "invalid_registration"},
            {"agent_name": "missing_config", "reason": "invalid_config_file"},
            {
                "agent_name": "missing_target",
                "path": str(missing_target),
                "reason": "missing_config_file",
            },
        ]

    @pytest.mark.parametrize(
        "role",
        (
            "semantic-code-navigator",
            "repository-impact-profiler",
            WEB_EVIDENCE_RESEARCHER_ROLE,
        ),
    )
    def test_protected_bundled_agent_collision_fails_before_mutation(self, role: str) -> None:
        (self.session_dir / "config.toml").write_text(
            f'[agents."{role}"]\n'
            'description = "ambient explorer"\n'
            f'config_file = "/profile/{role}.toml"\n'
        )
        original_config = (self.session_dir / "config.toml").read_text(encoding="utf-8")

        with pytest.raises(ValueError, match="ambient Codex agent name collision"):
            CodexBackend().setup_session_dir(self.session_dir)

        assert (self.session_dir / "config.toml").read_text(encoding="utf-8") == original_config
        assert {path.name for path in self.session_dir.iterdir()} == {"config.toml"}

    def test_explicit_agent_ambient_collision_fails_before_mutation(self) -> None:
        definition = next(
            definition
            for definition in load_agent_definitions(pkg_root() / "agents")
            if definition.name == "wp-elaborator"
        )
        (self.session_dir / "config.toml").write_text(
            '[agents."wp-elaborator"]\n'
            'description = "profile role"\n'
            'config_file = "/profile/wp-elaborator.toml"\n'
        )
        original_config = (self.session_dir / "config.toml").read_text(encoding="utf-8")

        with pytest.raises(ValueError, match="ambient Codex agent name collision"):
            CodexBackend().setup_session_dir(
                self.session_dir,
                agent_defs=(definition,),
            )

        assert (self.session_dir / "config.toml").read_text(encoding="utf-8") == original_config
        assert {path.name for path in self.session_dir.iterdir()} == {"config.toml"}

    def test_agent_artifact_collision_fails_before_mutation(self) -> None:
        agents_dir = self.session_dir / "agents"
        agents_dir.mkdir()
        artifact = agents_dir / "wp-elaborator.toml"
        artifact.write_text('name = "ambient"\n', encoding="utf-8")
        original_config = (self.session_dir / "config.toml").read_text(encoding="utf-8")

        with pytest.raises(ValueError, match="ambient Codex agent artifact collision"):
            CodexBackend().setup_session_dir(self.session_dir)

        assert (self.session_dir / "config.toml").read_text(encoding="utf-8") == original_config
        assert artifact.read_text(encoding="utf-8") == 'name = "ambient"\n'
        assert {path.name for path in self.session_dir.iterdir()} == {
            "agents",
            "config.toml",
        }

    def test_duplicate_injected_roles_fail_before_mutation(self) -> None:
        original_config = (self.session_dir / "config.toml").read_text(encoding="utf-8")
        definition = self._luna_definition()

        with pytest.raises(ValueError, match="duplicate Codex agent definitions"):
            CodexBackend().setup_session_dir(
                self.session_dir,
                parent_sandbox_mode="read-only",
                agent_defs=(definition, definition),
            )

        assert (self.session_dir / "config.toml").read_text(encoding="utf-8") == original_config
        assert {path.name for path in self.session_dir.iterdir()} == {"config.toml"}

    @pytest.mark.parametrize("role", ("worker", "review"))
    def test_host_reserved_agent_name_fails_before_mutation(self, role: str) -> None:
        original_config = (self.session_dir / "config.toml").read_text(encoding="utf-8")
        definition = AgentDef(
            name=role,
            description="Reserved-role collision",
            tools=("Read",),
            model="sonnet",
            max_turns=1,
            body="Return bounded evidence only.",
            codex=CodexAgentProjectionDef(None, None, "read-only"),
        )

        with pytest.raises(ValueError, match="Codex built-in agent name collision"):
            CodexBackend().setup_session_dir(
                self.session_dir,
                parent_sandbox_mode="read-only",
                agent_defs=(definition,),
            )

        assert (self.session_dir / "config.toml").read_text(encoding="utf-8") == original_config
        assert {path.name for path in self.session_dir.iterdir()} == {"config.toml"}

    def test_agent_toml_model_alias_mapped(self) -> None:
        self._write_all_source_files()
        CodexBackend().setup_session_dir(self.session_dir)
        wp_toml = self.session_dir / "agents" / "wp-elaborator.toml"
        data = tomllib.loads(wp_toml.read_text(encoding="utf-8"))
        assert data["model"] == CODEX_MODEL_ALIASES["sonnet"]

    def test_agent_toml_contains_effort(self) -> None:
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
        self._write_all_source_files()
        CodexBackend().setup_session_dir(self.session_dir)
        wp_toml = self.session_dir / "agents" / "wp-elaborator.toml"
        data = tomllib.loads(wp_toml.read_text(encoding="utf-8"))
        body = data["developer_instructions"]
        assert "# wp-elaborator" in body
        assert "## Tool Constraints" in body
        assert "```json" in body

    def test_no_git_subdir_created(self) -> None:
        self._write_all_source_files()
        CodexBackend().setup_session_dir(self.session_dir)
        assert not (self.session_dir / ".git").exists()

    def test_snapshotted_config_has_auto_compact_limit(self) -> None:
        from autoskillit.execution.backends import CODEX_AUTO_COMPACT_LIMIT

        (self.session_dir / "config.toml").write_text(
            f"model_auto_compact_token_limit = {CODEX_AUTO_COMPACT_LIMIT}\n"
            + self._CANONICAL_AUTOSKILLIT_MCP_CONFIG
        )
        (self.codex_home / "auth.json").write_text("{}")
        CodexBackend().setup_session_dir(self.session_dir)
        data = tomllib.loads((self.session_dir / "config.toml").read_text(encoding="utf-8"))
        assert data["model_auto_compact_token_limit"] == CODEX_AUTO_COMPACT_LIMIT

    def test_session_config_lacks_key_when_source_lacks_it(self) -> None:
        (self.codex_home / "config.toml").write_text("[mcp_servers.autoskillit]\n")
        (self.codex_home / "auth.json").write_text("{}")
        CodexBackend().setup_session_dir(self.session_dir)
        data = tomllib.loads((self.session_dir / "config.toml").read_text(encoding="utf-8"))
        assert "model_auto_compact_token_limit" not in data
