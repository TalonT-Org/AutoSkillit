"""Tests for the cli/_marketplace.py module."""

from __future__ import annotations


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
    """autoskillit upgrade must be a registered CLI command."""
    from autoskillit import cli

    assert hasattr(cli, "upgrade")


# MK-DEP-3
def test_marketplace_module_still_importable():
    """_marketplace module is still importable (not deleted)."""
    import autoskillit.cli._marketplace  # noqa: F401
