"""Unit tests for _is_plugin_installed backend guard."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autoskillit.cli._init_helpers import _is_plugin_installed

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


class TestIsPluginInstalledBackendGuard:
    """Backend guard returns False without subprocess for non-claude-code."""

    def test_non_claude_code_backend_returns_false(self):
        result = _is_plugin_installed(agent_backend="aider")
        assert result is False

    def test_claude_code_backend_calls_subprocess(self, monkeypatch):
        called = []

        def fake_run(*args, **kwargs):
            called.append(args)
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="autoskillit\n")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = _is_plugin_installed(agent_backend="claude-code")
        assert result is True
        assert len(called) == 1


class TestRegisterAllBackendKwarg:
    """_register_all accepts backend keyword argument."""

    def test_register_all_accepts_backend_kwarg(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """_register_all() accepts backend keyword argument without raising TypeError."""
        from unittest.mock import MagicMock

        import autoskillit.cli._hooks as _hooks_mod
        import autoskillit.core.paths as _core_paths
        from autoskillit.cli._init_helpers import _register_all

        # Patch at source modules — _register_all uses lazy `from ... import` so
        # patching on _init_helpers would not intercept the local bindings.
        monkeypatch.setattr(_hooks_mod, "sweep_all_scopes_for_orphans", lambda p: None)
        monkeypatch.setattr(_hooks_mod, "sync_hooks_to_settings", lambda p: None)
        monkeypatch.setattr(_hooks_mod, "_evict_stale_autoskillit_hooks", lambda p: None)
        monkeypatch.setattr(
            _hooks_mod, "_claude_settings_path", lambda s: tmp_path / "settings.json"
        )
        monkeypatch.setattr(_core_paths, "pkg_root", lambda: tmp_path / "pkg")
        # _is_plugin_installed and is_git_worktree are already auto-patched by conftest.py
        monkeypatch.setattr("autoskillit.cli._init_helpers._register_mcp_server", lambda p: None)
        monkeypatch.setattr(
            "autoskillit.cli._init_helpers._user_claude_json_path",
            lambda: tmp_path / ".claude.json",
        )
        monkeypatch.setattr(
            "autoskillit.cli._init_helpers._create_secrets_template", lambda p: None
        )
        monkeypatch.setattr("autoskillit.cli._init_helpers._prompt_github_repo", lambda: None)
        monkeypatch.setattr(
            "autoskillit.cli._init_helpers.evict_direct_mcp_entry", lambda p: False
        )
        monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))
        monkeypatch.setattr("autoskillit.core.ensure_project_temp", lambda p: tmp_path / "temp")
        (tmp_path / "pkg").mkdir()

        # Should not raise TypeError
        _register_all("user", tmp_path, backend=MagicMock())


class TestInitBackendResolution:
    """Init command resolves backend via get_backend()."""

    def test_init_resolves_backend_via_get_backend(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """init command calls get_backend with config.agent_backend.backend value."""
        from unittest.mock import MagicMock, patch

        get_backend_calls: list = []

        def fake_get_backend(name: str):
            get_backend_calls.append(name)
            return MagicMock()

        mock_config = MagicMock()
        mock_config.agent_backend.backend = "claude-code"

        # Create minimal project structure
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        config_dir = project_dir / ".autoskillit"
        config_dir.mkdir()

        # Patch gate to accept bypass
        import autoskillit.cli._init_helpers as init_helpers_mod

        monkeypatch.setattr(
            init_helpers_mod,
            "_check_secret_scanning",
            lambda p: MagicMock(passed=True, bypass_accepted=False),
        )

        # Patch _register_all to avoid actual file operations
        register_called_with: dict = {}

        def fake_register_all(scope, proj_dir, *, backend=None):
            register_called_with["scope"] = scope
            register_called_with["backend"] = backend

        with (
            patch("autoskillit.config.load_config", return_value=mock_config),
            patch("autoskillit.execution.backends.get_backend", side_effect=fake_get_backend),
            patch("autoskillit.cli._init_helpers._register_all", side_effect=fake_register_all),
        ):
            # init() uses click — invoke it directly
            from click.testing import CliRunner

            from autoskillit.cli.app import init

            runner = CliRunner()
            runner.invoke(init, ["--scope", "user"], input="n\n", catch_exceptions=False, obj={})
            # Command may exit early; get_backend call is what matters

        assert get_backend_calls, "get_backend must have been called"
        assert get_backend_calls[0] == "claude-code"
