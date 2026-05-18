"""Tests for ClaudeCodeBackend."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    CLAUDE_CODE_CAPABILITIES,
    CodingAgentBackend,
    EnvPolicy,
    ResultParser,
    SessionLocator,
    StreamParser,
)
from autoskillit.execution.backends import ClaudeCodeBackend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestClaudeCodeBackend:
    def test_isinstance_coding_agent_backend(self) -> None:
        assert isinstance(ClaudeCodeBackend(), CodingAgentBackend)

    def test_name_property(self) -> None:
        assert ClaudeCodeBackend().name == AGENT_BACKEND_CLAUDE_CODE

    def test_capabilities_property(self) -> None:
        assert ClaudeCodeBackend().capabilities == CLAUDE_CODE_CAPABILITIES

    def test_binary_name(self) -> None:
        assert ClaudeCodeBackend().binary_name() == "claude"

    def test_version_cmd(self) -> None:
        assert ClaudeCodeBackend().version_cmd() == ("claude", "--version")

    def test_build_cmd_returns_cmd_spec(self) -> None:
        result = ClaudeCodeBackend().build_cmd("say hello", "/tmp")
        assert hasattr(result, "cmd")
        assert hasattr(result, "env")

    def test_build_cmd_cmd_is_tuple_not_list(self) -> None:
        result = ClaudeCodeBackend().build_cmd("say hello", "/tmp")
        assert isinstance(result.cmd, tuple)

    def test_build_cmd_matches_build_headless_cmd(self) -> None:
        from autoskillit.execution.commands import build_headless_cmd

        backend = ClaudeCodeBackend()
        skill_cmd = "say hello"
        direct = build_headless_cmd(skill_cmd)
        result = backend.build_cmd(skill_cmd, "/tmp")
        assert tuple(direct.cmd) == result.cmd
        assert direct.env == result.env

    def test_stream_parser_returns_stream_parser(self) -> None:
        backend = ClaudeCodeBackend()
        result = backend.stream_parser()
        assert isinstance(result, StreamParser)

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
