"""Tests for the cli/_marketplace.py module."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


# MK1
def test_marketplace_module_exists():
    pass  # ImportError if missing


# MK2
def test_install_importable_from_marketplace():
    from autoskillit.cli._marketplace import install  # noqa: F401


# MK3
def test_upgrade_importable_from_marketplace():
    from autoskillit.cli._marketplace import upgrade  # noqa: F401


# MK4
def test_ensure_marketplace_importable_from_marketplace():
    from autoskillit.cli._marketplace import _ensure_marketplace  # noqa: F401


# MK5
def test_clear_plugin_cache_importable_from_marketplace():
    from autoskillit.cli._marketplace import _clear_plugin_cache  # noqa: F401


# MK6
def test_install_defined_in_app_module():
    """install command is registered in cli/app.py as a thin @app.command wrapper."""
    import importlib
    import inspect

    app_mod = importlib.import_module("autoskillit.cli.app")
    src = inspect.getsource(app_mod)
    assert "def install(" in src


# MK-DEP-1
def test_install_registered_as_cli_command():
    """autoskillit install is a registered CLI command (delegates to _marketplace)."""
    from autoskillit import cli

    assert hasattr(cli, "install")


# MK-DEP-2
def test_upgrade_is_registered_as_cli_command():
    """autoskillit upgrade must be a registered CLI command (defined in cli/app.py)."""
    import importlib
    import inspect

    app_mod = importlib.import_module("autoskillit.cli.app")
    src = inspect.getsource(app_mod)
    assert "def upgrade(" in src


# MK-DEP-3
def test_marketplace_module_still_importable():
    """_marketplace module is still importable (not deleted)."""
    import autoskillit.cli._marketplace  # noqa: F401


class TestInstallPluginInstallCapableGuard:
    """Verify install() gates on backend.capabilities.plugin_install_capable."""

    def test_install_rejects_when_plugin_install_not_capable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        from unittest.mock import MagicMock

        from autoskillit.config import AgentBackendConfig, AutomationConfig

        mock_cfg = AutomationConfig(agent_backend=AgentBackendConfig(backend="test-backend"))
        monkeypatch.setattr("autoskillit.config.load_config", lambda _: mock_cfg)

        mock_backend = MagicMock()
        mock_backend.capabilities.plugin_install_capable = False
        monkeypatch.setattr("autoskillit.execution.get_backend", lambda _: mock_backend)

        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setattr(Path, "cwd", staticmethod(lambda: tmp_path))

        from autoskillit.cli._marketplace import install

        result = install(scope="user")

        assert result is False
        captured = capsys.readouterr()
        assert "plugin_install_capable" in captured.out
        assert "test-backend" in captured.out

    def test_install_passes_guard_when_plugin_install_capable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import importlib
        import subprocess
        from unittest.mock import MagicMock

        from autoskillit.config import AgentBackendConfig, AutomationConfig

        mock_cfg = AutomationConfig(agent_backend=AgentBackendConfig(backend="claude-code"))
        monkeypatch.setattr("autoskillit.config.load_config", lambda _: mock_cfg)

        mock_backend = MagicMock()
        mock_backend.capabilities.plugin_install_capable = True
        monkeypatch.setattr("autoskillit.execution.get_backend", lambda _: mock_backend)

        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setattr(Path, "cwd", staticmethod(lambda: tmp_path))
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/claude")

        _app_mod = importlib.import_module("autoskillit.cli._marketplace")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        monkeypatch.setattr(_app_mod, "evict_direct_mcp_entry", lambda _: False)
        monkeypatch.setattr(
            "autoskillit.cli._hooks._evict_stale_autoskillit_hooks", lambda _: None
        )
        monkeypatch.setattr(_app_mod, "generate_hooks_json", lambda: {})
        monkeypatch.setattr(_app_mod, "atomic_write", lambda *a, **kw: None)

        called = []
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **kw: (
                called.append(a),
                subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
            )[1],
        )

        from autoskillit.cli._marketplace import install

        result = install(scope="user")

        assert result is True
        assert len(called) >= 1
