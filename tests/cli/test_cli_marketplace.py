"""Tests for the cli/_marketplace.py module."""

from __future__ import annotations

from unittest.mock import patch

import pytest


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
def test_install_not_defined_in_app_module():
    """install must have moved -- not defined in cli/app.py."""
    import importlib
    import inspect

    app_mod = importlib.import_module("autoskillit.cli.app")

    src = inspect.getsource(app_mod)
    # The @app.command install definition should NOT be in app.py
    # (only a forwarding import or nothing)
    assert "def install(" not in src, (
        "install() should be defined in cli/_marketplace.py, not cli/app.py"
    )


# MK-DEP-1
def test_install_not_registered_as_cli_command():
    """autoskillit install is not a registered CLI command."""
    from autoskillit import cli

    with pytest.raises(SystemExit) as exc_info:
        with patch("sys.argv", ["autoskillit", "install"]):
            cli.main()
    assert exc_info.value.code != 0


# MK-DEP-2
def test_upgrade_not_registered_as_cli_command():
    """autoskillit upgrade is not a registered CLI command."""
    from autoskillit import cli

    with pytest.raises(SystemExit) as exc_info:
        with patch("sys.argv", ["autoskillit", "upgrade"]):
            cli.main()
    assert exc_info.value.code != 0


# MK-DEP-3
def test_marketplace_module_still_importable():
    """_marketplace module is still importable (not deleted)."""
    import autoskillit.cli._marketplace  # noqa: F401
