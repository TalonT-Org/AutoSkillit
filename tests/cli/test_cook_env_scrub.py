"""Launch-site env-scrub contract tests for _launch_cook_session and cook().

Each test monkeypatches ``CLAUDE_CODE_SSE_PORT`` and ``ENABLE_IDE_INTEGRATION``
into the parent env, drives the launch site with ``subprocess.run`` patched,
and asserts the captured ``env`` kwarg does not contain the IDE discovery
variables and does contain the auto-connect suppressor.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoskillit.execution import _MAX_MCP_OUTPUT_TOKENS_VALUE

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def test_launch_cook_session_env_excludes_ide_vars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SSE_PORT", "23270")
    monkeypatch.setenv("ENABLE_IDE_INTEGRATION", "true")
    monkeypatch.setenv("VSCODE_GIT_ASKPASS_MAIN", "/fake/vscode")
    monkeypatch.setenv("CLAUDE_CODE_IDE_HOST_OVERRIDE", "host")

    from autoskillit.cli.session._session_launch import _launch_cook_session

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("autoskillit.cli._init_helpers._is_plugin_installed", return_value=False),
        patch(
            "autoskillit.cli.session._session_launch.subprocess.run",
            return_value=MagicMock(returncode=0),
        ) as mock_run,
    ):
        _launch_cook_session("system prompt", initial_message="hello")

    mock_run.assert_called_once()
    env = mock_run.call_args.kwargs["env"]
    assert "CLAUDE_CODE_SSE_PORT" not in env
    assert "ENABLE_IDE_INTEGRATION" not in env
    assert "VSCODE_GIT_ASKPASS_MAIN" not in env
    assert "CLAUDE_CODE_IDE_HOST_OVERRIDE" not in env
    assert env["CLAUDE_CODE_AUTO_CONNECT_IDE"] == "0"


def test_launch_cook_session_extra_env_still_applied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SSE_PORT", "23270")

    from autoskillit.cli.session._session_launch import _launch_cook_session

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("autoskillit.cli._init_helpers._is_plugin_installed", return_value=False),
        patch(
            "autoskillit.cli.session._session_launch.subprocess.run",
            return_value=MagicMock(returncode=0),
        ) as mock_run,
    ):
        _launch_cook_session(
            "system prompt",
            extra_env={"AUTOSKILLIT_SUBSETS__DISABLED": "@json []"},
        )

    env = mock_run.call_args.kwargs["env"]
    assert env["AUTOSKILLIT_SUBSETS__DISABLED"] == "@json []"
    assert "CLAUDE_CODE_SSE_PORT" not in env


def test_launch_cook_session_env_has_max_mcp_output_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_launch_cook_session (order path) must produce env with MAX_MCP_OUTPUT_TOKENS."""
    from autoskillit.cli.session._session_launch import _launch_cook_session

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("autoskillit.cli._init_helpers._is_plugin_installed", return_value=False),
        patch(
            "autoskillit.cli.session._session_launch.subprocess.run",
            return_value=MagicMock(returncode=0),
        ) as mock_run,
    ):
        _launch_cook_session("system prompt", initial_message="hello")

    env = mock_run.call_args.kwargs["env"]
    assert env["MAX_MCP_OUTPUT_TOKENS"] == _MAX_MCP_OUTPUT_TOKENS_VALUE


def test_launch_cook_session_env_has_mcp_connection_nonblocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_launch_cook_session (order path) must produce env with MCP_CONNECTION_NONBLOCKING=0."""
    from autoskillit.cli.session._session_launch import _launch_cook_session

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("autoskillit.cli._init_helpers._is_plugin_installed", return_value=False),
        patch(
            "autoskillit.cli.session._session_launch.subprocess.run",
            return_value=MagicMock(returncode=0),
        ) as mock_run,
    ):
        _launch_cook_session("system prompt", initial_message="hello")

    env = mock_run.call_args.kwargs["env"]
    assert env["MCP_CONNECTION_NONBLOCKING"] == "0"


def test_cook_command_env_excludes_ide_vars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SSE_PORT", "23270")
    monkeypatch.setenv("ENABLE_IDE_INTEGRATION", "true")
    monkeypatch.chdir(tmp_path)

    fake_skills_dir = tmp_path / "skills"
    fake_skills_dir.mkdir()
    mock_mgr = MagicMock()
    mock_mgr.init_session.return_value = fake_skills_dir

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("builtins.input", return_value=""),
        patch("sys.stdin.isatty", return_value=True),
        patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=mock_mgr),
        patch(
            "autoskillit.cli.session._cook.subprocess.run",
            return_value=MagicMock(returncode=0),
        ) as mock_run,
        patch("autoskillit.cli.session._cook.terminal_guard"),
    ):
        from autoskillit.cli.session._cook import cook

        cook()

    mock_run.assert_called_once()
    env = mock_run.call_args.kwargs["env"]
    assert "CLAUDE_CODE_SSE_PORT" not in env
    assert "ENABLE_IDE_INTEGRATION" not in env
    assert env["CLAUDE_CODE_AUTO_CONNECT_IDE"] == "0"


def test_cook_command_env_has_max_mcp_output_tokens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """cook() must inject MAX_MCP_OUTPUT_TOKENS=50000 into the subprocess env."""
    monkeypatch.chdir(tmp_path)

    fake_skills_dir = tmp_path / "skills"
    fake_skills_dir.mkdir()
    mock_mgr = MagicMock()
    mock_mgr.init_session.return_value = fake_skills_dir

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("builtins.input", return_value=""),
        patch("sys.stdin.isatty", return_value=True),
        patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=mock_mgr),
        patch(
            "autoskillit.cli.session._cook.subprocess.run",
            return_value=MagicMock(returncode=0),
        ) as mock_run,
        patch("autoskillit.cli.session._cook.terminal_guard"),
    ):
        from autoskillit.cli.session._cook import cook

        cook()

    env = mock_run.call_args.kwargs["env"]
    assert env["MAX_MCP_OUTPUT_TOKENS"] == _MAX_MCP_OUTPUT_TOKENS_VALUE
