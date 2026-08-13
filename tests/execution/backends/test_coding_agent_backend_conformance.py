from __future__ import annotations

import dataclasses
import inspect
import json
from pathlib import Path
from typing import Literal

import pytest

from autoskillit.core import (
    CODEX_SESSIONS_SUBDIR,
    BackendCapabilities,
    BackendConventions,
    CapabilityNotSupportedError,
    CmdSpec,
    CodingAgentBackend,
    EnvPolicy,
    ResultParser,
    SessionLocator,
    SkillSessionConfig,
    StreamParser,
)
from autoskillit.execution.backends import BACKEND_REGISTRY, get_backend
from tests.execution.backends._plugin_binding import plugin_binding

from .test_backend_contract_base import BackendContractBase

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

NOT_YET_LIVE: frozenset[str] = frozenset(
    {
        "min_version",
        "patch_format",
        "required_session_files",
        "session_dir_symlinks",
        "supports_model_invocation_gating",
        "supports_thinking_blocks",
        "github_api_callable",
    }
)

CAPABILITY_CLASSIFICATION: dict[str, Literal["REQUIRED", "OPTIONAL"]] = {
    "applicable_guards": "REQUIRED",
    "anthropic_provider_capable": "OPTIONAL",
    "channel_b_capable": "OPTIONAL",
    "supports_task_lifecycle_events": "REQUIRED",
    "claude_marketplace_tool_prefix_capable": "REQUIRED",
    "completion_record_types": "REQUIRED",
    "cook_exact_binding_probe_required": "OPTIONAL",
    "default_skill_sandbox_mode": "REQUIRED",
    "unnegotiated_tool_result_token_limit": "REQUIRED",
    "env_denylist_prefixes": "REQUIRED",
    "exit_code_is_terminal": "REQUIRED",
    "explicit_path_env_var": "OPTIONAL",
    "food_truck_capable": "OPTIONAL",
    "github_api_callable": "OPTIONAL",
    "has_unguarded_filesystem_access": "REQUIRED",
    "terminal_explorer_capable": "REQUIRED",
    "session_scoped_explorer_capable": "REQUIRED",
    "hook_config_format": "REQUIRED",
    "hook_trust_policy": "REQUIRED",
    "inspector_capable": "OPTIONAL",
    "mcp_config_capable": "OPTIONAL",
    "mcp_env_forward_vars": "OPTIONAL",
    "min_version": "OPTIONAL",
    "patch_format": "OPTIONAL",
    "plugin_install_capable": "OPTIONAL",
    "protected_recipe_delivery_capable": "OPTIONAL",
    "recipe_delivery_budget": "OPTIONAL",
    "process_name": "REQUIRED",
    "process_name_aliases": "REQUIRED",
    "pty_required": "REQUIRED",
    "record_capable": "OPTIONAL",
    "replay_capable": "OPTIONAL",
    "required_session_files": "OPTIONAL",
    "required_skill_fields": "REQUIRED",
    "cook_startup_observer_capable": "OPTIONAL",
    "session_dir_persistent": "OPTIONAL",
    "session_dir_symlinks": "OPTIONAL",
    "session_record_types": "REQUIRED",
    "session_resume_capable": "OPTIONAL",
    "skill_injection_capable": "REQUIRED",
    "skill_sigil": "REQUIRED",
    "skills_subdir": "REQUIRED",
    "supports_claude_format_stdout": "REQUIRED",
    "supports_context_exhaustion_detection": "OPTIONAL",
    "supports_context_window_suffix": "OPTIONAL",
    "supports_model_capacity_error_detection": "OPTIONAL",
    "supports_model_invocation_gating": "OPTIONAL",
    "supports_thinking_blocks": "OPTIONAL",
    "supports_tool_list_changed": "OPTIONAL",
    "triage_capable": "OPTIONAL",
    "version_check_command": "REQUIRED",
    "write_detection_strategy": "REQUIRED",
    "write_guard_tool_names": "REQUIRED",
}


def make_backend(backend_name: str) -> CodingAgentBackend:
    return get_backend(backend_name)


