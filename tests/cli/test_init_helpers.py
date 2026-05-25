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

        # Create minimal project structure with an existing config so init skips prompting
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        config_dir = project_dir / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("test_check:\n  command: [pytest]\n")

        monkeypatch.chdir(project_dir)

        with (
            patch(
                "autoskillit.cli.app._check_secret_scanning",
                lambda p: MagicMock(passed=True, bypass_accepted=False),
            ),
            patch("autoskillit.config.load_config", return_value=mock_config),
            patch("autoskillit.execution.get_backend", side_effect=fake_get_backend),
            patch("autoskillit.cli.app._register_all", side_effect=lambda *a, **kw: None),
        ):
            from autoskillit.cli.app import init

            init(scope="user", force=False)

        assert get_backend_calls, "get_backend must have been called"
        assert get_backend_calls[0] == "claude-code"


class TestRegisterAllBackendDispatch:
    """Dispatch tests: codex calls ensure_codex_mcp_registered, claude-code calls _register_mcp_server."""

    def _setup_register_all(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, backend_name: str
    ) -> tuple[list, list]:
        """Shared setup: patch all side effects, return (codex_calls, mcp_calls)."""
        from unittest.mock import MagicMock

        import autoskillit.cli._hooks as _hooks_mod
        import autoskillit.core.paths as _core_paths

        codex_calls: list = []
        mcp_calls: list = []

        monkeypatch.setattr(_hooks_mod, "sweep_all_scopes_for_orphans", lambda p: None)
        monkeypatch.setattr(_hooks_mod, "sync_hooks_to_settings", lambda p: None)
        monkeypatch.setattr(_hooks_mod, "_evict_stale_autoskillit_hooks", lambda p: None)
        monkeypatch.setattr(
            _hooks_mod, "_claude_settings_path", lambda s: tmp_path / "settings.json"
        )
        monkeypatch.setattr(_core_paths, "pkg_root", lambda: tmp_path / "pkg")
        monkeypatch.setattr(
            "autoskillit.cli._init_helpers._register_mcp_server",
            lambda p: mcp_calls.append("register_mcp"),
        )
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
        monkeypatch.setattr(
            "autoskillit.execution.ensure_codex_mcp_registered",
            lambda **kwargs: codex_calls.append("ensure_codex") or True,
        )
        monkeypatch.setattr(
            "autoskillit.cli._hooks_codex.sync_hooks_to_codex_config",
            lambda **kwargs: True,
        )
        # Override conftest's blanket patch on _is_plugin_installed to let real logic run
        monkeypatch.setattr(
            "autoskillit.cli._init_helpers._is_plugin_installed",
            lambda **kwargs: False,
        )

        # Config mock
        mock_config = MagicMock()
        mock_config.agent_backend.backend = backend_name
        monkeypatch.setattr("autoskillit.config.load_config", lambda p=None: mock_config)

        (tmp_path / "pkg").mkdir(exist_ok=True)

        return codex_calls, mcp_calls

    def test_codex_backend_calls_ensure_codex_mcp_registered(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from autoskillit.cli._init_helpers import _register_all

        codex_calls, mcp_calls = self._setup_register_all(monkeypatch, tmp_path, "codex")
        _register_all("user", tmp_path)
        assert codex_calls, "ensure_codex_mcp_registered must be called for codex backend"
        assert not mcp_calls, "_register_mcp_server must NOT be called for codex backend"

    def test_claude_code_backend_calls_register_mcp_server(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from autoskillit.cli._init_helpers import _register_all

        codex_calls, mcp_calls = self._setup_register_all(monkeypatch, tmp_path, "claude-code")
        _register_all("user", tmp_path)
        assert mcp_calls, "_register_mcp_server must be called for claude-code backend"
        assert not codex_calls, "ensure_codex_mcp_registered must NOT be called for claude-code"

    def test_codex_backend_does_not_write_claude_json(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from autoskillit.cli._init_helpers import _register_all

        claude_json_calls: list = []
        codex_calls, mcp_calls = self._setup_register_all(monkeypatch, tmp_path, "codex")
        monkeypatch.setattr(
            "autoskillit.cli._init_helpers._user_claude_json_path",
            lambda: claude_json_calls.append("called") or (tmp_path / ".claude.json"),
        )
        monkeypatch.setattr(
            "autoskillit.cli._init_helpers.evict_direct_mcp_entry",
            lambda p: claude_json_calls.append("evict") or False,
        )
        _register_all("user", tmp_path)
        assert not mcp_calls, "_register_mcp_server must NOT be called for codex"
        # Neither _user_claude_json_path nor evict_direct_mcp_entry should be called
        assert "evict" not in claude_json_calls

    def test_init_passes_agent_backend_to_register_all(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from unittest.mock import MagicMock, patch

        register_all_calls: list = []

        def capture_register_all(*args, **kwargs):
            register_all_calls.append(kwargs)

        mock_config = MagicMock()
        mock_config.agent_backend.backend = "codex"

        mock_backend = MagicMock()
        mock_backend.name = "codex"

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        config_dir = project_dir / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("test_check:\n  command: [pytest]\n")

        monkeypatch.chdir(project_dir)

        with (
            patch(
                "autoskillit.cli.app._check_secret_scanning",
                lambda p: MagicMock(passed=True, bypass_accepted=False),
            ),
            patch("autoskillit.config.load_config", return_value=mock_config),
            patch("autoskillit.execution.get_backend", return_value=mock_backend),
            patch("autoskillit.cli.app._register_all", side_effect=capture_register_all),
        ):
            from autoskillit.cli.app import init

            init(scope="user", force=False)

        assert register_all_calls, "_register_all must have been called"
        assert "backend" in register_all_calls[0]
        assert register_all_calls[0]["backend"].name == "codex"


class TestRegisterAllCodexHookWiring:
    """Codex hook registration is wired into _register_all()."""

    def _setup(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, backend_name: str
    ) -> tuple[list, list]:
        """Shared setup: patch all side effects, return (codex_calls, hook_sync_calls)."""
        from unittest.mock import MagicMock

        import autoskillit.cli._hooks as _hooks_mod
        import autoskillit.core.paths as _core_paths

        codex_calls: list = []
        hook_sync_calls: list = []

        monkeypatch.setattr(_hooks_mod, "sweep_all_scopes_for_orphans", lambda p: None)
        monkeypatch.setattr(_hooks_mod, "sync_hooks_to_settings", lambda p: None)
        monkeypatch.setattr(_hooks_mod, "_evict_stale_autoskillit_hooks", lambda p: None)
        monkeypatch.setattr(
            _hooks_mod, "_claude_settings_path", lambda s: tmp_path / "settings.json"
        )
        monkeypatch.setattr(_core_paths, "pkg_root", lambda: tmp_path / "pkg")
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
        monkeypatch.setattr(
            "autoskillit.execution.ensure_codex_mcp_registered",
            lambda **kwargs: codex_calls.append("ensure_codex") or True,
        )
        monkeypatch.setattr(
            "autoskillit.cli._hooks_codex.sync_hooks_to_codex_config",
            lambda **kwargs: hook_sync_calls.append("sync_hooks") or True,
        )
        monkeypatch.setattr(
            "autoskillit.cli._init_helpers._is_plugin_installed",
            lambda **kwargs: False,
        )

        mock_config = MagicMock()
        mock_config.agent_backend.backend = backend_name
        monkeypatch.setattr("autoskillit.config.load_config", lambda p=None: mock_config)

        (tmp_path / "pkg").mkdir(exist_ok=True)

        return codex_calls, hook_sync_calls

    def test_codex_backend_calls_sync_hooks_to_codex_config(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from autoskillit.cli._init_helpers import _register_all

        codex_calls, hook_sync_calls = self._setup(monkeypatch, tmp_path, "codex")
        _register_all("user", tmp_path)
        assert len(hook_sync_calls) == 1

    def test_claude_code_backend_does_not_call_sync_hooks_to_codex_config(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from autoskillit.cli._init_helpers import _register_all

        codex_calls, hook_sync_calls = self._setup(monkeypatch, tmp_path, "claude-code")
        _register_all("user", tmp_path)
        assert not hook_sync_calls

    def test_sync_hooks_to_codex_config_exception_propagates(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from autoskillit.cli._init_helpers import _register_all

        codex_calls, _ = self._setup(monkeypatch, tmp_path, "codex")
        monkeypatch.setattr(
            "autoskillit.cli._hooks_codex.sync_hooks_to_codex_config",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("hook sync failed")),
        )
        with pytest.raises(RuntimeError, match="hook sync failed"):
            _register_all("user", tmp_path)
