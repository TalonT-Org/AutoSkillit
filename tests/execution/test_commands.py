"""Tests for execution/commands.py — CmdSpec / ClaudeHeadlessCmd alias builders."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    BareResume,
    ClaudeFlags,
    CmdSpec,
    DirectInstall,
    MarketplaceInstall,
    NamedResume,
    NoResume,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.commands import (
    _MAX_MCP_OUTPUT_TOKENS_VALUE,
    ClaudeHeadlessCmd,
    ClaudeInteractiveCmd,
    build_headless_cmd,
    build_headless_resume_cmd,
    build_interactive_cmd,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestBuildInteractiveCmd:
    def test_returns_correct_type(self) -> None:
        result = build_interactive_cmd()
        assert isinstance(result, ClaudeInteractiveCmd)

    def test_includes_dangerously_skip_permissions(self) -> None:
        result = build_interactive_cmd()
        assert ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS in result.cmd

    def test_does_not_include_allow_dangerously_skip_permissions(self) -> None:
        result = build_interactive_cmd()
        assert ClaudeFlags.ALLOW_DANGEROUSLY_SKIP_PERMISSIONS not in result.cmd

    def test_does_not_include_prompt_flag(self) -> None:
        result = build_interactive_cmd()
        assert ClaudeFlags.PRINT not in result.cmd

    def test_starts_with_claude(self) -> None:
        result = build_interactive_cmd()
        assert result.cmd[0] == "claude"

    def test_env_is_populated_and_scrubbed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDE_CODE_SSE_PORT", "23270")
        monkeypatch.setenv("HOME", "/tmp/home")
        result = build_interactive_cmd()
        assert "CLAUDE_CODE_SSE_PORT" not in result.env
        assert result.env.get("HOME") == "/tmp/home"
        assert result.env["CLAUDE_CODE_AUTO_CONNECT_IDE"] == "0"

    def test_accepts_model(self) -> None:
        result = build_interactive_cmd(model="claude-opus-4-6")
        assert ClaudeFlags.MODEL in result.cmd
        idx = result.cmd.index(ClaudeFlags.MODEL)
        assert result.cmd[idx + 1] == "claude-opus-4-6"

    def test_no_model_flag_when_model_is_none(self) -> None:
        result = build_interactive_cmd(model=None)
        assert ClaudeFlags.MODEL not in result.cmd

    def test_includes_initial_prompt_as_positional_arg(self) -> None:
        result = build_interactive_cmd(initial_prompt="Hello chef")
        assert "Hello chef" in result.cmd
        assert ClaudeFlags.PRINT not in result.cmd  # still interactive, not headless

    def test_initial_prompt_precedes_add_dir_flag(self) -> None:
        result = build_interactive_cmd(initial_prompt="Hello chef", add_dirs=[Path("/tmp/skills")])
        prompt_idx = result.cmd.index("Hello chef")
        add_dir_idx = result.cmd.index(ClaudeFlags.ADD_DIR)
        assert prompt_idx < add_dir_idx

    def test_initial_prompt_precedes_all_variadic_flags_combined(self) -> None:
        result = build_interactive_cmd(
            initial_prompt="Hello chef",
            tools=("AskUserQuestion",),
            add_dirs=[Path("/tmp/skills")],
        )
        prompt_idx = result.cmd.index("Hello chef")
        tools_idx = result.cmd.index(ClaudeFlags.TOOLS)
        add_dir_idx = result.cmd.index(ClaudeFlags.ADD_DIR)
        assert prompt_idx < tools_idx
        assert prompt_idx < add_dir_idx

    def test_omits_prompt_when_initial_prompt_is_none(self) -> None:
        result = build_interactive_cmd()
        # cmd is just ["claude", "--dangerously-skip-permissions"]
        assert len(result.cmd) == 2

    # REQ-CMD-001
    def test_named_resume_appends_session_id(self) -> None:
        result = build_interactive_cmd(resume_spec=NamedResume(session_id="abc123"))
        assert "--resume" in result.cmd
        idx = result.cmd.index("--resume")
        assert result.cmd[idx + 1] == "abc123"

    def test_bare_resume_produces_bare_flag_no_id(self) -> None:
        result = build_interactive_cmd(resume_spec=BareResume())
        assert "--resume" in result.cmd
        idx = result.cmd.index("--resume")
        assert idx == len(result.cmd) - 1

    def test_no_resume_spec_emits_no_flag(self) -> None:
        result = build_interactive_cmd(resume_spec=NoResume())
        assert "--resume" not in result.cmd

    def test_resume_placed_before_initial_prompt(self) -> None:
        result = build_interactive_cmd(
            resume_spec=NamedResume(session_id="abc123"), initial_prompt="hello"
        )
        resume_idx = result.cmd.index("--resume")
        prompt_idx = result.cmd.index("hello")
        assert resume_idx < prompt_idx

    def test_env_has_max_mcp_output_tokens(self) -> None:
        """build_interactive_cmd must inject MAX_MCP_OUTPUT_TOKENS even with no env_extras."""
        spec = build_interactive_cmd()
        assert spec.env["MAX_MCP_OUTPUT_TOKENS"] == _MAX_MCP_OUTPUT_TOKENS_VALUE

    def test_caller_extras_override_baseline(self) -> None:
        """Caller-supplied env_extras must override the baseline default."""
        spec = build_interactive_cmd(env_extras={"MAX_MCP_OUTPUT_TOKENS": "99999"})
        assert spec.env["MAX_MCP_OUTPUT_TOKENS"] == "99999"


class TestBuildInteractiveCmdExtended:
    def test_accepts_plugin_source_direct_install(self, tmp_path: Path) -> None:
        """build_interactive_cmd with DirectInstall includes --plugin-dir flag."""
        plugin_source = DirectInstall(plugin_dir=tmp_path)
        result = build_interactive_cmd(plugin_source=plugin_source)
        assert "--plugin-dir" in result.cmd
        idx = result.cmd.index("--plugin-dir")
        assert result.cmd[idx + 1] == str(tmp_path)

    def test_accepts_add_dirs(self, tmp_path: Path) -> None:
        """build_interactive_cmd with add_dirs includes --add-dir for each entry."""
        d1, d2 = Path(tmp_path) / "a", Path(tmp_path) / "b"
        result = build_interactive_cmd(add_dirs=[d1, d2])
        assert result.cmd.count("--add-dir") == 2

    def test_marketplace_install_omits_plugin_dir_flag(self, tmp_path: Path) -> None:
        """build_interactive_cmd with MarketplaceInstall does not emit --plugin-dir."""
        result = build_interactive_cmd(plugin_source=MarketplaceInstall(cache_path=tmp_path))
        assert "--plugin-dir" not in result.cmd

    def test_no_plugin_source_omits_plugin_dir_flag(self) -> None:
        """build_interactive_cmd with no plugin_source does not emit --plugin-dir."""
        result = build_interactive_cmd()
        assert "--plugin-dir" not in result.cmd

    def test_cook_uses_builder_output(self, tmp_path: Path) -> None:
        """cook subprocess cmd is consistent with build_interactive_cmd output."""
        from unittest.mock import MagicMock, patch

        from autoskillit.core import pkg_root

        fake_skills_dir = Path(tmp_path) / "skills"
        fake_skills_dir.mkdir()
        mock_mgr = MagicMock()
        mock_mgr.init_session.return_value = fake_skills_dir

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("builtins.input", return_value=""),
            patch("sys.stdin.isatty", return_value=True),
            patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=mock_mgr),
            patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
        ):
            import autoskillit.cli.session._cook as module

            module.cook()

        actual_cmd = mock_run.call_args[0][0]
        expected_prefix = build_interactive_cmd(
            plugin_source=DirectInstall(plugin_dir=pkg_root()), add_dirs=[fake_skills_dir]
        ).cmd
        assert actual_cmd == expected_prefix


class TestBuildHeadlessCmd:
    def test_returns_correct_type(self) -> None:
        result = build_headless_cmd("some prompt")
        assert isinstance(result, ClaudeHeadlessCmd)

    def test_includes_prompt_flag(self) -> None:
        result = build_headless_cmd("some prompt")
        assert ClaudeFlags.PRINT in result.cmd

    def test_includes_dangerously_skip_permissions(self) -> None:
        result = build_headless_cmd("some prompt")
        assert ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS in result.cmd

    def test_does_not_include_allow_dangerously_skip_permissions(self) -> None:
        result = build_headless_cmd("some prompt")
        assert ClaudeFlags.ALLOW_DANGEROUSLY_SKIP_PERMISSIONS not in result.cmd

    def test_env_is_populated_and_scrubbed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDE_CODE_SSE_PORT", "23270")
        monkeypatch.setenv("HOME", "/tmp/home")
        result = build_headless_cmd("some prompt")
        assert "CLAUDE_CODE_SSE_PORT" not in result.env
        assert result.env.get("HOME") == "/tmp/home"
        assert result.env["CLAUDE_CODE_AUTO_CONNECT_IDE"] == "0"

    def test_accepts_model(self) -> None:
        result = build_headless_cmd("some prompt", model="claude-sonnet-4-6")
        assert ClaudeFlags.MODEL in result.cmd
        idx = result.cmd.index(ClaudeFlags.MODEL)
        assert result.cmd[idx + 1] == "claude-sonnet-4-6"


class TestBuildHeadlessResumeCmd:
    def test_basic_cmd_structure(self) -> None:
        result = build_headless_resume_cmd(resume_session_id="abc-123", prompt="Emit token")
        assert result.cmd[0] == "claude"
        assert ClaudeFlags.PRINT in result.cmd
        assert ClaudeFlags.RESUME in result.cmd
        assert result.cmd[result.cmd.index(ClaudeFlags.RESUME) + 1] == "abc-123"
        assert ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS in result.cmd
        assert ClaudeFlags.OUTPUT_FORMAT in result.cmd
        assert result.cmd[result.cmd.index(ClaudeFlags.OUTPUT_FORMAT) + 1] == "json"
        prompt_idx = result.cmd.index(ClaudeFlags.PRINT) + 1
        assert result.cmd[prompt_idx] == "Emit token"

    def test_env_is_populated_with_ide_suppression(self) -> None:
        from collections.abc import Mapping

        result = build_headless_resume_cmd(resume_session_id="abc-123", prompt="Emit token")
        assert isinstance(result.env, Mapping)
        assert len(result.env) > 0
        assert result.env.get("CLAUDE_CODE_AUTO_CONNECT_IDE") == "0"

    def test_env_has_max_mcp_output_tokens(self) -> None:
        """build_headless_resume_cmd must inject MAX_MCP_OUTPUT_TOKENS even with no env_extras."""
        spec = build_headless_resume_cmd(resume_session_id="abc", prompt="Emit token")
        assert spec.env["MAX_MCP_OUTPUT_TOKENS"] == _MAX_MCP_OUTPUT_TOKENS_VALUE

    def test_no_plugin_dir_by_default(self) -> None:
        result = build_headless_resume_cmd(resume_session_id="abc-123", prompt="Emit token")
        assert ClaudeFlags.PLUGIN_DIR not in result.cmd

    def test_with_plugin_source_direct_install(self) -> None:
        result = build_headless_resume_cmd(
            resume_session_id="abc-123",
            prompt="Emit token",
            plugin_source=DirectInstall(plugin_dir=Path("/tmp/plugin")),
        )
        assert ClaudeFlags.PLUGIN_DIR in result.cmd
        idx = result.cmd.index(ClaudeFlags.PLUGIN_DIR)
        assert result.cmd[idx + 1] == "/tmp/plugin"


def test_session_baseline_env_contains_mcp_connection_nonblocking() -> None:
    from autoskillit.execution.commands import _SESSION_BASELINE_ENV

    assert "MCP_CONNECTION_NONBLOCKING" in _SESSION_BASELINE_ENV
    assert _SESSION_BASELINE_ENV["MCP_CONNECTION_NONBLOCKING"] == "0"


def test_interactive_cmd_env_has_mcp_connection_nonblocking() -> None:
    spec = build_interactive_cmd()
    assert spec.env.get("MCP_CONNECTION_NONBLOCKING") == "0"


def test_headless_cmd_is_cmdspec_alias() -> None:
    """ClaudeHeadlessCmd must be a type alias for CmdSpec, not a separate class."""
    from autoskillit.execution.commands import ClaudeHeadlessCmd

    assert ClaudeHeadlessCmd is CmdSpec


@pytest.mark.parametrize(
    "builder,kwargs",
    [
        (build_headless_cmd, {"prompt": "go"}),
        (build_headless_resume_cmd, {"resume_session_id": "abc", "prompt": "go"}),
    ],
)
def test_headless_builders_return_tuple_cmd(builder, kwargs) -> None:
    """Builder return values must have cmd as tuple, not list."""
    result = builder(**kwargs)
    assert isinstance(result.cmd, tuple)


def test_cmdspec_importable_from_execution() -> None:
    """CmdSpec must be importable from the execution package gateway."""
    from autoskillit.execution import CmdSpec as execution_CmdSpec

    assert execution_CmdSpec is CmdSpec


def test_cmdspec_in_execution_all() -> None:
    """CmdSpec must appear in execution.__all__."""
    import autoskillit.execution as m

    assert "CmdSpec" in m.__all__


def test_system_prompt_forwarded_to_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    original = ClaudeCodeBackend.build_interactive_cmd

    def spy(self_inner: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return original(self_inner, **kwargs)

    monkeypatch.setattr(ClaudeCodeBackend, "build_interactive_cmd", spy)
    build_interactive_cmd(system_prompt="forwarded-prompt")
    assert captured["system_prompt"] == "forwarded-prompt"


class TestShimDeprecationWarnings:
    def test_build_interactive_cmd_warns(self) -> None:
        with pytest.warns(DeprecationWarning) as rec:
            build_interactive_cmd()
        assert any(
            "deprecated" in str(w.message).lower() or "ClaudeCodeBackend" in str(w.message)
            for w in rec.list
        )

    def test_build_headless_cmd_warns(self) -> None:
        with pytest.warns(DeprecationWarning) as rec:
            build_headless_cmd("test prompt")
        assert any(
            "deprecated" in str(w.message).lower() or "ClaudeCodeBackend" in str(w.message)
            for w in rec.list
        )

    def test_build_headless_resume_cmd_warns(self) -> None:
        with pytest.warns(DeprecationWarning) as rec:
            build_headless_resume_cmd(resume_session_id="abc", prompt="go")
        assert any(
            "deprecated" in str(w.message).lower() or "ClaudeCodeBackend" in str(w.message)
            for w in rec.list
        )