@pytest.mark.parametrize("backend_name", list(BACKEND_REGISTRY))
class TestCodingAgentBackendConformance(BackendContractBase):
    @pytest.fixture(autouse=True)
    def _setup_backend(self, backend_name: str) -> None:
        self.backend = make_backend(backend_name)

    def make_backend(self) -> CodingAgentBackend:
        return self.backend

    # --- Group 1: Core Properties ---

    def test_isinstance_coding_agent_backend(self) -> None:
        assert isinstance(self.backend, CodingAgentBackend)

    def test_name_is_non_empty_string(self) -> None:
        """BackendCapabilities.process_name — backend name from identity field."""
        assert isinstance(self.backend.name, str)
        assert len(self.backend.name) > 0

    def test_marketplace_tool_prefix_capability_is_bool(self) -> None:
        """BackendCapabilities.claude_marketplace_tool_prefix_capable is boolean."""
        assert isinstance(
            self.backend.capabilities.claude_marketplace_tool_prefix_capable,
            bool,
        )

    def test_cook_exact_binding_probe_capability_is_bool(self) -> None:
        """BackendCapabilities.cook_exact_binding_probe_required — fresh-cook gate."""
        assert isinstance(
            self.backend.capabilities.cook_exact_binding_probe_required,
            bool,
        )

    def test_explicit_path_env_var_capability_is_str(self) -> None:
        """BackendCapabilities.explicit_path_env_var — cold-launch selector coverage."""
        assert isinstance(self.backend.capabilities.explicit_path_env_var, str)

    def test_capabilities_returns_backend_capabilities(self) -> None:
        """BackendCapabilities contract — exercises multiple fields.

        Fields cited: applicable_guards, default_skill_sandbox_mode,
        unnegotiated_tool_result_token_limit,
        has_unguarded_filesystem_access, process_name_aliases,
        record_capable, replay_capable,
        cook_startup_observer_capable, session_dir_persistent,
        supports_context_window_suffix,
        supports_tool_list_changed, triage_capable, write_detection_strategy.
        """
        assert isinstance(self.backend.capabilities, BackendCapabilities)

    def test_hook_trust_policy_is_typed(self) -> None:
        """BackendCapabilities.hook_trust_policy — interactive hook review policy."""
        from autoskillit.core import HookTrustPolicy

        assert isinstance(self.backend.capabilities.hook_trust_policy, HookTrustPolicy)

    def test_conventions_returns_backend_conventions(self) -> None:
        assert isinstance(self.backend.conventions, BackendConventions)

    def test_write_tool_names_returns_nonempty_frozenset(self) -> None:
        """BackendCapabilities.write_guard_tool_names — write_tool_names returns frozenset."""
        result = self.backend.write_tool_names()
        assert isinstance(result, frozenset)
        assert len(result) >= 1

    def test_binary_name_returns_non_empty_string(self) -> None:
        """BackendCapabilities.version_check_command — binary_name is the executable."""
        result = self.backend.binary_name()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_translate_model_returns_string(self) -> None:
        """BackendCapabilities.anthropic_provider_capable — maps provider models."""
        result = self.backend.translate_model("sonnet")
        assert isinstance(result, str)

    def test_model_config_overrides_returns_tuple(self) -> None:
        result = self.backend.model_config_overrides("sonnet")
        assert isinstance(result, tuple)

    # --- Group 2: Sub-protocol Factories ---

    def test_stream_parser_no_marker_returns_stream_parser(self) -> None:
        """BackendCapabilities.channel_b_capable and supports_claude_format_stdout — parser dep."""
        result = self.backend.stream_parser()
        assert isinstance(result, StreamParser)

    def test_stream_parser_with_marker_stores_completion_marker(self) -> None:
        """BackendCapabilities.completion_record_types — completion marker drives record filter."""
        parser = self.backend.stream_parser(completion_marker="%%DONE%%")
        assert parser.completion_marker == "%%DONE%%"  # type: ignore[attr-defined]

    def test_result_parser_returns_result_parser(self) -> None:
        """BackendCapabilities.session_record_types — parser extracts content from entries."""
        result = self.backend.result_parser()
        assert isinstance(result, ResultParser)

    def test_env_policy_returns_env_policy(self) -> None:
        """BackendCapabilities.env_denylist_prefixes — env policy built from prefixes."""
        result = self.backend.env_policy()
        assert isinstance(result, EnvPolicy)

    def test_session_locator_returns_session_locator(self) -> None:
        result = self.backend.session_locator()
        assert isinstance(result, SessionLocator)

    def test_session_locator_locate_empty_string_returns_none(self) -> None:
        locator = self.backend.session_locator()
        assert locator.locate_session("") is None

    def test_session_locator_has_typed_listing(self) -> None:
        locator = self.backend.session_locator()
        assert callable(locator.list_sessions)

    # --- Group 3: Command-builder Contracts ---

    def test_build_cmd_returns_cmd_spec(self) -> None:
        """BackendCapabilities.exit_code_is_terminal and pty_required — cmd construction deps."""
        result = self.backend.build_cmd(skill_command="do stuff", cwd="/tmp")
        assert isinstance(result, CmdSpec)
        assert isinstance(result.cmd, tuple)

    def test_build_skill_session_cmd_with_default_config_returns_cmd_spec(self) -> None:
        """BackendCapabilities.skill_injection_capable, required_skill_fields, skill_sigil."""
        result = self.backend.build_skill_session_cmd(
            skill_command="/test-skill", cwd="/work", config=SkillSessionConfig()
        )
        assert isinstance(result, CmdSpec)
        assert isinstance(result.cmd, tuple)

    def test_build_interactive_cmd_returns_cmd_spec(self) -> None:
        """BackendCapabilities.mcp_config_capable — interactive cmd uses MCP config when true."""
        result = self.backend.build_interactive_cmd()
        assert isinstance(result, CmdSpec)
        assert isinstance(result.cmd, tuple)

    def test_validate_session_layout_returns_list(self, tmp_path: Path) -> None:
        result = self.backend.validate_session_layout(tmp_path, project_dir=tmp_path)
        assert isinstance(result, list)

    def test_validate_interactive_invocation_returns_list(self) -> None:
        spec = self.backend.build_interactive_cmd()
        assert isinstance(self.backend.validate_interactive_invocation(spec), list)

    def test_cook_lifecycle_boundaries_are_implemented(self) -> None:
        assert callable(self.backend.recover_cook_history)
        assert callable(self.backend.cook_session_context)

    def test_validate_skill_content_returns_list(self) -> None:
        """BackendCapabilities.hook_config_format and skills_subdir — skill content validation."""
        result = self.backend.validate_skill_content("")
        assert isinstance(result, list)

    def test_list_plugins_returns_list(self) -> None:
        """BackendCapabilities.plugin_install_capable — list_plugins returns installed plugins."""
        result = self.backend.list_plugins()
        assert isinstance(result, list)

    # --- Group 4: Setup + SessionLocator P3 ---

    def test_setup_session_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        if self.backend.name == "claude-code":
            self.backend.setup_session_dir(tmp_path)
        elif self.backend.name == "codex":
            fake_home = tmp_path / "fake_home"
            fake_home.mkdir()
            monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
            fake_log_dir = tmp_path / "fake_logs"
            fake_log_dir.mkdir()
            monkeypatch.setattr(
                "autoskillit.execution.backends.codex.default_log_dir",
                lambda: fake_log_dir,
            )
            session_dir = tmp_path / "session"
            session_dir.mkdir()
            with pytest.raises(FileNotFoundError):
                self.backend.setup_session_dir(session_dir)
        else:
            # Intentional sentinel: when a new backend is added to BACKEND_REGISTRY,
            # add an elif branch above with explicit setup_session_dir coverage rather
            # than removing this guard.
            pytest.fail(
                f"test_setup_session_dir has no coverage for backend {self.backend.name!r}"
            )

    def test_project_log_dir_returns_absolute_path(self) -> None:
        locator = self.backend.session_locator()
        result = locator.project_log_dir("/tmp/fake-cwd")
        assert isinstance(result, Path)
        assert result.is_absolute()

    def test_session_log_path_returns_none_for_empty_id(self) -> None:
        locator = self.backend.session_locator()
        result = locator.session_log_path("/tmp/fake-cwd", "")
        assert result is None

    def test_session_log_path_returns_none_for_no_session_prefix(self) -> None:
        locator = self.backend.session_locator()
        result = locator.session_log_path("/tmp/fake-cwd", "no_session_abc")
        assert result is None

    # --- Group 5: Capability-Gated Command Builders ---

    def test_build_resume_cmd_when_capable(self) -> None:
        """BackendCapabilities.session_resume_capable — build_resume_cmd returns valid CmdSpec."""
        self._require_capability("session_resume_capable")
        result = self.backend.build_resume_cmd(
            resume_session_id="test-session-id", prompt="test prompt"
        )
        assert isinstance(result, CmdSpec)
        assert len(result.cmd) > 0

    def test_resume_cmd_carries_plugin_binding_descriptors(self) -> None:
        self._require_capability("session_resume_capable")
        result = self.backend.build_resume_cmd(
            resume_session_id="test-session-id",
            prompt="test prompt",
            plugin_binding=plugin_binding("/tmp/plugin", inherited_fds=(9, 3)),
        )
        assert result.inherited_fds == (9, 3)

    def test_build_food_truck_cmd_when_capable(self) -> None:
        """BackendCapabilities.food_truck_capable — build_food_truck_cmd returns valid CmdSpec."""
        self._require_capability("food_truck_capable")
        result = self.backend.build_food_truck_cmd(
            orchestrator_prompt="x",
            plugin_binding=plugin_binding(Path("/tmp")),
            cwd="/tmp",
            completion_marker="%%X%%",
        )
        assert isinstance(result, CmdSpec)

    def test_food_truck_cmd_carries_plugin_binding_descriptors(self) -> None:
        self._require_capability("food_truck_capable")
        result = self.backend.build_food_truck_cmd(
            orchestrator_prompt="x",
            plugin_binding=plugin_binding("/tmp/plugin", inherited_fds=(9, 3)),
            cwd="/tmp",
            completion_marker="%%X%%",
        )
        assert result.inherited_fds == (9, 3)

    def test_skill_cmd_carries_plugin_binding_descriptors(self) -> None:
        result = self.backend.build_skill_session_cmd(
            "/test",
            "/tmp",
            config=SkillSessionConfig(
                plugin_binding=plugin_binding("/tmp/plugin", inherited_fds=(9, 3))
            ),
        )
        assert result.inherited_fds == (9, 3)

    def test_interactive_cmd_carries_plugin_binding_descriptors(self) -> None:
        result = self.backend.build_interactive_cmd(
            plugin_binding=plugin_binding("/tmp/plugin", inherited_fds=(9, 3))
        )
        assert result.inherited_fds == (9, 3)

    def test_build_inspector_cmd_raises_when_not_capable(self) -> None:
        """BackendCapabilities.inspector_capable — raises when not capable."""
        if self.backend.capabilities.inspector_capable:
            pytest.skip("backend is inspector_capable — not testing the not-capable path")
        with pytest.raises(CapabilityNotSupportedError) as exc_info:
            self.backend.build_inspector_cmd("test prompt")
        assert exc_info.value.capability == "inspector_capable"
        assert exc_info.value.backend_name == self.backend.name

    # --- Group 6: Behavioral Contracts ---

    def test_build_cmd_env_contains_mcp_forward_vars(self) -> None:
        """BackendCapabilities.mcp_env_forward_vars — vars via env injection (G6 defense)."""
        forward_vars = self.backend.capabilities.mcp_env_forward_vars
        if not forward_vars:
            pytest.skip(
                f"mcp_env_forward_vars is empty for {self.backend.name!r}"
                " — no env injection to verify"
            )
        result = self.backend.build_cmd(skill_command="do stuff", cwd="/tmp")
        for var in forward_vars:
            assert var in result.env, (
                f"{var!r} declared in mcp_env_forward_vars"
                f" but missing from build_cmd env for {self.backend.name!r}"
            )

    def test_build_resume_cmd_includes_session_id(self) -> None:
        """BackendCapabilities.session_resume_capable — embeds the session ID in cmd tuple."""
        self._require_capability("session_resume_capable")
        result = self.backend.build_resume_cmd(
            resume_session_id="test-session-id", prompt="test prompt"
        )
        assert "test-session-id" in result.cmd, (
            f"'test-session-id' not found in result.cmd for {self.backend.name!r}"
        )

    def test_setup_session_dir_and_locator_round_trip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """BackendCapabilities.session_dir_symlinks and setup_session_dir round-trip."""
        if self.backend.name == "claude-code":
            self.backend.setup_session_dir(tmp_path)
            locator = self.backend.session_locator()
            assert locator.locate_session("any-id") is None
        elif self.backend.name == "codex":
            fake_home = tmp_path / "fake_home"
            fake_home.mkdir()
            monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
            fake_log_dir = tmp_path / "fake_logs"
            fake_log_dir.mkdir()
            monkeypatch.setattr(
                "autoskillit.execution.backends.codex.default_log_dir",
                lambda: fake_log_dir,
            )
            (fake_home / ".codex").mkdir()
            session_dir = tmp_path / "session"
            session_dir.mkdir()
            (session_dir / "config.toml").write_text('[model_provider]\nname = "fake"\n')
            self.backend.setup_session_dir(session_dir)
            assert not (session_dir / "sessions").exists()
            assert not (session_dir / "archived_sessions").exists()
            date_dir = fake_log_dir / CODEX_SESSIONS_SUBDIR / "2026" / "01" / "01"
            date_dir.mkdir(parents=True)
            rollout_path = date_dir / "rollout-fake.jsonl"
            event = {
                "type": "thread.started",
                "thread_id": "fake-thread-id",
            }
            rollout_path.write_text(json.dumps(event) + "\n")
            locator = self.backend.session_locator()
            found = locator.locate_session("fake-thread-id")
            assert found is not None, "CodexSessionLocator did not find the synthetic rollout file"
            assert found == rollout_path
        else:
            pytest.fail(
                f"test_setup_session_dir_and_locator_round_trip has no coverage"
                f" for backend {self.backend.name!r}"
            )

    def test_supports_context_exhaustion_detection_is_bool(self) -> None:
        """BackendCapabilities.supports_context_exhaustion_detection — capability is bool-typed."""
        assert isinstance(self.backend.capabilities.supports_context_exhaustion_detection, bool)

    def test_supports_model_capacity_error_detection_is_bool(self) -> None:
        """BackendCapabilities.supports_model_capacity_error_detection is bool-typed."""
        assert isinstance(
            self.backend.capabilities.supports_model_capacity_error_detection,
            bool,
        )

    def test_supports_task_lifecycle_events_is_bool(self) -> None:
        """BackendCapabilities.supports_task_lifecycle_events is bool-typed."""
        assert isinstance(self.backend.capabilities.supports_task_lifecycle_events, bool)

    def test_protected_recipe_delivery_capable_is_bool(self) -> None:
        """BackendCapabilities.protected_recipe_delivery_capable — protected host gate is bool."""
        assert isinstance(self.backend.capabilities.protected_recipe_delivery_capable, bool)

    def test_terminal_explorer_capable_is_bool(self) -> None:
        """BackendCapabilities.terminal_explorer_capable — explorer support is bool-typed."""
        assert isinstance(self.backend.capabilities.terminal_explorer_capable, bool)

    def test_session_scoped_explorer_capable_is_bool(self) -> None:
        """BackendCapabilities.session_scoped_explorer_capable is bool-typed."""
        assert isinstance(self.backend.capabilities.session_scoped_explorer_capable, bool)

    def test_recipe_delivery_budget_matches_protected_capability(self) -> None:
        """BackendCapabilities.recipe_delivery_budget — protected backends select a budget."""
        capabilities = self.backend.capabilities
        if capabilities.protected_recipe_delivery_capable:
            assert capabilities.recipe_delivery_budget is not None


