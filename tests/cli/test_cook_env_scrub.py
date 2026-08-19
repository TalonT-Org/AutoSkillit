"""Launch-site env-scrub contract tests for _launch_cook_session and cook().

Each test monkeypatches ``CLAUDE_CODE_SSE_PORT`` and ``ENABLE_IDE_INTEGRATION``
into the parent env, drives the launch site with ``subprocess.run`` patched,
and asserts the captured ``env`` kwarg does not contain the IDE discovery
variables and does contain the auto-connect suppressor.
"""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from autoskillit.core import CmdSpec, ManagedSessionHome, ValidatedAddDir
from autoskillit.execution.backends._backend_cmd_builder_base import SHARED_BASELINE_ENV
from tests.cli._interactive_process import InteractiveProcessStub

pytestmark = [
    pytest.mark.layer("cli"),
    pytest.mark.small,
    pytest.mark.usefixtures("_stub_interactive_prelaunch"),
]


def test_launch_cook_session_env_excludes_ide_vars(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    launch_kwargs: dict[str, object],
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
            "autoskillit.cli.session._session_launch.subprocess.Popen",
            return_value=InteractiveProcessStub(),
        ) as mock_run,
    ):
        _launch_cook_session(
            "system prompt",
            initial_message="hello",
            required_env=frozenset(),
            **launch_kwargs,
        )

    mock_run.assert_called_once()
    env = mock_run.call_args.kwargs["env"]
    assert "CLAUDE_CODE_SSE_PORT" not in env
    assert "ENABLE_IDE_INTEGRATION" not in env
    assert "VSCODE_GIT_ASKPASS_MAIN" not in env
    assert "CLAUDE_CODE_IDE_HOST_OVERRIDE" not in env
    assert env["CLAUDE_CODE_AUTO_CONNECT_IDE"] == "0"


def test_launch_cook_session_extra_env_still_applied(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    launch_kwargs: dict[str, object],
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SSE_PORT", "23270")

    from autoskillit.cli.session._session_launch import _launch_cook_session

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("autoskillit.cli._init_helpers._is_plugin_installed", return_value=False),
        patch(
            "autoskillit.cli.session._session_launch.subprocess.Popen",
            return_value=InteractiveProcessStub(),
        ) as mock_run,
    ):
        _launch_cook_session(
            "system prompt",
            extra_env={"AUTOSKILLIT_SUBSETS__DISABLED": "@json []"},
            required_env=frozenset(),
            **launch_kwargs,
        )

    env = mock_run.call_args.kwargs["env"]
    assert env["AUTOSKILLIT_SUBSETS__DISABLED"] == "@json []"
    assert "CLAUDE_CODE_SSE_PORT" not in env


def test_launch_cook_session_env_has_max_mcp_output_tokens(
    monkeypatch: pytest.MonkeyPatch,
    launch_kwargs: dict[str, object],
) -> None:
    """_launch_cook_session (order path) must produce env with MAX_MCP_OUTPUT_TOKENS."""
    from autoskillit.cli.session._session_launch import _launch_cook_session

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("autoskillit.cli._init_helpers._is_plugin_installed", return_value=False),
        patch(
            "autoskillit.cli.session._session_launch.subprocess.Popen",
            return_value=InteractiveProcessStub(),
        ) as mock_run,
    ):
        _launch_cook_session(
            "system prompt",
            initial_message="hello",
            required_env=frozenset(),
            **launch_kwargs,
        )

    env = mock_run.call_args.kwargs["env"]
    assert env["MAX_MCP_OUTPUT_TOKENS"] == SHARED_BASELINE_ENV["MAX_MCP_OUTPUT_TOKENS"]


