from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    CmdSpec,
    CodingAgentBackend,
    EnvPolicy,
    OutputFormat,
    ResultParser,
    SessionLocator,
    StreamParser,
)
from autoskillit.execution.backends import ClaudeCodeBackend, ClaudeStreamParser

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestClaudeCodeBackend:
    def test_isinstance_coding_agent_backend(self) -> None:
        assert isinstance(ClaudeCodeBackend(), CodingAgentBackend)

    def test_name_property(self) -> None:
        assert ClaudeCodeBackend().name == AGENT_BACKEND_CLAUDE_CODE

    def test_capabilities_mcp_config_capable_false(self) -> None:
        assert ClaudeCodeBackend().capabilities.mcp_config_capable is False

    def test_binary_name(self) -> None:
        assert ClaudeCodeBackend().binary_name() == "claude"

    def test_version_cmd(self) -> None:
        assert ClaudeCodeBackend().version_cmd() == ("claude", "--version")

    def test_build_cmd_returns_cmd_spec(self, tmp_path: Path) -> None:
        result = ClaudeCodeBackend().build_cmd("say hello", str(tmp_path))
        assert isinstance(result, CmdSpec)

    def test_build_cmd_cmd_is_tuple_not_list(self, tmp_path: Path) -> None:
        result = ClaudeCodeBackend().build_cmd("say hello", str(tmp_path))
        assert isinstance(result.cmd, tuple)

    def test_build_cmd_matches_build_headless_cmd(self, tmp_path: Path) -> None:
        from autoskillit.execution.commands import build_headless_cmd

        backend = ClaudeCodeBackend()
        skill_cmd = "say hello"
        direct = build_headless_cmd(skill_cmd)
        result = backend.build_cmd(skill_cmd, str(tmp_path))
        assert tuple(direct.cmd) == result.cmd
        assert direct.env == result.env
        assert result.cwd == str(tmp_path)

    def test_stream_parser_returns_stream_parser(self) -> None:
        backend = ClaudeCodeBackend()
        result = backend.stream_parser()
        assert isinstance(result, StreamParser)

    @pytest.mark.parametrize(
        ("marker_kwarg", "expected"),
        [
            ({"completion_marker": "%%DONE%%"}, "%%DONE%%"),
            ({}, ""),
        ],
        ids=["explicit-marker", "default-empty"],
    )
    def test_stream_parser_factory_completion_marker(
        self, marker_kwarg: dict[str, str], expected: str
    ) -> None:
        parser = ClaudeCodeBackend().stream_parser(**marker_kwarg)
        assert isinstance(parser, ClaudeStreamParser)
        assert parser.completion_marker == expected

    def test_result_parser_returns_result_parser(self) -> None:
        backend = ClaudeCodeBackend()
        result = backend.result_parser()
        assert isinstance(result, ResultParser)

    def test_env_policy_returns_env_policy(self) -> None:
        backend = ClaudeCodeBackend()
        result = backend.env_policy()
        assert isinstance(result, EnvPolicy)

    def test_session_locator_returns_session_locator(self) -> None:
        backend = ClaudeCodeBackend()
        result = backend.session_locator()
        assert isinstance(result, SessionLocator)

    def test_write_tool_names_returns_write_edit(self) -> None:
        backend = ClaudeCodeBackend()
        assert backend.write_tool_names() == frozenset({"Write", "Edit"})


class TestClaudeCodeBackendAgentBackendEnv:
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
        spec = ClaudeCodeBackend().build_skill_session_cmd(**self.BASE)
        assert spec.env["AUTOSKILLIT_AGENT_BACKEND"] == "claude-code"

    def test_agent_backend_overrides_parent_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "wrong-value")
        spec = ClaudeCodeBackend().build_skill_session_cmd(**self.BASE)
        assert spec.env["AUTOSKILLIT_AGENT_BACKEND"] == "claude-code"

    def test_agent_backend_present_without_parent_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)
        spec = ClaudeCodeBackend().build_skill_session_cmd(**self.BASE)
        assert spec.env["AUTOSKILLIT_AGENT_BACKEND"] == "claude-code"
