from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    BackendCapabilities,
    BackendConventions,
    CmdSpec,
    CodingAgentBackend,
    DirectInstall,
    EnvPolicy,
    ResultParser,
    SessionLocator,
    SkillSessionConfig,
    StreamParser,
)
from autoskillit.execution.backends import BACKEND_REGISTRY, get_backend

from .test_backend_contract_base import BackendContractBase

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


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
        assert isinstance(self.backend.name, str)
        assert len(self.backend.name) > 0

    def test_capabilities_returns_backend_capabilities(self) -> None:
        assert isinstance(self.backend.capabilities, BackendCapabilities)

    def test_conventions_returns_backend_conventions(self) -> None:
        assert isinstance(self.backend.conventions, BackendConventions)

    def test_write_tool_names_returns_nonempty_frozenset(self) -> None:
        result = self.backend.write_tool_names()
        assert isinstance(result, frozenset)
        assert len(result) >= 1

    def test_binary_name_returns_non_empty_string(self) -> None:
        result = self.backend.binary_name()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_translate_model_returns_string(self) -> None:
        result = self.backend.translate_model("sonnet")
        assert isinstance(result, str)

    def test_model_config_overrides_returns_tuple(self) -> None:
        result = self.backend.model_config_overrides("sonnet")
        assert isinstance(result, tuple)

    # --- Group 2: Sub-protocol Factories ---

    def test_stream_parser_no_marker_returns_stream_parser(self) -> None:
        result = self.backend.stream_parser()
        assert isinstance(result, StreamParser)

    def test_stream_parser_with_marker_stores_completion_marker(self) -> None:
        parser = self.backend.stream_parser(completion_marker="%%DONE%%")
        assert parser.completion_marker == "%%DONE%%"  # type: ignore[attr-defined]

    def test_result_parser_returns_result_parser(self) -> None:
        result = self.backend.result_parser()
        assert isinstance(result, ResultParser)

    def test_env_policy_returns_env_policy(self) -> None:
        result = self.backend.env_policy()
        assert isinstance(result, EnvPolicy)

    def test_session_locator_returns_session_locator(self) -> None:
        result = self.backend.session_locator()
        assert isinstance(result, SessionLocator)

    def test_session_locator_locate_empty_string_returns_none(self) -> None:
        locator = self.backend.session_locator()
        assert locator.locate_session("") is None

    # --- Group 3: Command-builder Contracts ---

    def test_build_cmd_returns_cmd_spec(self) -> None:
        result = self.backend.build_cmd(skill_command="do stuff", cwd="/tmp")
        assert isinstance(result, CmdSpec)
        assert isinstance(result.cmd, tuple)

    def test_build_skill_session_cmd_with_default_config_returns_cmd_spec(self) -> None:
        result = self.backend.build_skill_session_cmd(
            skill_command="/test-skill", cwd="/work", config=SkillSessionConfig()
        )
        assert isinstance(result, CmdSpec)
        assert isinstance(result.cmd, tuple)

    def test_build_interactive_cmd_returns_cmd_spec(self) -> None:
        result = self.backend.build_interactive_cmd()
        assert isinstance(result, CmdSpec)
        assert isinstance(result.cmd, tuple)

    def test_validate_session_layout_returns_list(self, tmp_path: Path) -> None:
        result = self.backend.validate_session_layout(tmp_path)
        assert isinstance(result, list)

    def test_validate_skill_content_returns_list(self) -> None:
        result = self.backend.validate_skill_content("")
        assert isinstance(result, list)

    def test_list_plugins_returns_list(self) -> None:
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
        self._require_capability("session_resume_capable")
        result = self.backend.build_resume_cmd(
            resume_session_id="test-session-id", prompt="test prompt"
        )
        assert isinstance(result, CmdSpec)
        assert len(result.cmd) > 0

    def test_build_food_truck_cmd_when_capable(self) -> None:
        self._require_capability("food_truck_capable")
        result = self.backend.build_food_truck_cmd(
            orchestrator_prompt="x",
            plugin_source=DirectInstall(plugin_dir=Path("/tmp")),
            cwd="/tmp",
            completion_marker="%%X%%",
        )
        assert isinstance(result, CmdSpec)

    def test_build_inspector_cmd_raises_when_not_capable(self) -> None:
        if self.backend.capabilities.inspector_capable:
            pytest.skip("backend is inspector_capable — not testing the not-capable path")
        with pytest.raises(RuntimeError) as exc_info:
            self.backend.build_inspector_cmd("test prompt")
        assert "not yet implemented" in str(exc_info.value)