def test_launch_cook_session_env_has_mcp_connection_nonblocking(
    monkeypatch: pytest.MonkeyPatch,
    launch_kwargs: dict[str, object],
) -> None:
    """_launch_cook_session (order path) must produce env with MCP_CONNECTION_NONBLOCKING=0."""
    from autoskillit.cli.session._session_launch import _launch_cook_session

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("autoskillit.cli._init_helpers._is_plugin_installed", return_value=False),
        patch(
            "autoskillit.cli.session._session_launch.subprocess.Popen",
            return_value=InteractiveProcessStub(),
        ) as mock_run,
    ):
        _launch_cook_session(
            "system prompt",
            initial_message="hello",
            required_env=frozenset(),
            **launch_kwargs,
        )

    env = mock_run.call_args.kwargs["env"]
    assert env["MCP_CONNECTION_NONBLOCKING"] == "0"


def _capture_cook_spec(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> CmdSpec:
    from autoskillit.cli.session._session_cook import cook
    from autoskillit.execution.backends.claude import ClaudeCodeBackend

    generated_home = tmp_path / "generated-home"
    skills_dir = generated_home / ".claude" / "skills"
    skills_dir.mkdir(parents=True)
    managed_home = ManagedSessionHome(
        launch_id="0123456789abcdef",
        generated_home=generated_home,
        skills_dir=ValidatedAddDir(path=str(skills_dir)),
        pass_fds=(),
    )
    mock_mgr = MagicMock()
    mock_mgr.managed_session.return_value = nullcontext(managed_home)
    captured: dict[str, object] = {}

    def fake_run(spec, **_kwargs):
        captured["spec"] = spec
        return SimpleNamespace(pid=101, pgid=101, returncode=0)

    monkeypatch.setattr("shutil.which", lambda _cmd: "/usr/bin/claude")
    monkeypatch.setattr("autoskillit.cli._onboarding.is_first_run", lambda _path: False)
    monkeypatch.setattr("autoskillit.cli.ui._timed_input.timed_prompt", lambda *_a, **_k: "")
    monkeypatch.setattr(
        "autoskillit.cli.install._installed_plugins.InstalledPluginsFile.contains",
        lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        "autoskillit.workspace.DefaultSessionSkillManager", lambda *_a, **_k: mock_mgr
    )
    monkeypatch.setattr(
        "autoskillit.cli.session._session_process.run_cook_attempt",
        fake_run,
    )
    monkeypatch.setattr(
        "autoskillit.cli.session._session_reload.consume_reload_sentinel",
        lambda _path: None,
    )
    monkeypatch.setattr("autoskillit.core.write_registry_entry", lambda *_a, **_k: None)

    cook(backend=ClaudeCodeBackend())
    result = captured["spec"]
    assert isinstance(result, CmdSpec)
    return result


def test_cook_command_env_excludes_ide_vars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SSE_PORT", "23270")
    monkeypatch.setenv("ENABLE_IDE_INTEGRATION", "true")
    monkeypatch.chdir(tmp_path)
    # cook() derives project_dir via the same git-toplevel helper the MCP server
    # uses; subprocess.run is patched wholesale below, so pin the helper instead.
    monkeypatch.setattr("autoskillit.cli.session._session_cook.resolve_project_dir", Path.cwd)

    spec = _capture_cook_spec(monkeypatch, tmp_path)
    env = spec.env
    assert "CLAUDE_CODE_SSE_PORT" not in env
    assert "ENABLE_IDE_INTEGRATION" not in env
    assert env["CLAUDE_CODE_AUTO_CONNECT_IDE"] == "0"


def test_cook_command_env_has_max_mcp_output_tokens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """cook() must inject MAX_MCP_OUTPUT_TOKENS=50000 into the subprocess env."""
    monkeypatch.chdir(tmp_path)

    spec = _capture_cook_spec(monkeypatch, tmp_path)
    env = spec.env
    assert env["MAX_MCP_OUTPUT_TOKENS"] == SHARED_BASELINE_ENV["MAX_MCP_OUTPUT_TOKENS"]


def test_claude_cook_command_env_excludes_codex_home_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    spec = _capture_cook_spec(monkeypatch, tmp_path)

    assert "CODEX_HOME" not in spec.env
    assert "CODEX_SQLITE_HOME" not in spec.env
