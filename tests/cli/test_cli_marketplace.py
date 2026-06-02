"""Tests for the cli/_marketplace.py module."""

from __future__ import annotations

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
    def test_rejects_when_plugin_install_not_capable(self, monkeypatch, capsys):
        """install() returns False with rejection message when capability is False."""
        from unittest.mock import MagicMock

        mock_cfg = MagicMock()
        mock_cfg.agent_backend.backend = "some-backend"
        monkeypatch.setattr("autoskillit.config.load_config", lambda _: mock_cfg)

        mock_backend = MagicMock()
        mock_backend.capabilities.plugin_install_capable = False
        monkeypatch.setattr("autoskillit.execution.get_backend", lambda name: mock_backend)

        from autoskillit.cli._marketplace import install

        result = install(scope="user")

        assert result is False
        captured = capsys.readouterr()
        assert "plugin_install_capable" in captured.out

    def test_passes_guard_when_plugin_install_capable(self, monkeypatch, capsys):
        """install() does not reject at capability guard when True."""
        from unittest.mock import MagicMock

        mock_cfg = MagicMock()
        mock_cfg.agent_backend.backend = "claude-code"
        monkeypatch.setattr("autoskillit.config.load_config", lambda _: mock_cfg)

        mock_backend = MagicMock()
        mock_backend.capabilities.plugin_install_capable = True
        monkeypatch.setattr("autoskillit.execution.get_backend", lambda name: mock_backend)

        # Stub CLAUDECODE env to trigger the next guard (deferred exit)
        monkeypatch.setenv("CLAUDECODE", "1")

        from autoskillit.cli._marketplace import install

        result = install(scope="user")

        # Should reach the CLAUDECODE guard (returns False for deferred),
        # NOT the capability guard
        assert result is False
        captured = capsys.readouterr()
        assert "plugin_install_capable" not in captured.out