def test_every_capability_field_exercised_or_not_yet_live() -> None:
    fields = {f.name for f in dataclasses.fields(BackendCapabilities)}
    methods = inspect.getmembers(TestCodingAgentBackendConformance, predicate=inspect.isfunction)
    cited: set[str] = set()
    for _name, method in methods:
        doc = inspect.getdoc(method) or ""
        for field_name in fields:
            if field_name in doc:
                cited.add(field_name)
    uncovered = fields - cited - NOT_YET_LIVE
    assert not uncovered, (
        f"BackendCapabilities fields not exercised and not in NOT_YET_LIVE: {sorted(uncovered)}"
    )


def test_every_capability_field_has_classification() -> None:
    fields = {f.name for f in dataclasses.fields(BackendCapabilities)}
    assert CAPABILITY_CLASSIFICATION.keys() == fields, (
        f"CAPABILITY_CLASSIFICATION key mismatch — "
        f"missing: {sorted(fields - CAPABILITY_CLASSIFICATION.keys())}, "
        f"extra: {sorted(CAPABILITY_CLASSIFICATION.keys() - fields)}"
    )
    invalid = {
        k: v for k, v in CAPABILITY_CLASSIFICATION.items() if v not in ("REQUIRED", "OPTIONAL")
    }
    assert not invalid, f"Invalid classification values: {invalid}"
    not_yet_live_required = {
        f for f in NOT_YET_LIVE if CAPABILITY_CLASSIFICATION.get(f) == "REQUIRED"
    }
    assert not not_yet_live_required, (
        f"NOT_YET_LIVE fields must be OPTIONAL — found REQUIRED: {sorted(not_yet_live_required)}"
    )
